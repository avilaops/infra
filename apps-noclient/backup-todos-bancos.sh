#!/bin/bash
# Dump de TODOS os bancos deste servidor (apps-noclient), host e containers.
# Lista explicita, como no servidor de aplicacoes: banco novo que nao entrar
# aqui nao e copiado. Criado em 10/09/2026 na mudanca de servidor.
set -uo pipefail
DEST="/opt/backups/db"; STAMP="$(date -u +%Y%m%d)"; RETENCAO=7; ERROS=0; OK=0
mkdir -p "$DEST"
# gzip de entrada vazia grava 20 bytes, entao "[ -s ]" nunca falhava: pg_dump com
# rc=0 e sem saida passava como ok. Dump de verdade tem sempre mais que isso.
tem_dado() { [ "$(stat -c %s "$1" 2>/dev/null || echo 0)" -gt 20 ]; }
# Postgres do host: arxisvr (nunca esteve na lista do servidor antigo) e sms
# n8n, notas e ia chegaram em 11/09/2026 (tarde), do servidor de aplicacoes.
# alobarbeiro, engops e engops_dev entraram em 07/10/2026: existiam desde 11/09 e
# nunca tinham sido copiados.
# infisical (cofre de segredos) entrou em 10/10/2026: existia desde 09/10 sem copia.
for DB in arxisvr sms n8n notas ia agricola_medusa alobarbeiro engops engops_dev infisical; do
  if sudo -u postgres pg_dump "$DB" 2>/dev/null | gzip > "$DEST/host-$DB-$STAMP.sql.gz"; then
    tem_dado "$DEST/host-$DB-$STAMP.sql.gz" && OK=$((OK+1)) || { ERROS=$((ERROS+1)); rm -f "$DEST/host-$DB-$STAMP.sql.gz"; }
  else ERROS=$((ERROS+1)); rm -f "$DEST/host-$DB-$STAMP.sql.gz"; fi
done
# Postgres em container: container:usuario:banco
# Daemon do Docker fora do ar nao e "container nao existe": conta um erro e nao
# tenta as entradas (antes elas eram puladas sem contar nada).
DOCKER_OK=1
docker info >/dev/null 2>&1 || { DOCKER_OK=0; ERROS=$((ERROS+1)); }
for ENTRADA in "agenda-crm-db:agenda_crm:agenda_crm"; do
  C="${ENTRADA%%:*}"; RESTO="${ENTRADA#*:}"; U="${RESTO%%:*}"; D="${RESTO#*:}"
  # Container removido em 05/10/2026: sem ele nao ha o que copiar (antes gerava
  # arquivo de 20 bytes e erro todo dia). Container parado ainda conta como erro;
  # o .gz vazio da falha e apagado para nao subir ao R2.
  [ "$DOCKER_OK" = 1 ] || continue
  docker container inspect "$C" >/dev/null 2>&1 || continue
  if docker exec "$C" pg_dump -U "$U" "$D" 2>/dev/null | gzip > "$DEST/ct-$D-$STAMP.sql.gz"; then
    tem_dado "$DEST/ct-$D-$STAMP.sql.gz" && OK=$((OK+1)) || { ERROS=$((ERROS+1)); rm -f "$DEST/ct-$D-$STAMP.sql.gz"; }
  else ERROS=$((ERROS+1)); rm -f "$DEST/ct-$D-$STAMP.sql.gz"; fi
done
# Volumes com dado que nao esta em banco
for VOL in arxisvr_uploads agenda-crm-storage sms_smpp-dados notas-avilaops-com_outline-data ia_dados; do
  ORIGEM="/var/lib/docker/volumes/$VOL/_data"; [ -d "$ORIGEM" ] || continue
  if tar -czf "$DEST/vol-$VOL-$STAMP.tar.gz" -C "$ORIGEM" . 2>/dev/null; then OK=$((OK+1)); else ERROS=$((ERROS+1)); fi
done
# n8n guarda config e a CHAVE DE CRIPTOGRAFIA das credenciais em /opt/n8n/dados
# (bind mount, nao volume): sem ela o dump do banco e inutil.
# /opt/n8n foi apagado em 05/10/2026; a ultima copia boa esta em
# /opt/backups/retidos-20261005 e no R2. So copia se a pasta existir.
if [ -d /opt/n8n/dados ]; then
  if tar -czf "$DEST/dir-n8n-dados-$STAMP.tar.gz" -C /opt/n8n dados arquivos 2>/dev/null; then OK=$((OK+1)); else ERROS=$((ERROS+1)); fi
fi
# O Infisical cifra os segredos com a ENCRYPTION_KEY de /opt/infisical/.env:
# sem ela o dump do banco `infisical` e ilegivel. Mesmo caso da pasta do n8n.
if [ -f /opt/infisical/.env ]; then
  if (umask 077; tar -czf "$DEST/dir-infisical-env-$STAMP.tar.gz" -C /opt/infisical .env docker-compose.yml 2>/dev/null); then OK=$((OK+1)); else ERROS=$((ERROS+1)); fi
fi
find "$DEST" -type f -mtime +$RETENCAO -delete
echo "$(date -Is) ok=$OK erros=$ERROS"
