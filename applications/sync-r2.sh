#!/bin/bash
#
# Manda para o R2 da Cloudflare o que hoje so existe no disco deste servidor.
#
# O backup local em /opt/backups/db tem retencao de 7 dias e mora no mesmo
# disco que o dado que ele protege — o que nao e backup, e copia. Aqui ele
# sai da maquina.
#
# `copy` e nao `sync` de proposito: o expurgo de 7 dias local nao pode apagar
# o historico remoto. No R2 o arquivo fica ate a regra de ciclo de vida do
# bucket decidir o contrario.
set -uo pipefail

REMOTE="r2"
BUCKET="avilaops-backups"
LOG="/var/log/avila-r2.log"

log() { echo "$(date -Is) $*" >> "$LOG"; }

if ! rclone listremotes 2>/dev/null | grep -q "^${REMOTE}:$"; then
  log "remote '${REMOTE}' nao configurado — nada a fazer"
  exit 0
fi

ENVIADOS=0

# Dumps e filestore de todos os bancos do servidor, ERP incluso.
#
# Os "pre-migracao-*" (dump que o deploy faz antes de migracao pendente, tarefas
# 241 e 247) NAO sobem: servem para desfazer uma migracao nas horas seguintes,
# saem do disco pela rotacao local e aqui ficariam para sempre, porque este
# script so copia e o bucket nao apaga nada. O estado do banco depois da
# migracao sobe no dump diario das 3h30. A ordem dos filtros importa: a primeira
# regra que casa decide.
if [ -d /opt/backups/db ]; then
  rclone copy /opt/backups/db "${REMOTE}:${BUCKET}/db" \
    --filter "- pre-migracao-*" --filter "+ *.sql.gz" --filter "+ *.tar.gz" --filter "- *" \
    --transfers 3 --retries 3 --log-file "$LOG" --log-level INFO
  ENVIADOS=$((ENVIADOS + $(ls -1 /opt/backups/db 2>/dev/null | wc -l)))
fi

# Dumps do portal do cliente, que gravam em outro diretorio.
if [ -d /var/backups/cliente_portal ]; then
  rclone copy /var/backups/cliente_portal "${REMOTE}:${BUCKET}/cliente_portal" \
    --include "*.sql.gz" --transfers 2 --retries 3 \
    --log-file "$LOG" --log-level INFO
fi

REMOTO=$(rclone size "${REMOTE}:${BUCKET}" --json 2>/dev/null)
log "sincronizado; ${ENVIADOS} arquivo(s) na origem; remoto: ${REMOTO}"
