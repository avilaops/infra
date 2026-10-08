#!/usr/bin/env bash
# Audita os containers contra a Norma de Plataforma AvilaOps.
#
# Ver NORMA-PLATAFORMA.md. Somente leitura — nao altera nada.
#
#   ./norma-check.sh            relatorio
#   ./norma-check.sh --strict   sai != 0 se houver violacao (uso em CI)
set -uo pipefail

STRICT=0
[ "${1:-}" = "--strict" ] && STRICT=1

VIOLACOES=0
CONFORMES=0

# O MTA precisa de portas baixas na interface publica por definicao do protocolo,
# e o Caddy e quem publica 80/443. Os dois sao excecao prevista na norma.
EXCECAO_PORTA="caddy|avila-mail|mta"

vermelho() { printf '\033[31m%s\033[0m\n' "$*"; }
verde()    { printf '\033[32m%s\033[0m\n' "$*"; }
amarelo()  { printf '\033[33m%s\033[0m\n' "$*"; }

falha() {
  vermelho "    ✗ $1"
  VIOLACOES=$((VIOLACOES + 1))
}

echo "═══════════════════════════════════════════════════════════"
echo " Norma de Plataforma AvilaOps — verificacao"
echo " $(date -Is)"
echo "═══════════════════════════════════════════════════════════"
echo

for C in $(docker ps --format '{{.Names}}' | sort); do
  ERROS_ANTES=$VIOLACOES

  DOMINIO=$(docker inspect "$C" --format '{{index .Config.Labels "avilaops.domain"}}' 2>/dev/null)
  TIER=$(docker inspect "$C"    --format '{{index .Config.Labels "avilaops.tier"}}'   2>/dev/null)
  BACKUP=$(docker inspect "$C"  --format '{{index .Config.Labels "avilaops.backup"}}' 2>/dev/null)
  STACK=$(docker inspect "$C"   --format '{{index .Config.Labels "avilaops.stack"}}'  2>/dev/null)
  OWNER=$(docker inspect "$C"   --format '{{index .Config.Labels "avilaops.owner"}}'  2>/dev/null)

  echo "── $C"

  # 1. Labels obrigatorios
  [ -z "$DOMINIO" ] && falha "sem label avilaops.domain"
  [ -z "$TIER"    ] && falha "sem label avilaops.tier"
  [ -z "$STACK"   ] && falha "sem label avilaops.stack"
  [ -z "$OWNER"   ] && falha "sem label avilaops.owner"

  # 2. Portas publicadas no host — so o Caddy e o MTA podem
  PORTAS=$(docker inspect "$C" --format '{{range $p, $conf := .NetworkSettings.Ports}}{{if $conf}}{{$p}} {{end}}{{end}}' 2>/dev/null)
  if [ -n "$PORTAS" ] && ! echo "$C" | grep -qiE "$EXCECAO_PORTA"; then
    falha "publica porta no host ($PORTAS) — deveria usar expose + rede edge"
  fi

  # 3. Banco de dados nao pode estar na rede edge
  REDES=$(docker inspect "$C" --format '{{range $n, $v := .NetworkSettings.Networks}}{{$n}} {{end}}' 2>/dev/null)
  if echo "$C" | grep -qiE "\-db$|postgres|mysql|mariadb|redis"; then
    if echo "$REDES" | grep -qw "edge"; then
      falha "container de dados exposto na rede edge"
    fi
    [ -z "$BACKUP" ] && falha "container de dados sem label avilaops.backup"
  fi

  # 4. Healthcheck
  HC=$(docker inspect "$C" --format '{{if .Config.Healthcheck}}sim{{else}}nao{{end}}' 2>/dev/null)
  [ "$HC" = "nao" ] && falha "sem healthcheck"

  # 5. Politica de restart
  RESTART=$(docker inspect "$C" --format '{{.HostConfig.RestartPolicy.Name}}' 2>/dev/null)
  case "$RESTART" in
    unless-stopped|always) ;;
    *) falha "restart='$RESTART' — a norma pede unless-stopped" ;;
  esac

  # 6. Nome no padrao <slug>-<papel>
  if [ -n "$DOMINIO" ]; then
    SLUG=$(echo "$DOMINIO" | tr '.' '-')
    echo "$C" | grep -q "^${SLUG}-" || falha "nome fora do padrao (esperado ${SLUG}-<papel>)"
  fi

  if [ "$VIOLACOES" -eq "$ERROS_ANTES" ]; then
    verde "    ✓ conforme"
    CONFORMES=$((CONFORMES + 1))
  fi
  echo
done

# Redes data precisam ser internal
echo "── redes"
for N in $(docker network ls --format '{{.Name}}' | grep -- '-data$'); do
  INTERNAL=$(docker network inspect "$N" --format '{{.Internal}}' 2>/dev/null)
  if [ "$INTERNAL" = "true" ]; then
    verde "    ✓ $N (internal)"
  else
    falha "$N nao e internal — os containers de dados tem saida para a internet"
  fi
done
docker network ls --format '{{.Name}}' | grep -qw "edge" \
  && verde "    ✓ rede edge existe" \
  || amarelo "    ! rede edge ainda nao existe (docker network create edge)"

echo
echo "═══════════════════════════════════════════════════════════"
TOTAL=$(docker ps -q | wc -l)
echo " containers conformes: $CONFORMES de $TOTAL"
echo " violacoes: $VIOLACOES"
echo "═══════════════════════════════════════════════════════════"

if [ "$VIOLACOES" -gt 0 ] && [ "$STRICT" -eq 1 ]; then
  exit 1
fi
exit 0
