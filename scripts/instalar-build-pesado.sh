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
# Nenhum destino e trocado antes de todos estarem prontos: primeiro o script
# confere se consegue gravar em cada pasta (direto, ou com `sudo -n`), depois
# deixa a copia nova ao lado de cada destino e so entao faz os renames. Assim
# uma falha de permissao, de sudo ou de disco nao deixa as copias divergentes.
#
# Variaveis (para teste): BUILD_PESADO_REPO; BUILD_PESADO_DESTINO, um ou mais
# caminhos separados por ':'.
# Saida: 0 instalado em todos os destinos; 64 uso (inclui BUILD_PESADO_DESTINO
# sem nenhum caminho); 65 commit fora de origin/main; 66 commit sem o script;
# 73 nao consegui gravar num destino. No 73 a mensagem diz se algum destino
# chegou a ser trocado; fora de falha no proprio rename, nenhum e.
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

lista=()
IFS=: read -r -a partes <<< "$destinos"
for destino in "${partes[@]}"; do
  [[ -n "$destino" ]] || continue
  for ja in "${lista[@]}"; do [[ "$ja" != "$destino" ]] || continue 2; done
  lista+=("$destino")
done
(( ${#lista[@]} > 0 )) \
  || { aviso "BUILD_PESADO_DESTINO sem nenhum caminho ('$destinos'). Nada foi instalado."; exit 64; }

novo=$(mktemp)
modos=()
copias=()
limpar() {
  local i
  rm -f "$novo"
  for i in "${!copias[@]}"; do
    [[ -n "${copias[i]}" ]] || continue
    if [[ "${modos[i]}" == sudo ]]; then
      sudo -n rm -f "${copias[i]}" 2>/dev/null || true
    else
      rm -f "${copias[i]}"
    fi
  done
}
trap limpar EXIT
git -C "$repo" show "$commit:scripts/build-pesado.sh" > "$novo" 2>/dev/null \
  || { aviso "o commit $commit nao tem scripts/build-pesado.sh. Nada foi instalado."; exit 66; }
printf '\n# Instalado por instalar-build-pesado.sh a partir do commit %s.\n' "$commit" >> "$novo"
bash -n "$novo"

# 1. Conferir, sem escrever nada: cada pasta (ou o ancestral que ja existe) e
# gravavel pelo usuario, ou entao `sudo -n` funciona.
for i in "${!lista[@]}"; do
  destino=${lista[i]}
  [[ ! -d "$destino" || -L "$destino" ]] \
    || { aviso "$destino e um diretorio, nao um arquivo. Nenhum destino foi trocado."; exit 73; }
  existente=$(dirname "$destino")
  while [[ ! -e "$existente" ]]; do existente=$(dirname "$existente"); done
  if [[ -w "$existente" ]]; then
    modos[i]=direto
  elif sudo -n true 2>/dev/null; then
    modos[i]=sudo
  else
    aviso "sem permissao de escrita em $existente e sem sudo sem senha, para instalar $destino. Nenhum destino foi trocado."
    exit 73
  fi
done

# 2. Deixar a copia nova ao lado de cada destino.
for i in "${!lista[@]}"; do
  pasta=$(dirname "${lista[i]}")
  if [[ "${modos[i]}" == direto ]]; then
    mkdir -p "$pasta" && copias[i]=$(mktemp "$pasta/.build-pesado.XXXXXX") \
      && cat "$novo" > "${copias[i]}" && chmod 755 "${copias[i]}" && continue
  else
    copias[i]=$pasta/.build-pesado.$$
    sudo -n install -D -m 755 -o root -g root -T "$novo" "${copias[i]}" && continue
  fi
  aviso "nao consegui preparar a copia nova em $pasta (erro acima). Nenhum destino foi trocado."
  exit 73
done

# 3. Trocar. -T: troca o proprio destino, mesmo que hoje seja um link; nunca
# escreve atraves dele.
trocados=()
for i in "${!lista[@]}"; do
  destino=${lista[i]}
  if [[ "${modos[i]}" == direto ]]; then
    mv -fT "${copias[i]}" "$destino" || falhou=1
  else
    sudo -n mv -fT "${copias[i]}" "$destino" || falhou=1
  fi
  if [[ -n "${falhou-}" ]]; then
    aviso "nao consegui trocar $destino (erro acima). Ja trocados: ${trocados[*]:-nenhum}."
    exit 73
  fi
  copias[i]=
  trocados+=("$destino")
  printf 'build-pesado instalado em %s (commit %s)\n' "$destino" "$commit"
done
