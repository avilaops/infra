#!/usr/bin/env bash
# Instala o build-pesado em ~/.local/bin como COPIA de um commit ja enviado a
# main, e nao como link para a arvore de trabalho: checkout ou edicao ainda nao
# revisada no infra nao muda o que os agentes executam.
#
# Uso: instalar-build-pesado.sh [commit]      (padrao: origin/main)
#
# O commit precisa estar contido em origin/main como este clone a conhece; o
# script nao faz fetch. A troca e atomica (arquivo novo + rename), entao um
# build-pesado em execucao segue ate o fim com a versao que ja abriu.
#
# Variaveis (para teste): BUILD_PESADO_REPO, BUILD_PESADO_DESTINO.
# Saida: 0 instalado; 64 uso; 65 commit fora de origin/main; 66 commit sem o script.
set -euo pipefail

aviso() { printf 'instalar-build-pesado: %s\n' "$*" >&2; }

(( $# <= 1 )) || { aviso 'uso: instalar-build-pesado.sh [commit]'; exit 64; }
repo=${BUILD_PESADO_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
destino=${BUILD_PESADO_DESTINO:-$HOME/.local/bin/build-pesado}
ref=${1:-origin/main}

commit=$(git -C "$repo" rev-parse --verify --quiet "$ref^{commit}") \
  || { aviso "commit desconhecido em $repo: $ref"; exit 64; }
git -C "$repo" merge-base --is-ancestor "$commit" origin/main 2>/dev/null \
  || { aviso "o commit $commit nao esta em origin/main (ainda sem push?). Nada foi instalado."; exit 65; }

mkdir -p "$(dirname "$destino")"
novo=$(mktemp "$(dirname "$destino")/.build-pesado.XXXXXX")
trap 'rm -f "$novo"' EXIT
git -C "$repo" show "$commit:scripts/build-pesado.sh" > "$novo" 2>/dev/null \
  || { aviso "o commit $commit nao tem scripts/build-pesado.sh. Nada foi instalado."; exit 66; }
printf '\n# Instalado por instalar-build-pesado.sh a partir do commit %s.\n' "$commit" >> "$novo"
bash -n "$novo"
chmod 755 "$novo"
# -T: troca o proprio destino, mesmo que hoje seja um link; nunca escreve
# atraves dele.
mv -fT "$novo" "$destino"
trap - EXIT
printf 'build-pesado instalado em %s (commit %s)\n' "$destino" "$commit"
