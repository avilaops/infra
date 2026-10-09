#!/usr/bin/env bash
# Instala o build-pesado em ~/.local/bin e em /usr/local/bin como COPIA de um
# commit ja enviado a main, e nao como link para a arvore de trabalho: checkout
# ou edicao ainda nao revisada no infra nao muda o que os agentes executam.
#
# Uso: instalar-build-pesado.sh [commit]      (padrao: origin/main)
#
# Os dois destinos recebem o mesmo arquivo. ~/.local/bin so entra no PATH pelo
# ~/.profile (shell de login); /usr/local/bin esta no PATH de cron, systemd e
# `env -i`, onde os scripts de deploy procuram o build-pesado e, sem acha-lo,
# constroem sem trava e sem teto (tarefa 261). Diretorio em que o usuario nao
# grava (/usr/local/bin) e escrito com `sudo -n`, dono root.
#
# O commit precisa estar contido em origin/main como este clone a conhece; o
# script nao faz fetch. A troca e atomica (arquivo novo + rename), entao um
# build-pesado em execucao segue ate o fim com a versao que ja abriu.
#
# Variaveis (para teste): BUILD_PESADO_REPO; BUILD_PESADO_DESTINO, um ou mais
# caminhos separados por ':'.
# Saida: 0 instalado; 64 uso; 65 commit fora de origin/main; 66 commit sem o
# script; 73 nao consegui gravar num destino (os anteriores ja foram trocados).
set -euo pipefail

aviso() { printf 'instalar-build-pesado: %s\n' "$*" >&2; }

(( $# <= 1 )) || { aviso 'uso: instalar-build-pesado.sh [commit]'; exit 64; }
repo=${BUILD_PESADO_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
destinos=${BUILD_PESADO_DESTINO:-$HOME/.local/bin/build-pesado:/usr/local/bin/build-pesado}
ref=${1:-origin/main}

commit=$(git -C "$repo" rev-parse --verify --quiet "$ref^{commit}") \
  || { aviso "commit desconhecido em $repo: $ref"; exit 64; }
git -C "$repo" merge-base --is-ancestor "$commit" origin/main 2>/dev/null \
  || { aviso "o commit $commit nao esta em origin/main (ainda sem push?). Nada foi instalado."; exit 65; }

novo=$(mktemp)
copia=
trap 'rm -f "$novo" ${copia:+"$copia"}' EXIT
git -C "$repo" show "$commit:scripts/build-pesado.sh" > "$novo" 2>/dev/null \
  || { aviso "o commit $commit nao tem scripts/build-pesado.sh. Nada foi instalado."; exit 66; }
printf '\n# Instalado por instalar-build-pesado.sh a partir do commit %s.\n' "$commit" >> "$novo"
bash -n "$novo"

IFS=: read -r -a lista <<< "$destinos"
for destino in "${lista[@]}"; do
  [[ -n "$destino" ]] || continue
  pasta=$(dirname "$destino")
  # -T: troca o proprio destino, mesmo que hoje seja um link; nunca escreve
  # atraves dele.
  if mkdir -p "$pasta" 2>/dev/null && [[ -w "$pasta" ]]; then
    copia=$(mktemp "$pasta/.build-pesado.XXXXXX")
    cat "$novo" > "$copia"
    chmod 755 "$copia"
    mv -fT "$copia" "$destino"
  else
    copia=
    sudo -n install -D -m 755 -o root -g root -T "$novo" "$pasta/.build-pesado.$$" \
      && sudo -n mv -fT "$pasta/.build-pesado.$$" "$destino" \
      || { sudo -n rm -f "$pasta/.build-pesado.$$" 2>/dev/null || true
           aviso "nao consegui gravar em $destino (sem permissao e sem sudo). Destino nao instalado."; exit 73; }
  fi
  copia=
  printf 'build-pesado instalado em %s (commit %s)\n' "$destino" "$commit"
done
