#!/usr/bin/env bash
# Deploy de container a partir de um arquivo de imagem, sem passar pelo GHCR.
# Instalado como /usr/local/sbin/avila-deploy-local, no servidor de destino.
#
# Uso (como root): avila-deploy-local <aplicacao> <arquivo.tar.gz> <sha256 do arquivo>
#
# E o par de scripts/build-noclient.sh, para quando o GitHub Actions nao pode
# construir nem publicar. Le a MESMA configuracao do avila-deploy
# (/etc/avilaops/deploy/<aplicacao>.conf) e faz a mesma troca: migracao antes,
# override da imagem, verificacao de saude e volta para a imagem anterior se o
# servico nao responder. O que muda e a origem da imagem — um arquivo conferido
# por checksum, no lugar de um pull por digest.
#
# As funcoes dump_if_pending, run_migrations e healthy sao copia das de deploy-container.sh.
# Mudou la, muda aqui.
set -euo pipefail
umask 077

fail() { printf '%s\n' "$*" >&2; exit 1; }

# Dump do banco ANTES de aplicar migracao pendente.
#
# A migracao e o unico passo do deploy que a imagem anterior nao desfaz: o
# rollback troca o container de volta, mas a tabela alterada continua alterada.
# O dump diario das 3h30 (backup-todos-bancos) deixa ate um dia de pedidos de
# fora. Este fica colado na migracao e so existe quando ha o que migrar: deploy
# sem migracao pendente nao gera arquivo.
#
# Opt-in por app via MIGRATE_DUMP_DB no .conf (nome do banco no Postgres do
# host). O arquivo vai para MIGRATE_DUMP_DIR (/opt/backups/db), com nome que o
# rotacionador ja entende: fica o mais recente da familia, os outros 7 dias.
# Se o dump falhar ou nao passar na conferencia, o deploy para ANTES de migrar.
#
# Uso: dump_if_pending <comando que sai com 0 quando nao ha migracao pendente>
dump_if_pending() {
  local db=${MIGRATE_DUMP_DB:-} dir=${MIGRATE_DUMP_DIR:-/opt/backups/db} final partial size
  [[ -n "$db" ]] || return 0
  [[ "$db" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]] || fail 'Nome de banco para o dump invalido.'
  [[ -d "$dir" ]] || fail 'Diretorio de dumps ausente.'
  # `prisma migrate status` sai com 0 so quando o banco esta em dia. Banco fora
  # do ar tambem sai diferente de 0, e ai o dump falha logo abaixo: para.
  if "$@" </dev/null >/dev/null 2>&1; then
    printf '==> sem migracao pendente; dump dispensado\n' >&2
    return 0
  fi
  final="$dir/pre-migracao-${application}-$(date -u +%Y%m%d-%H%M%S).sql.gz"
  partial="$dir/.${final##*/}.parcial"
  # Sobra de deploy morto no meio do dump: o nome comeca com ponto, o
  # rotacionador nao ve e ficaria no disco para sempre. A trava do deploy e por
  # aplicacao, entao nenhum outro dump desta aplicacao esta em andamento.
  rm -f -- "$dir/.pre-migracao-${application}-"*.parcial
  printf '==> migracao pendente: dump de %s antes de aplicar\n' "$db" >&2
  if ! runuser -u postgres -- pg_dump --no-owner -- "$db" | gzip > "$partial"; then
    rm -f -- "$partial"
    fail 'Dump antes da migracao falhou; nada foi migrado nem trocado.'
  fi
  # Mesma conferencia do backup diario: gzip integro, com conteudo e com o
  # marcador de fim do pg_dump. Dump truncado nao pode passar por backup.
  # Os "||" abaixo: o deploy-container-local chama esta funcao dentro de
  # "if !", onde o "set -e" nao vale e um comando que falha passaria batido.
  size=$(stat -c%s -- "$partial") || size=0
  if (( size <= 20 )) || ! gzip -t -- "$partial" 2>/dev/null \
     || ! zcat -- "$partial" | tail -n 20 | grep -q 'PostgreSQL database dump complete'; then
    rm -f -- "$partial"
    fail 'Dump antes da migracao saiu incompleto; nada foi migrado nem trocado.'
  fi
  if ! mv -- "$partial" "$final"; then
    rm -f -- "$partial"
    fail 'Dump antes da migracao nao chegou ao nome final; nada foi migrado nem trocado.'
  fi
  printf '==> dump conferido: %s (%s bytes)\n' "$final" "$size" >&2
}
run_migrations() {
  local base=$1 migrate_env var_name db_url schema_dir major migrate_dir cid
  [[ -n "${MIGRATE_ENV_FILE:-}" ]] || return 0
  migrate_env="$base/${MIGRATE_ENV_FILE}"
  [[ -f "$migrate_env" ]] || fail 'Arquivo de ambiente da migracao nao encontrado.'
  var_name=${MIGRATE_DB_VAR:-DATABASE_URL}
  db_url=$(grep -m1 "^${var_name}=" "$migrate_env" | cut -d= -f2-)
  [[ -n "$db_url" ]] || fail 'Variavel de banco ausente no ambiente da migracao.'
  # O .env pode trazer o valor entre aspas (o do auth traz); o Prisma recusa a
  # URL com as aspas dentro.
  db_url=${db_url%\"}; db_url=${db_url#\"}; db_url=${db_url%\'}; db_url=${db_url#\'}
  db_url=${db_url//host.docker.internal/127.0.0.1}
  schema_dir=${MIGRATE_SCHEMA_DIR:-/app/prisma}
  major=${MIGRATE_PRISMA_MAJOR:-6}
  migrate_dir=$(mktemp -d)
  cid=$(docker create "$image")
  docker cp "$cid:$schema_dir" "$migrate_dir/prisma" >/dev/null
  docker rm "$cid" >/dev/null
  if ! (dump_if_pending env "${var_name}=${db_url}" npx -y "prisma@${major}" migrate status --schema "$migrate_dir/prisma/schema.prisma"); then
    rm -rf "$migrate_dir"
    exit 1
  fi
  printf '==> aplicando migracoes de %s\n' "$application" >&2
  if ! env "${var_name}=${db_url}" npx -y "prisma@${major}" migrate deploy --schema "$migrate_dir/prisma/schema.prisma" </dev/null >&2; then
    rm -rf "$migrate_dir"
    fail 'Migracao do banco falhou; deploy interrompido antes de trocar o servico.'
  fi
  rm -rf "$migrate_dir"
}

healthy() {
  local code
  for (( attempt=0; attempt<${HEALTH_ATTEMPTS:-30}; attempt++ )); do
    if [[ $(docker inspect --format '{{.State.Running}}' "$CONTAINER" 2>/dev/null) == true ]]; then
      if [[ -n "${CONTAINER_HEALTH_URL:-}" ]]; then
        docker exec "$CONTAINER" node -e 'fetch(process.argv[1], {signal: AbortSignal.timeout(5000)}).then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))' "$CONTAINER_HEALTH_URL" </dev/null >/dev/null 2>&1 && return 0
        sleep 3
        continue
      fi
      if [[ "${USE_CONTAINER_HEALTH:-false}" == true ]]; then
        [[ $(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$CONTAINER") == healthy ]] && return 0
        sleep 3
        continue
      fi
      code=$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 5 "$HEALTH_URL" </dev/null || true)
      [[ "$code" =~ ^2[0-9]{2}$ ]] && return 0
    fi
    sleep 3
  done
  return 1
}

[[ $EUID == 0 ]] || fail 'O deploy precisa executar como root.'
[[ $# == 3 ]] || fail 'Uso: avila-deploy-local <aplicacao> <arquivo.tar.gz> <sha256>'
application=$1
artifact=$2
expected_sum=$3
[[ "$application" =~ ^[a-z0-9][a-z0-9.-]*$ ]] || fail 'Aplicacao invalida.'
[[ "$expected_sum" =~ ^[a-f0-9]{64}$ ]] || fail 'Checksum invalido.'
[[ -f "$artifact" && ! -L "$artifact" ]] || fail 'Arquivo de imagem nao encontrado.'

config="/etc/avilaops/deploy/$application.conf"
[[ -f "$config" && ! -L "$config" ]] || fail 'Aplicacao sem configuracao de deploy.'
[[ $(stat -c %u "$config") == 0 ]] || fail 'Configuracao precisa pertencer a root.'
config_mode=$(stat -c %a "$config")
(( (8#$config_mode & 022) == 0 )) || fail 'Configuracao gravavel por outros usuarios.'
# shellcheck source=/dev/null
source "$config"
: "${IMAGE_REPOSITORY:?}"
[[ "${DEPLOY_MODE:-container}" == container ]] || fail 'Este script so publica aplicacoes em container.'
: "${PROJECT_DIR:?}" "${COMPOSE_FILE:?}" "${COMPOSE_PROJECT:?}" "${SERVICE:?}" "${CONTAINER:?}"
if [[ "${USE_CONTAINER_HEALTH:-false}" != true && -z "${CONTAINER_HEALTH_URL:-}" ]]; then : "${HEALTH_URL:?}"; fi
[[ "$SERVICE" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ ]] || fail 'Nome de servico invalido.'
[[ -d "$PROJECT_DIR" && -f "$COMPOSE_FILE" ]] || fail 'Projeto nao instalado neste servidor.'

state_dir="/var/lib/avilaops/deploy/$application"
install -d -m 700 "$state_dir"
exec 9>"$state_dir/lock"
flock -w 600 9 || fail 'Outro deploy continua em andamento.'

# O arquivo e conferido antes de carregar: e a unica prova de que o que chegou
# e o que o build produziu.
actual_sum=$(sha256sum "$artifact" | cut -d' ' -f1)
[[ "$actual_sum" == "$expected_sum" ]] || fail 'Checksum do arquivo nao confere.'
loaded=$(gunzip -c "$artifact" | docker load | sed -n 's/^Loaded image: //p' | head -1)
[[ -n "$loaded" ]] || fail 'O arquivo nao continha uma imagem.'
# Mesma regra do avila-deploy: a imagem tem de ser do repositorio desta aplicacao.
[[ "$loaded" =~ ^"$IMAGE_REPOSITORY":sha-[a-f0-9]{40}$ ]] || fail "Imagem de outro repositorio ou sem tag de commit: $loaded"
image=$loaded
# O arquivo ja cumpriu o papel; nao fica copia de build no servidor.
rm -f "$artifact"

run_migrations "$PROJECT_DIR"

override="$state_dir/image.yml"
compose=(docker compose --project-directory "$PROJECT_DIR" -p "$COMPOSE_PROJECT" -f "$COMPOSE_FILE")
if [[ -n "${RUNTIME_OVERLAY:-}" ]]; then
  [[ -f "$RUNTIME_OVERLAY" ]] || fail 'Configuracao complementar ausente.'
  compose+=(-f "$RUNTIME_OVERLAY")
fi
compose+=(-f "$override")
previous_image=$(docker inspect --format '{{.Image}}' "$CONTAINER")
[[ "$previous_image" =~ ^sha256:[a-f0-9]{64}$ ]] || fail 'Container atual nao encontrado.'

write_override() {
  printf 'services:\n  %s:\n    image: %s\n' "$SERVICE" "$1" > "$override.next"
  mv "$override.next" "$override"
}
rollback() {
  local status=$?
  trap - EXIT INT TERM
  printf 'Falha no deploy. Restaurando a imagem anterior.\n' >&2
  write_override "$previous_image"
  if "${compose[@]}" up -d --no-deps --no-build --pull never "$SERVICE" </dev/null && healthy; then
    printf 'Imagem anterior restaurada.\n' >&2
  else
    printf 'Falha ao restaurar o servico. Intervencao necessaria.\n' >&2
  fi
  (( status != 0 )) || status=1
  exit "$status"
}

write_override "$image"
trap rollback EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
"${compose[@]}" config --quiet </dev/null
"${compose[@]}" up -d --no-deps --no-build --pull never "$SERVICE" </dev/null
healthy || fail 'O servico nao passou na verificacao de saude.'
expected_image=$(docker image inspect --format '{{.Id}}' "$image")
[[ $(docker inspect --format '{{.Image}}' "$CONTAINER") == "$expected_image" ]] || fail 'O container nao executa a imagem solicitada.'
trap - EXIT INT TERM
printf 'Deploy concluido: %s %s\n' "$application" "$image"
