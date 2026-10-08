# monitoring/prometheus/secrets/

Esta pasta **não é commitada** (ver `.gitignore`). Você precisa criar os arquivos abaixo manualmente no servidor antes de subir a stack.

## Arquivos necessários

### `app-avila-inc-metrics-token`

Token Bearer usado pelo Prometheus para autenticar o scrape de `GET https://app.avila.inc/api/metrics`.

**Criar o token:**
```bash
openssl rand -hex 32
```

**Salvar aqui** (sem quebra de linha, sem aspas):
```bash
printf 'SEU_TOKEN_AQUI' > monitoring/prometheus/secrets/app-avila-inc-metrics-token
```

**Configurar no app.avila.inc** (VPS de produção, `.env.local`):
```
METRICS_BEARER_TOKEN=SEU_TOKEN_AQUI
```

Reiniciar o container do app após adicionar a variável:
```bash
docker compose restart app
```

**Validar:**
```bash
curl -s -H "Authorization: Bearer $(cat monitoring/prometheus/secrets/app-avila-inc-metrics-token)" \
  https://app.avila.inc/api/metrics | head -20
```

---

## Permissões da pasta

O Prometheus roda como UID 65534 (nobody) dentro do container. Os arquivos de segredo precisam ser legíveis por esse UID:

```bash
chmod 644 monitoring/prometheus/secrets/app-avila-inc-metrics-token
```

---

## Referência no prometheus.yml

O job `app-avila-inc-seo` em `monitoring/prometheus/prometheus.yml` referencia este arquivo:
```yaml
authorization:
  credentials_file: /etc/prometheus/secrets/app-avila-inc-metrics-token
```

A pasta é montada como volume `read-only` no container do Prometheus (ver `docker-compose.yml`).
