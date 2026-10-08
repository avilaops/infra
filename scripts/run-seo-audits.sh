#!/bin/sh
# Dispara as auditorias de SEO/saúde do portfólio em app.avila.inc.
# Agendado via cron no VPS de observabilidade — ver README para o crontab.
#
# Cada rota, sem {fqdn} no body, roda para todos os domínios não-arquivados
# de uma vez (comportamento já implementado em app.avila.inc). O resultado
# fica em IntegrationConnection e é exposto via GET /api/metrics para o
# Prometheus fazer scrape — este script não grava métricas diretamente.
#
# Requer SEO_AUDIT_API_KEY no ambiente (mesmo valor de SEO_AUDIT_API_KEY ou
# SERVICE_JWT_SECRET configurado em app.avila.inc).

set -eu

APP_BASE_URL="${APP_BASE_URL:-https://app.avila.inc}"
LOG_TAG="seo-audit-cron"

if [ -z "${SEO_AUDIT_API_KEY:-}" ]; then
  echo "$LOG_TAG: SEO_AUDIT_API_KEY não definido no ambiente." >&2
  exit 1
fi

run_route() {
  route="$1"
  label="$2"
  echo "$LOG_TAG: iniciando $label"

  http_code=$(curl -sS -o /tmp/seo-audit-response.json -w "%{http_code}" \
    -X POST "${APP_BASE_URL}${route}" \
    -H "x-service-key: ${SEO_AUDIT_API_KEY}" \
    -H "Content-Type: application/json" \
    -d '{}' \
    --max-time 120) || http_code="curl_error"

  if [ "$http_code" = "200" ]; then
    echo "$LOG_TAG: $label concluído (HTTP 200)"
  else
    echo "$LOG_TAG: $label FALHOU (HTTP $http_code) — $(cat /tmp/seo-audit-response.json 2>/dev/null | head -c 500)" >&2
  fi
}

# Auditoria técnica (robots/sitemap/llms/OG/canonical/schema) — diária.
run_route "/api/integrations/seo-audit/run" "auditoria SEO técnica"

# Core Web Vitals via PageSpeed Insights — mais caro por chamada, ainda cabe diário.
run_route "/api/integrations/lighthouse/run" "PageSpeed/Core Web Vitals"

# Links quebrados (só homepage por domínio) — semanal (chamado condicionalmente, ver crontab).
run_route "/api/integrations/links/run" "links quebrados"

# Expiração de domínio/hospedagem — diária, barata.
run_route "/api/domains/check-renewals/run" "expiração de domínio"

# IndexNow — reenvia sitemap a cada rodada, diário está OK (idempotente).
run_route "/api/integrations/indexnow/submit-all" "IndexNow"

# Bing Webmaster — mesmo padrão do IndexNow.
run_route "/api/integrations/bing-webmaster/run" "Bing Webmaster"

echo "$LOG_TAG: rodada concluída."
