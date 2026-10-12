#!/usr/bin/env bash
#
# Vigia do disco do apps-noclient (204.168.249.111).
#
# Este servidor hospeda producao (Arxis, notas, Infisical) e e a maquina de
# build de todas as sessoes, num disco de 38 GB. Em 10/10/2026 ele chegou a
# 100% durante um deploy e o Redis do notas recusou gravacoes; desde a saida do
# OpenClaw ninguem olhava o disco. Esta rotina olha, limpa o que se refaz
# sozinho e avisa o Nicolas quando a limpeza nao basta.
#
# Instalado em /usr/local/bin/vigia-disco, chamado pelo vigia-disco.timer a
# cada 15 minutos. Configuracao em /etc/vigia-disco.env (600):
#
#   VIGIA_DISCO_WEBHOOK   endereco do fluxo "Infra - Aviso de disco" no n8n
#   VIGIA_DISCO_LIMPA     % a partir do qual limpa             (padrao 80)
#   VIGIA_DISCO_AVISA     % a partir do qual avisa             (padrao 90)
#   VIGIA_DISCO_PASTAS_H  horas parada para apagar pasta de build (padrao 48)
#   VIGIA_DISCO_REAVISO_H horas entre dois avisos de disco cheio  (padrao 6)
#
# O que a limpeza apaga, e so com nenhum build rodando:
#   - cache de build do Docker e imagens sem conteiner;
#   - volumes anonimos sem conteiner (banco de teste descartavel);
#   - cache do npm e de ferramentas em /root/.npm e /root/.cache;
#   - pastas de build em /opt/build e /root/build-* sem mexida ha mais de
#     VIGIA_DISCO_PASTAS_H horas e sem conteiner montando.
# Nunca toca em volume com nome, /opt/backups, /opt/<produto> nem banco.
#
# Uso: vigia-disco [--simula]   (--simula so mostra o que faria)
set -euo pipefail

[ -r /etc/vigia-disco.env ] && . /etc/vigia-disco.env

LIMPA="${VIGIA_DISCO_LIMPA:-80}"
AVISA="${VIGIA_DISCO_AVISA:-90}"
PASTAS_H="${VIGIA_DISCO_PASTAS_H:-48}"
REAVISO_H="${VIGIA_DISCO_REAVISO_H:-6}"
WEBHOOK="${VIGIA_DISCO_WEBHOOK:-}"
SERVIDOR="apps-noclient"
ESTADO=/var/lib/vigia-disco
SIMULA=0
[ "${1:-}" = "--simula" ] && SIMULA=1

mkdir -p "$ESTADO"

usado_pct() { df -P / | awk 'NR==2 { gsub(/%/, "", $5); print $5 }'; }
livre_mb() { df -P -B1M / | awk 'NR==2 { print $4 }'; }
agora() { date -u +%Y-%m-%dT%H:%M:%SZ; }
diz() { echo "$(agora) $*"; }

# Build em andamento: compilador ou empacotador rodando (no host ou dentro de
# conteiner, que aparece no `ps` do host), cliente do docker construindo, ou
# conteiner descartavel de imagem de build no ar. As ferramentas de Node
# (tsc, vite, next) aparecem com o processo `node`, por isso a busca nos
# argumentos.
build_rodando() {
  if docker ps --format '{{.Image}}' 2>/dev/null \
    | grep -qE '^(node|rust|golang|python|mcr\.microsoft\.com/playwright)[:@]'; then
    return 0
  fi
  ps -eo comm,args | awk '
    $1 ~ /^(cargo|rustc|esbuild|buildkitd|docker-buildx|next-server)$/ { n++ }
    $1 ~ /^(node|npm|npx|pnpm|yarn)$/ && $0 ~ /(tsc|vite|next|esbuild|webpack|rollup|vitest|jest|playwright| ci| install| run build)/ { n++ }
    $1 == "docker" && $0 ~ / (build|load|pull|run) / { n++ }
    END { exit n ? 0 : 1 }'
}

