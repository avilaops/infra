#!/usr/bin/env bash
# Backup diário do OpenClaw e do banco `agentes` (sem LLM).
#
# Guarda em ~/backups/openclaw/AAAA-MM-DD/ (fora de qualquer repositório, modo 700):
#   openclaw.json        configuração do gateway (contém segredo)
#   cron.json            definição das rotinas agendadas
#   workspaces.tar.gz    ~/.openclaw/workspace (contratos, memória, roadmap)
#   agentes.dump         pg_dump -Fc do banco agentes
# Cada arquivo é conferido depois de gravado. Mantém os últimos RETENCAO_DIAS dias.
#
# Uso: backup_agentes.sh [--sem-retencao]
set -euo pipefail
umask 077

DESTINO="${BACKUP_DESTINO:-$HOME/backups/openclaw}"
RETENCAO_DIAS="${BACKUP_RETENCAO_DIAS:-7}"
OPENCLAW="${OPENCLAW_HOME:-$HOME/.openclaw}"
dia="$(date +%F)"
pasta="$DESTINO/$dia"

# Nunca gravar dentro de um repositório git.
mkdir -p "$DESTINO"
if git -C "$DESTINO" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "ERRO: $DESTINO está dentro de um repositório git" >&2
    exit 2
fi

tmp="$(mktemp -d "$DESTINO/.parcial-XXXXXX")"
trap 'rm -rf "$tmp"' EXIT

cp -p "$OPENCLAW/openclaw.json" "$tmp/openclaw.json"
python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$tmp/openclaw.json"

# A definição dos jobs vive no estado do gateway; o export é opcional (gateway pode estar fora).
if timeout 60 openclaw cron list --json >"$tmp/cron.json" 2>/dev/null \
    && python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$tmp/cron.json" 2>/dev/null; then
    :
else
    rm -f "$tmp/cron.json"
    echo "aviso: não consegui exportar as rotinas (cron.json ficou de fora)" >&2
fi

tar -C "$OPENCLAW" --exclude='node_modules' --exclude='*.sock' -czf "$tmp/workspaces.tar.gz" workspace
tar -tzf "$tmp/workspaces.tar.gz" >/dev/null

pg_dump -d agentes -Fc -f "$tmp/agentes.dump"
pg_restore -l "$tmp/agentes.dump" >/dev/null

# Troca atômica: um backup do mesmo dia substitui o anterior só depois de pronto.
rm -rf "$pasta.velho"
[ -d "$pasta" ] && mv "$pasta" "$pasta.velho"
mv "$tmp" "$pasta"
rm -rf "$pasta.velho"
trap - EXIT

removidos=0
if [ "${1:-}" != "--sem-retencao" ]; then
    # Só apaga pastas com nome de data, dentro do destino, mais velhas que a retenção.
    while IFS= read -r velha; do
        rm -rf "$velha"
        removidos=$((removidos + 1))
    done < <(find "$DESTINO" -mindepth 1 -maxdepth 1 -type d -name '20[0-9][0-9]-[0-9][0-9]-[0-9][0-9]' -mtime "+$((RETENCAO_DIAS - 1))")
fi

tamanho="$(du -sh "$pasta" | cut -f1)"
guardados="$(find "$DESTINO" -mindepth 1 -maxdepth 1 -type d -name '20*' | wc -l)"
printf '{"backup":"%s","tamanho":"%s","arquivos":"%s","guardados":%s,"removidos":%s}\n' \
    "$pasta" "$tamanho" "$(ls "$pasta" | tr '\n' ' ' | sed 's/ $//')" "$guardados" "$removidos"
