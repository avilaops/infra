#!/usr/bin/env bash
# Publica as rotinas a partir da `main` do GitHub (sem LLM).
#
# Os jobs do OpenClaw não rodam da árvore de trabalho do repositório: rodam de
#   ~/.local/share/rotinas-openclaw/atual/rotinas-openclaw
# `atual` é um link para releases/<commit>, e só este script o move. Ele busca a `main`
# direto do GitHub para um repositório próprio (repo.git), extrai a pasta
# rotinas-openclaw daquele commit, confere a sintaxe, roda os testes das rotinas
# (tests/test_rotinas_openclaw.py do mesmo commit) e troca o link de uma vez. Commit
# local ou edição sem commit em ~/projetos/infra não chega aos jobs.
#
# Qualquer falha (rede, GitHub, commit sem a pasta, erro de sintaxe, teste que não
# passa) sai com código diferente de zero ANTES de mexer no link: os jobs seguem na
# versão anterior. A versão publicada fica sem permissão de escrita.
#
# No fim de cada rodada chama o suite_da_main.py da versão publicada, que uma vez por
# dia roda a suíte inteira daquele commit e deixa o resultado para o vigia. A conferência
# só usa o tempo que sobra do job (ROTINAS_JOB_LIMITE_S): sem folga, fica para a rodada
# seguinte.
#
# Uso: publica_rotinas.sh
set -euo pipefail
umask 022

BASE="${ROTINAS_PUBLICADO:-$HOME/.local/share/rotinas-openclaw}"
ORIGEM="${ROTINAS_ORIGEM:-git@github.com:avilaops/infra.git}"
RAMO="${ROTINAS_RAMO:-main}"
PASTA="rotinas-openclaw"
TESTES="tests/test_rotinas_openclaw.py"
GUARDAR="${ROTINAS_GUARDAR:-3}"
# Limite do job no OpenClaw; a conferência da suíte tem de acabar antes dele.
JOB_LIMITE_S="${ROTINAS_JOB_LIMITE_S:-120}"
FOLGA_S=10  # para o resumo sair depois da conferência
CONFERE_MIN_S="${ROTINAS_CONFERE_MIN_S:-30}"  # com menos do que isso sobrando, nem começa

mkdir -p "$BASE/releases"
exec 9>"$BASE/.trava"
if ! flock -n 9; then
    echo "ERRO: outra publicação em andamento" >&2
    exit 3
fi

# Nada daqui grava __pycache__ (nem a conferência, nem os testes).
export PYTHONDONTWRITEBYTECODE=1

# As versões publicadas ficam sem escrita: devolve a do dono antes de apagar.
apaga() {
    local alvo
    for alvo in "$@"; do
        [ -e "$alvo" ] || [ -L "$alvo" ] || continue
        [ -L "$alvo" ] || chmod -R u+w "$alvo"
        rm -rf "$alvo"
    done
}

# Sobra de uma execução interrompida (só existe uma por vez, por causa da trava).
apaga "$BASE/releases"/.parcial-* "$BASE"/.atual.novo.*

repo="$BASE/repo.git"
[ -d "$repo" ] || git init --quiet --bare "$repo"
GIT_TERMINAL_PROMPT=0 timeout 60 git -C "$repo" fetch --quiet --no-tags "$ORIGEM" \
    "+refs/heads/$RAMO:refs/heads/$RAMO"
commit="$(git -C "$repo" rev-parse --verify --quiet "refs/heads/$RAMO^{commit}")"

# Suíte inteira do commit publicado, uma vez por dia (suite_da_main.py decide se é hora
# e guarda o resultado para o vigia). Nunca muda a saída nem o resumo desta rodada: tem
# teto de tempo (o que sobra do job, contado por $SECONDS), e interrompida no teto ela
# apaga a pasta extraída, não grava nada e a rodada seguinte tenta de novo.
confere_suite() {
    local conferidor="$BASE/releases/$commit/$PASTA/suite_da_main.py"
    local teto=$((JOB_LIMITE_S - SECONDS - FOLGA_S))
    [ -f "$conferidor" ] || return 0
    [ "$teto" -ge "$CONFERE_MIN_S" ] || return 0
    ROTINAS_PUBLICADO="$BASE" timeout -k 5 "$teto" python3 "$conferidor" >/dev/null 2>&1 || true
}

anterior="$(readlink "$BASE/atual" 2>/dev/null || true)"
anterior="${anterior#releases/}"
if [ "$anterior" = "$commit" ] && [ -d "$BASE/releases/$commit/$PASTA" ]; then
    confere_suite
    printf '{"publicado":false,"commit":"%s"}\n' "$commit"
    exit 0
fi

if [ ! -d "$BASE/releases/$commit/$PASTA" ]; then
    tmp="$(mktemp -d "$BASE/releases/.parcial-XXXXXX")"
    trap 'apaga "$tmp"' EXIT
    timeout 10 git -C "$repo" archive "$commit" "$PASTA" "$TESTES" | timeout 10 tar -x -C "$tmp"

    # Confere a sintaxe sem executar nada e sem gravar __pycache__.
    for arquivo in "$tmp/$PASTA"/*.py; do
        [ -e "$arquivo" ] || continue
        python3 -c 'import sys; compile(open(sys.argv[1], encoding="utf-8").read(), sys.argv[1], "exec")' "$arquivo"
    done
    for arquivo in "$tmp/$PASTA"/*.sh; do
        [ -e "$arquivo" ] || continue
        bash -n "$arquivo"
    done

    # Testes das rotinas daquele commit, contra a cópia extraída (não a árvore local).
    if ! saida="$(cd "$tmp" && timeout 60 python3 -m unittest "$TESTES" 2>&1)"; then
        printf '%s\n' "$saida" | tail -n 40 >&2
        echo "ERRO: $TESTES do commit $commit não passou; link mantido" >&2
        exit 4
    fi

    apaga "$BASE/releases/$commit"
    # Tira a escrita ANTES de dar o nome final: interrompido aqui, sobra só um .parcial-*
    # (apagado na próxima rodada), nunca uma versão com escrita, que a rodada seguinte
    # aceitaria como pronta. Renomear na mesma pasta não exige escrita na pasta movida.
    chmod 755 "$tmp"
    chmod -R a-w "$tmp"
    mv "$tmp" "$BASE/releases/$commit"
    trap - EXIT
fi

# Troca atômica do link: quem já está rodando termina na versão em que começou.
ln -s "releases/$commit" "$BASE/.atual.novo.$$"
mv -T "$BASE/.atual.novo.$$" "$BASE/atual"

# Guarda as últimas versões (a atual e a anterior nunca saem).
removidos=0
while IFS= read -r velha; do
    nome="$(basename "$velha")"
    [ "$nome" = "$commit" ] || [ "$nome" = "$anterior" ] && continue
    apaga "$velha"
    removidos=$((removidos + 1))
done < <(find "$BASE/releases" -mindepth 1 -maxdepth 1 -type d ! -name '.*' -printf '%T@ %p\n' \
    | sort -rn | tail -n "+$((GUARDAR + 1))" | cut -d' ' -f2-)

confere_suite
printf '{"publicado":true,"commit":"%s","anterior":"%s","removidos":%s}\n' \
    "$commit" "$anterior" "$removidos"