faz() {
  if [ "$SIMULA" = 1 ]; then diz "simula: $*"; else "$@" >/dev/null 2>&1 || diz "falhou: $*"; fi
}

# Pasta de build que algum conteiner esta montando nao sai.
montada() {
  local pasta="$1" id
  for id in $(docker ps -q 2>/dev/null); do
    docker inspect "$id" --format '{{range .Mounts}}{{println .Source}}{{end}}' 2>/dev/null \
      | grep -q "^${pasta}\(/\|$\)" && return 0
  done
  return 1
}

limpar() {
  faz docker builder prune -af
  faz docker image prune -af
  faz docker volume prune -f
  faz rm -rf /root/.npm/_cacache /root/.npm/_logs
  if [ -d /root/.cache ]; then faz find /root/.cache -mindepth 1 -maxdepth 1 -exec rm -rf {} +; fi

  local pasta
  for pasta in /opt/build/* /root/build-*; do
    [ -d "$pasta" ] || continue
    # A mexida mais recente em qualquer arquivo da pasta, ate dois niveis.
    if [ -n "$(find "$pasta" -maxdepth 2 -mmin "-$((PASTAS_H * 60))" -print -quit 2>/dev/null)" ]; then
      continue
    fi
    montada "$pasta" && continue
    diz "pasta de build parada ha mais de ${PASTAS_H} h: $pasta"
    faz rm -rf "$pasta"
  done
}

avisar() {
  local situacao="$1" liberado_gb="$2" livre_gb
  livre_gb=$(awk -v m="$(livre_mb)" 'BEGIN { printf "%.1f", m / 1024 }')
  if [ -z "$WEBHOOK" ]; then diz "sem VIGIA_DISCO_WEBHOOK: aviso '$situacao' nao enviado"; return 0; fi
  if [ "$SIMULA" = 1 ]; then diz "simula: aviso '$situacao' ($(usado_pct)%, ${livre_gb} GB livres)"; return 0; fi
  curl --fail --silent --show-error --max-time 20 -X POST "$WEBHOOK" \
    -H 'Content-Type: application/json' \
    -d "{\"servidor\":\"$SERVIDOR\",\"situacao\":\"$situacao\",\"usado_pct\":$(usado_pct),\"livre_gb\":$livre_gb,\"liberado_gb\":$liberado_gb}" \
    >/dev/null || diz "aviso '$situacao' nao chegou ao n8n"
}

pct=$(usado_pct)

if [ "$pct" -lt "$LIMPA" ]; then
  # Voltou ao normal depois de um aviso: conta uma vez e esquece.
  if [ -e "$ESTADO/avisado" ]; then
    diz "disco em ${pct}%: de volta ao normal"
    avisar normal 0
    [ "$SIMULA" = 1 ] || rm -f "$ESTADO/avisado"
  fi
  exit 0
fi

antes=$(livre_mb)
if build_rodando; then
  diz "disco em ${pct}%, mas ha build rodando: limpeza adiada"
else
  diz "disco em ${pct}%: limpando o que se refaz sozinho"
  limpar
  sleep 15 # o containerd devolve o espaco das imagens com atraso
fi
depois=$(livre_mb)
liberado_gb=$(awk -v a="$antes" -v d="$depois" 'BEGIN { v = (d - a) / 1024; if (v < 0) v = 0; printf "%.1f", v }')
pct=$(usado_pct)
diz "disco em ${pct}% depois da limpeza (${liberado_gb} GB liberados)"

if [ "$pct" -ge "$AVISA" ]; then
  # Um aviso por janela: disco cheio por horas nao pode virar uma mensagem a
  # cada 15 minutos.
  if [ -z "$(find "$ESTADO/avisado" -mmin "-$((REAVISO_H * 60))" 2>/dev/null)" ]; then
    avisar alerta "$liberado_gb"
    [ "$SIMULA" = 1 ] || touch "$ESTADO/avisado"
  fi
fi
