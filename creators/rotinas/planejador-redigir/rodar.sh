#!/usr/bin/env bash
# Rotina do servidor creators: redige os rascunhos do planejador de posts.
#
# O planejador (n8n) cria as pecas de cada guia novo com texto de modelo. Esta
# rotina chama o Claude Code para reescrever cada peca no tom do canal. Nada e
# publicado aqui: a peca continua em rascunho ate o Nicolas aprovar na tela.
#
# Antes de chamar o Claude, pergunta ao planejador se ha o que redigir; sem
# pendencia, sai sem gastar nada.
#
# Uso:  rodar.sh            (chamado pelo cron do usuario avops)
set -euo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEGREDOS="${HOME}/.agents/claude/out/planejador.env"
ESTADO="${HOME}/.local/state/rotinas"
REGISTRO="${ESTADO}/planejador-redigir.log"
A_REDIGIR="https://n8n.avilaops.com/webhook/planejador-a-redigir"

mkdir -p "${ESTADO}"

# Uma execucao por vez: a redacao de tres pacotes pode passar da meia hora.
exec 9>"${ESTADO}/planejador-redigir.trava"
flock --nonblock 9 || exit 0

agora() { date '+%Y-%m-%d %H:%M:%S'; }

# shellcheck disable=SC1090
source "${SEGREDOS}"

resposta="$(curl --silent --show-error --max-time 30 \
  --header "x-api-key: ${PLANEJADOR_CHAVE}" "${A_REDIGIR}")" || {
  echo "$(agora) planejador nao respondeu" >>"${REGISTRO}"
  exit 1
}
quantidade="$(printf '%s' "${resposta}" | sed -n 's/.*"quantidade":\([0-9][0-9]*\).*/\1/p')"
if [[ -z "${quantidade}" ]]; then
  echo "$(agora) resposta inesperada do planejador" >>"${REGISTRO}"
  exit 1
fi
[[ "${quantidade}" -eq 0 ]] && exit 0

echo "$(agora) ${quantidade} peca(s) a redigir" >>"${REGISTRO}"

# Pasta vazia: o Claude nao precisa de arquivo nenhum para esta tarefa.
trabalho="$(mktemp -d)"
trap 'rm -rf "${trabalho}"' EXIT
cd "${trabalho}"

PATH="${HOME}/.npm-global/bin:${PATH}" timeout 45m claude --print "$(cat "${AQUI}/prompt.md")" \
  --allowedTools "mcp__avilaops__planejador_rascunhos" "mcp__avilaops__planejador_redigir" "WebFetch(domain:avilaops.com)" \
  --output-format text >>"${REGISTRO}" 2>&1 || echo "$(agora) o Claude terminou com erro" >>"${REGISTRO}"

echo "$(agora) fim" >>"${REGISTRO}"
