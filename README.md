# infra

Infraestrutura compartilhada da Avila Ops: monitoramento, logs e deploy dos aplicativos e sites.

Repositório canônico: https://github.com/avilaops/infra, branch `main`.

Os workflows reutilizáveis ficam em `.github/workflows/`. As configurações dos destinos estão em `deploy/production/`, e os scripts de publicação em `scripts/deploy-*.sh`. As aplicações referenciam os workflows por commit.

As seções de observabilidade abaixo incluem planejamento histórico e precisam ser conferidas com os servidores antes de aplicar suas instruções.

## O que está pronto aqui (preparado localmente)

- `docker-compose.yml` — stack do VPS de observabilidade: Caddy (TLS + Basic Auth), Grafana, Prometheus, blackbox_exporter, node_exporter local, Alertmanager, Loki.
- `producao-agents/docker-compose.yml` — agentes leves que rodam no VPS de **produção** (178.105.82.48): node_exporter, cAdvisor, Promtail. Não sobem no VPS de observabilidade.
- `monitoring/prometheus/targets/domains.yml` — lista de domínios monitorados via blackbox. Editar conforme o portfólio mudar (não precisa reiniciar o Prometheus).
- `monitoring/prometheus/alert.rules.yml` — regras: site fora do ar (5min), SSL expirando (<14 dias), resposta lenta (>3s), RAM/disco altos, exporter não responde.
- `monitoring/grafana/provisioning/` — datasources (Prometheus + Loki) e 3 dashboards prontos já baixados (Blackbox Exporter, Node Exporter Full, Docker Container).
- `.env.example` — copiar para `.env` e preencher antes de subir.
- `scripts/run-seo-audits.sh` — dispara as rotas de auditoria de SEO/saúde já implementadas em `app.avila.inc` (SEO técnico, PageSpeed, links quebrados, expiração de domínio, IndexNow, Bing Webmaster). Agendar via cron (passo 7).
- Job de scrape `app-avila-inc-seo` em `monitoring/prometheus/prometheus.yml` — puxa `GET https://app.avila.inc/api/metrics` a cada 5min, autenticado por bearer token (arquivo em `monitoring/prometheus/secrets/`, nunca commitado). Essas séries dão o histórico real (score SEO, PageSpeed, links quebrados, dias até expirar domínio) que os dados em `IntegrationConnection` sozinhos não têm, porque lá cada rodada sobrescreve a anterior.

## O que só você pode fazer (fora do meu alcance nesta sessão)

### 1. Provisionar o VPS Hetzner novo (CPX21)
Console Hetzner → New Server → CPX21 (2 vCPU/4GB) → Ubuntu 24.04 LTS → adicionar sua chave SSH (`hetzner_avilaops.pub`) já existente em `~/.ssh/`.

### 2. Apontar DNS
Cloudflare → criar registro `A obs.avilaops.com → <IP do novo VPS>` (proxy pode ficar desligado ou ligado, tanto faz para uso interno).

### 3. Configurar túnel WireGuard entre os dois VPS
No **VPS de observabilidade** (vai ser `10.10.0.1`) e no **VPS de produção** (vai ser `10.10.0.2`):

```bash
# Em ambos:
apt install -y wireguard
wg genkey | tee privatekey | wg pubkey > publickey
```

Trocar as chaves públicas entre os dois, depois configurar `/etc/wireguard/wg0.conf` em cada lado (endpoint = IP público do outro, AllowedIPs = `10.10.0.0/24`), `wg-quick up wg0` e `systemctl enable wg-quick@wg0` nos dois.

Depois de validar `ping 10.10.0.2` (do lado do observabilidade) funcionando, os IPs já batem com o que está hardcoded em `monitoring/prometheus/prometheus.yml` e `producao-agents/promtail-config.yml`. Não precisa editar nada se usar exatamente `10.10.0.1`/`10.10.0.2`.

### 4. Preencher o `.env`
```bash
cp .env.example .env
```
Preencher `GRAFANA_ADMIN_PASSWORD`, `CADDY_BASICAUTH_HASH` (gerar com `docker run --rm caddy:2-alpine caddy hash-password --plaintext 'sua-senha'`), e as credenciais SMTP para alertas por e-mail.

### 5. Subir a stack
No VPS de observabilidade, depois de copiar esta pasta (`scp`/`rsync`) e configurar `.env`:
```bash
docker compose up -d
```

No VPS de produção, copiar `producao-agents/` e subir separadamente:
```bash
docker compose up -d
```

### 6. Validar
- `https://obs.avilaops.com` pede Basic Auth e depois mostra o login do Grafana.
- Dashboard "Blackbox Exporter" mostra os domínios do portfólio.
- Forçar um alerta de teste (ex. `docker compose stop` num container de teste, ou usar `amtool alert add` dentro do container do Alertmanager) e confirmar que o e-mail chega.

### 7. Conectar as métricas de SEO/auditoria (`app.avila.inc`)

