#!/usr/bin/env bash
# Uso pela chave SSH: sudo /usr/local/sbin/avila-deploy dominio "$SSH_ORIGINAL_COMMAND"
# Cada chave fica vinculada a um dominio no authorized_keys.
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
# Uso: dump_if_pending <URL do banco que sera migrado> <comando que sai com 0 quando nao ha migracao pendente>
dump_if_pending() {
  local db=${MIGRATE_DUMP_DB:-} dir=${MIGRATE_DUMP_DIR:-/opt/backups/db} target=${1:-} final partial size
  shift || true
  [[ -n "$db" ]] || return 0
  [[ "$db" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]] || fail 'Nome de banco para o dump invalido.'
  # O dump sai de MIGRATE_DUMP_DB (.conf) e a migracao usa a URL do .env. Se os
  # dois apontarem para bancos diferentes, sairia "dump conferido" do banco
  # errado. Confere o nome do banco na URL (esquema://usuario:senha@host:porta/banco?opcoes)
  # antes de qualquer coisa. URL que nao da para ler tambem para o deploy. A
  # mensagem nao leva nenhum pedaco da URL: ela carrega a senha.
  # So o nome e conferido: host e porta diferentes do Postgres local nao sao vistos aqui.
  if [[ "$target" == *://* ]]; then
    target=${target#*://}
    target=${target%%[?#]*}
    target=${target##*@}
    if [[ "$target" == */* ]]; then target=${target#*/}; else target=''; fi
  else
    target=''
  fi
  [[ "$target" == "$db" ]] || fail 'Banco da URL de migracao nao e o de MIGRATE_DUMP_DB; nada foi migrado nem trocado.'
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
# Roda `prisma migrate deploy` contra o banco de producao ANTES de trocar o
# codigo em execucao, usando a imagem nova ja baixada. Existe porque o
# vedashow ficou fora do ar por dois dias em 09/2026: o deploy trocava o
# container sem aplicar a migracao pendente, e a rota de produtos passou a
# falhar com "column does not exist" sem que a verificacao de saude notasse
# (ela testava so a home, que nao consulta produto nenhum). Se a migracao
# falhar aqui, a implantacao para SEM tocar no servico em producao.
# Opt-in por app via MIGRATE_ENV_FILE no .conf; app sem essa variavel pula.
run_migrations() (
  # Subshell: a limpeza continua ativa em qualquer erro, sem trocar o trap do dispatcher.
  local base=$1 migrate_env var_name schema_dir major migrate_dir db_url='' cid='' repeated=0
  [[ -n "${MIGRATE_ENV_FILE:-}" ]] || return 0
  migrate_env="$base/${MIGRATE_ENV_FILE}"
  [[ -f "$migrate_env" ]] || fail 'Arquivo de ambiente da migracao nao encontrado.'
  var_name=${MIGRATE_DB_VAR:-DATABASE_URL}
  [[ "$var_name" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]] || fail 'Nome de variavel de banco invalido.'
  schema_dir=${MIGRATE_SCHEMA_DIR:-/app/prisma}
  major=${MIGRATE_PRISMA_MAJOR:-6}
  [[ "$major" == 5 || "$major" == 6 ]] || fail 'Migracao requer Prisma 5 ou 6 com suporte a dotenv.'
  migrate_dir=$(mktemp -d)
  trap '[[ -z "$cid" ]] || docker rm "$cid" >/dev/null 2>&1 || true; rm -rf -- "$migrate_dir"' EXIT
  cid=$(docker create --network none --entrypoint /bin/true "$image")
  docker cp "$cid:$schema_dir" "$migrate_dir/prisma" >/dev/null
  docker rm "$cid" >/dev/null
  cid=''
  [[ -s "$migrate_dir/prisma/schema.prisma" ]] || fail 'Imagem sem schema de migracao.'
  # Prisma interpreta aspas e expansoes dotenv. Nunca executar o .env como shell.
  # A copia privada preserva a origem e adapta somente o alias de rede do Docker.
  sed 's/host\.docker\.internal/127.0.0.1/g' "$migrate_env" > "$migrate_dir/.env"
  rm -f "$migrate_dir/prisma/.env"
  cd "$migrate_dir"
  # So para conferir o nome do banco antes do dump; quem usa a URL e o Prisma, pelo .env.
  if [[ -n "${MIGRATE_DUMP_DB:-}" ]]; then
    # Com a variavel repetida no .env o Prisma fica com a ULTIMA linha, e o dotenv
    # dele tambem aceita "export", espacos em volta e ":" no lugar de "="
    # (conferido com prisma@5 e prisma@6 de verdade, tarefa 262). Ler a primeira
    # conferiria um banco e migraria outro. Em vez de adivinhar qual linha vale,
    # para: quem arruma e o .env, que deve ter a variavel uma vez so.
    repeated=$(grep -cE "^[[:space:]]*(export[[:space:]]+)?${var_name}[[:space:]]*[=:]" .env) || true
    (( ${repeated:-0} <= 1 )) || fail 'Variavel de banco repetida no ambiente da migracao; nada foi migrado nem trocado.'
    db_url=$(grep -m1 -E "^(export[[:space:]]+)?${var_name}=" .env | cut -d= -f2-) || db_url=''
    db_url=${db_url%$'\r'}
    db_url=${db_url%\"}; db_url=${db_url#\"}; db_url=${db_url%\'}; db_url=${db_url#\'}
  fi
  dump_if_pending "$db_url" env -u "$var_name" npx -y "prisma@${major}" migrate status --schema prisma/schema.prisma
  printf '==> aplicando migracoes de %s\n' "$application" >&2
  if ! env -u "$var_name" npx -y "prisma@${major}" migrate deploy --schema prisma/schema.prisma >&2; then
    fail 'Migracao do banco falhou; deploy interrompido antes de trocar o servico.'
  fi
)

# Validar o destino antes de permitir qualquer migracao no banco.
preflight() (
  case "$DEPLOY_MODE" in
    static)
      : "${WEB_ROOT:?}"
      [[ "$WEB_ROOT" == /var/www/* && "$WEB_ROOT" != *..* && -d "$WEB_ROOT" ]] || fail 'Diretorio do site nao instalado.'
      [[ -x /usr/local/lib/avilaops/deploy-static ]] || fail 'Publicador estatico nao instalado.'
      [[ -z "${MIGRATE_ENV_FILE:-}" ]] || fail 'Deploy estatico nao admite migracao de banco.'
      ;;
    systemd)
      : "${APP_ROOT:?}" "${SYSTEMD_UNITS:?}" "${ENTRYPOINT:?}" "${HEALTH_URL:?}"
      [[ "$APP_ROOT" == /opt/* && "$APP_ROOT" != *..* && -d "$APP_ROOT" ]] || fail 'Aplicacao systemd nao instalada.'
      [[ -x /usr/local/lib/avilaops/deploy-systemd ]] || fail 'Publicador systemd nao instalado.'
      read -r -a units <<< "$SYSTEMD_UNITS"
      for unit in "${units[@]}"; do
        [[ "$unit" =~ ^[a-zA-Z0-9@_.-]+\.service$ ]] || fail 'Unidade systemd invalida.'
        systemctl cat "$unit" >/dev/null || fail 'Unidade systemd nao instalada.'
      done
      ;;
    container)
      : "${PROJECT_DIR:?}" "${COMPOSE_FILE:?}" "${COMPOSE_PROJECT:?}" "${SERVICE:?}" "${CONTAINER:?}"
      if [[ "${USE_CONTAINER_HEALTH:-false}" != true ]]; then : "${HEALTH_URL:?}"; fi
      [[ "$SERVICE" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ ]] || fail 'Nome de servico invalido.'
      [[ -d "$PROJECT_DIR" && -f "$COMPOSE_FILE" ]] || fail 'Projeto nao instalado neste servidor.'
      compose_preflight=(docker compose --project-directory "$PROJECT_DIR" -p "$COMPOSE_PROJECT" -f "$COMPOSE_FILE")
      if [[ -n "${RUNTIME_OVERLAY:-}" ]]; then
        [[ -f "$RUNTIME_OVERLAY" ]] || fail 'Configuracao complementar ausente.'
        compose_preflight+=(-f "$RUNTIME_OVERLAY")
      fi
      "${compose_preflight[@]}" config --quiet
      # Lista inteira antes de procurar: com "| grep -q" e pipefail, o grep sai no
      # primeiro acerto e o compose, se ainda estiver escrevendo, morre de SIGPIPE.
      services=$("${compose_preflight[@]}" config --services)
      grep -Fxq -- "$SERVICE" <<< "$services" || fail 'Servico ausente no Compose.'
      previous=$(docker inspect --format '{{.Image}}' "$CONTAINER")
      [[ "$previous" =~ ^sha256:[a-f0-9]{64}$ ]] || fail 'Container atual nao encontrado.'
      ;;
    *) fail 'Modo de deploy desconhecido.' ;;
  esac
)
# Em 03/10/2026 o disco do servidor encheu de imagens antigas: o pull de
# lojas e da calculadora da CIFRA morreu no meio e o saudepet-backend nao
# conseguiu nem recriar o container nem voltar para a imagem anterior.
# Conferir o espaco ANTES de baixar ou migrar qualquer coisa, para falhar
# com o servico ainda intacto.
free_mb() { df -Pm -- "$1" | awk 'NR == 2 { print $4 }'; }
low_space() {
  local dir
  for dir in /var/lib/containerd "$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)"; do
    [[ -n "$dir" && -d "$dir" ]] || continue
    (( $(free_mb "$dir") >= ${MIN_FREE_MB:-3072} )) || { printf '%s\n' "$dir"; return 0; }
  done
  return 1
}
ensure_space() {
  local dir
  dir=$(low_space) || return 0
  printf '==> pouco espaco em %s; removendo imagens sem tag e cache de build\n' "$dir" >&2
  docker image prune --force >/dev/null || true
  docker builder prune --force >/dev/null 2>&1 || true
  if dir=$(low_space); then
    fail "Espaco insuficiente em $dir (minimo ${MIN_FREE_MB:-3072} MB). Intervencao necessaria; servico nao foi alterado."
  fi
}
# Cada deploy deixa uma imagem nova por digest. Depois do sucesso, manter
# deste app so a imagem em execucao e a anterior (alvo do proximo rollback).
prune_old_images() {
  local id
  docker image ls --no-trunc --format '{{.ID}}' "$IMAGE_REPOSITORY" | sort -u | while read -r id; do
    [[ -n "$id" && "$id" != "$expected_image" && "$id" != "$previous_image" ]] || continue
    # Sem a entrada do laco: quem le dela come o resto da lista.
    docker image rm "$id" </dev/null >/dev/null 2>&1 || true
  done
}
[[ $EUID == 0 ]] || fail 'O dispatcher precisa executar como root.'
[[ $# == 2 ]] || fail 'Comando de deploy invalido.'
application=$1
[[ "$application" =~ ^[a-z0-9][a-z0-9.-]*$ ]] || fail 'Aplicacao invalida.'
read -r verb requested_application image extra <<< "$2"
[[ "$2" == "deploy $application $image" && "$verb" == deploy && "$requested_application" == "$application" && -z "$extra" ]] || fail 'Chave sem acesso a esta aplicacao.'
[[ "$image" =~ ^ghcr\.io/[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9._/-]*@sha256:[a-f0-9]{64}$ ]] || fail 'A imagem precisa ter um digest SHA256.'

config="/etc/avilaops/deploy/$application.conf"
[[ -f "$config" && ! -L "$config" ]] || fail 'Aplicacao sem configuracao de deploy.'
[[ $(stat -c %u "$config") == 0 ]] || fail 'Configuracao precisa pertencer a root.'
config_mode=$(stat -c %a "$config")
(( (8#$config_mode & 022) == 0 )) || fail 'Configuracao gravavel por outros usuarios.'
# Arquivo instalado pelo administrador, nunca fornecido pelo comando remoto.
# shellcheck source=/dev/null
source "$config"
: "${IMAGE_REPOSITORY:?}"
DEPLOY_MODE=${DEPLOY_MODE:-container}
[[ "$image" == "$IMAGE_REPOSITORY"@sha256:* ]] || fail 'Repositorio de imagem nao autorizado.'
state_dir="/var/lib/avilaops/deploy/$application"
install -d -m 700 "$state_dir"
exec 9>"$state_dir/lock"
flock -w 600 9 || fail 'Outro deploy continua em andamento.'
# A entrada padrao traz o token do GHCR: nada antes do read pode ler dela.
preflight </dev/null
ensure_space </dev/null
# Token temporario do proprio job, recebido pelo canal SSH e descartado apos o pull.
IFS= read -r registry_token || fail 'Token temporario do GHCR ausente.'
[[ -n "$registry_token" ]] || fail 'Token temporario do GHCR vazio.'
registry_config=$(mktemp -d)
trap 'rm -rf "$registry_config"' EXIT
printf '%s\n' "$registry_token" | docker --config "$registry_config" login ghcr.io --username avilaops --password-stdin >/dev/null 2>&1
unset registry_token
docker --config "$registry_config" pull "$image"
rm -rf "$registry_config"
trap - EXIT
run_migrations "${PROJECT_DIR:-${APP_ROOT:-.}}"
if [[ "$DEPLOY_MODE" == static ]]; then
  : "${WEB_ROOT:?}"
  exec /usr/local/lib/avilaops/deploy-static "$application" "$image" "$WEB_ROOT"
fi
if [[ "$DEPLOY_MODE" == systemd ]]; then
  exec /usr/local/lib/avilaops/deploy-systemd "$application" "$image"
fi
[[ "$DEPLOY_MODE" == container ]] || fail 'Modo de deploy desconhecido.'
: "${PROJECT_DIR:?}" "${COMPOSE_FILE:?}" "${COMPOSE_PROJECT:?}" "${SERVICE:?}" "${CONTAINER:?}"
if [[ "${USE_CONTAINER_HEALTH:-false}" != true ]]; then : "${HEALTH_URL:?}"; fi
[[ "$SERVICE" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ ]] || fail 'Nome de servico invalido.'
[[ -d "$PROJECT_DIR" && -f "$COMPOSE_FILE" ]] || fail 'Projeto nao instalado neste servidor.'

override="$state_dir/image.yml"
compose=(docker compose --project-directory "$PROJECT_DIR" -p "$COMPOSE_PROJECT" -f "$COMPOSE_FILE")
if [[ -n "${RUNTIME_OVERLAY:-}" ]]; then
  [[ -f "$RUNTIME_OVERLAY" ]] || fail 'Configuracao complementar ausente.'
  compose+=(-f "$RUNTIME_OVERLAY")
fi
compose+=(-f "$override")
previous_image=$(docker inspect --format '{{.Image}}' "$CONTAINER")
[[ "$previous_image" =~ ^sha256:[a-f0-9]{64}$ ]] || fail 'Container atual nao encontrado.'

rollback_compose=("${compose[@]}")
# A primeira migracao pode substituir codigo montado por codigo dentro da imagem.
if [[ -n "${LEGACY_IMAGE_ID:-}" && "$previous_image" == "$LEGACY_IMAGE_ID" ]]; then
  rollback_compose=(docker compose --project-directory "$PROJECT_DIR" -p "$COMPOSE_PROJECT" -f "$COMPOSE_FILE" -f "$override")
fi

write_override() {
  printf 'services:\n  %s:\n    image: %s\n' "$SERVICE" "$1" > "$override.next"
  mv "$override.next" "$override"
}
healthy() {
  local code
  for (( attempt=0; attempt<${HEALTH_ATTEMPTS:-30}; attempt++ )); do
    if [[ $(docker inspect --format '{{.State.Running}}' "$CONTAINER" 2>/dev/null) == true ]]; then
      if [[ -n "${CONTAINER_HEALTH_URL:-}" ]]; then
        docker exec "$CONTAINER" node -e 'fetch(process.argv[1], {signal: AbortSignal.timeout(5000)}).then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))' "$CONTAINER_HEALTH_URL" >/dev/null 2>&1 && return 0
        sleep 3
        continue
      fi
      if [[ "${USE_CONTAINER_HEALTH:-false}" == true ]]; then
        [[ $(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$CONTAINER") == healthy ]] && return 0
        sleep 3
        continue
      fi
      code=$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 5 "$HEALTH_URL" || true)
      [[ "$code" =~ ^2[0-9]{2}$ ]] && return 0
    fi
    sleep 3
  done
  return 1
}
rollback() {
  local status=$?
  trap - EXIT INT TERM
  printf 'Falha no deploy. Restaurando a imagem anterior.\n' >&2
  write_override "$previous_image"
  if "${rollback_compose[@]}" up -d --no-deps --no-build --pull never "$SERVICE" && healthy; then
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
"${compose[@]}" config --quiet
"${compose[@]}" up -d --no-deps --no-build --pull never "$SERVICE"
healthy || fail 'O servico nao passou na verificacao de saude.'
expected_image=$(docker image inspect --format '{{.Id}}' "$image")
[[ $(docker inspect --format '{{.Image}}' "$CONTAINER") == "$expected_image" ]] || fail 'O container nao executa a imagem solicitada.'
trap - EXIT INT TERM
prune_old_images || true
printf 'Deploy concluido: %s %s\n' "$application" "$image"