> **O agendamento por cron deste passo está obsoleto (conferido em 2026-08-14).**
>
> O workflow n8n **"Rodada Diária — Auditoria e Saúde do Portfólio"**
> (`QGsuaBd6f48MwVVY`, em `n8n.avilaops.com`) já está **ativo** e chama as mesmas 6
> rotas de auditoria, com resumo no Discord. Ele se descreve como substituto do
> `scripts/run-seo-audits.sh`.
>
> Seguir o item **b** abaixo ao pé da letra faz as auditorias rodarem **duas vezes por
> dia** — cron e n8n em paralelo, gastando cota de PageSpeed à toa e sujando o
> histórico das métricas.
>
> - **Agendamento**: fica no n8n. Não criar a entrada de crontab.
> - **Métricas** (item **a**) e **validação** (item **c**): continuam valendo, são
>   independentes de quem dispara.
> - `scripts/run-seo-audits.sh` fica no repo como execução manual/fallback.

As rotas de auditoria (SEO técnico, PageSpeed, links quebrados, expiração de domínio, IndexNow, Bing Webmaster) já existem em `app.avila.inc` — implementadas separadamente, não por esta sessão. O que faltava era (a) expor o resultado como métricas Prometheus e (b) agendar a execução periódica; os dois já estão prontos aqui, faltando só configurar segredos e o cron:

**a. Gerar o token de scrape e configurar em ambos os lados:**
```bash
openssl rand -hex 32
```
- Colar o valor em `app.avila.inc/.env.local` (produção) como `METRICS_BEARER_TOKEN=...`.
- Colar o **mesmo** valor, sem quebra de linha, no arquivo `monitoring/prometheus/secrets/app-avila-inc-metrics-token` deste repo (não commitado, está no `.gitignore`).
- Reiniciar o container `app-avila-inc-app-1` no VPS de produção para carregar a variável nova.

**b. Configurar `SEO_AUDIT_API_KEY` e agendar o cron:**
- Confirmar que `app.avila.inc/.env.local` já tem `SEO_AUDIT_API_KEY` (ou `SERVICE_JWT_SECRET`) definido — as rotas de auditoria já checam esse header.
- No VPS de observabilidade, copiar `scripts/run-seo-audits.sh` para, por exemplo, `/opt/scripts/run-seo-audits.sh`.
- Adicionar ao crontab (`crontab -e`):
  ```cron
  # Auditoria SEO/saúde diária às 6h (horário do servidor)
  0 6 * * * SEO_AUDIT_API_KEY='<mesmo valor de app.avila.inc>' /opt/scripts/run-seo-audits.sh >> /var/log/seo-audit-cron.log 2>&1
  ```
- `run-seo-audits.sh` já chama `links/run` (checker de links quebrados) na mesma rodada diária — o plano original previa frequência semanal por ser mais caro, mas hoje o checker só varre a homepage (não crawla o site inteiro), então o custo é baixo o suficiente para rodar diário sem problema. Se algum domínio começar a demorar demais, mover essa chamada para uma segunda linha de cron semanal é só comentar a linha correspondente no script e criar uma entrada `0 6 * * 1` separada chamando só essa rota.

**c. Validar:**
- `curl -H "Authorization: Bearer <token>" https://app.avila.inc/api/metrics` retorna texto no formato Prometheus (`avilaops_seo_audit_score{fqdn="avila.inc"} 85`, etc.).
- Alvo `app-avila-inc-seo` aparece "UP" em `https://obs.avilaops.com/prometheus/targets` (ou via Grafana Explore).
- Rodar `run-seo-audits.sh` manualmente uma vez e conferir no log que todas as 6 rotas retornaram HTTP 200.

## Projetos novos em desenvolvimento (2026-08-13)

Três projetos novos entraram em desenvolvimento em 13/08/2026. Situação de cada um:

- **sorroche** (`D:\Administrativo\Websites\sorroche`) — site institucional. Atualizado em 07/10/2026:
  `sorroche.beauty` está publicado no GitHub Pages (repo `avilaops/sorroche.beauty`), fora do Hetzner, e
  responde 200 no alvo de `monitoring/prometheus/targets/domains.yml`. O `app.sorroche.beauty` foi
  desligado em 16/09/2026 e os restos estão arquivados em `/opt/arquivo/sorroche-20261006/` no servidor
  `applications`. Nada a fazer aqui além do check HTTP.
- **ERP** (`D:\ERP`) — backend Express/Postgres/Redis. Endpoint `/metrics` já implementado e
  testado localmente (contagem/latência por rota via `prom-client`, mais um contador de
  resultado do fiscal worker). Job de scrape já está em `monitoring/prometheus/prometheus.yml`, comentado
  — descomentar e ajustar o `target`/token quando o ERP tiver deploy real.
- **Espetaria** (`D:\Administrativo\Websites\Espetaria`) — ainda é só especificação
  (`README.md`), nenhum código escrito. Nada a monitorar ainda; quando o backend existir,
  seguir o mesmo padrão do ERP (`/metrics` com `prom-client` ou similar).

## Próximas fases

- Dashboard Grafana dedicado a SEO/auditoria (score ao longo do tempo, PageSpeed, links quebrados, expiração de domínio) — ainda não criado; as métricas já estão disponíveis em `avilaops_*` para montar os painéis.
- Envio dos logs de execução do `run-seo-audits.sh` para o Loki via Promtail (hoje só vão para `/var/log/seo-audit-cron.log` local).
- Bing Webmaster: rota + UI já implementadas (`/api/integrations/bing-webmaster/run`, `BingWebmasterPanel.tsx` em `/operacao/seo`) — falta só confirmar `BING_WEBMASTER_API_KEY` configurada em produção.
- Cobertura completa de Search Console/IndexNow nos ~20 domínios do portfólio (hoje maior parte ainda não verificada) — trabalho de configuração, não de código.
