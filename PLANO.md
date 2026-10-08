# Observabilidade + Saúde de SEO — Portfólio Ávila Ops

## Contexto

O portfólio hoje tem ~20 domínios/projetos ativos espalhados entre um VPS Hetzner próprio (178.105.82.48, produção — 12 containers via Docker Compose + Caddy), GitHub Pages, e um caso isolado no Render.com. Não existe visão unificada de saúde: sem uptime monitoring, sem alertas, SEO técnico (sitemap/robots/IndexNow/etc.) implementado de forma desigual entre projetos, e já apareceram problemas reais no meio do levantamento — `maprojetos.com.br` sem DNS resolvendo, `cifrainssdeobras.com.br` apontando para o lugar errado (GitHub Pages em vez do VPS onde o site de fato roda).

O usuário pediu um checklist amplo (domínios, robots, sitemap, llms.txt, IndexNow, Search Console, Bing Webmaster, analytics, SSL, expiração de domínio/hospedagem, Core Web Vitals, 404s, links quebrados, páginas órfãs, canonicals, schema.org, uptime, alertas automáticos, entre outros). A exploração encontrou duas coisas que mudam a abordagem:

1. **O VPS de produção está sem folga de recursos** (2 vCPU, 3.7GB RAM, só ~743MB livres, já usando swap) — não há espaço seguro para rodar observabilidade ali. Decisão já tomada: **novo VPS Hetzner dedicado**.
2. **`app.avila.inc` já tem a base de dados certa para SEO** — modelos Prisma `DomainAsset` (com `expiresAt`, `cloudflareZoneId`, `indexNowKey`) e `IntegrationConnection` (genérico por `provider`+`siteUrl`, já usado para IndexNow e Search Console), mais um painel funcional em `/operacao/seo`. Isso não é um projeto do zero — é expansão do que já existe.

Decisões já confirmadas com o usuário: novo VPS **CPX21** (2 vCPU/4GB), alertas por **email** para começar, painel em **obs.avilaops.com**, stack self-hosted (Grafana Cloud foi recusado).

## Arquitetura

```
VPS OBSERVABILIDADE (novo, CPX21)          VPS PRODUÇÃO (178.105.82.48, existente)
┌────────────────────────────┐             ┌──────────────────────────────┐
│ Caddy (TLS + Basic Auth)    │             │ Caddy (já existe)             │
│ Grafana (auth obrigatória)  │             │ 12 containers de produção     │
│ Prometheus                  │◄─túnel──────┤ node_exporter (novo, leve)    │
│ blackbox_exporter           │  WireGuard  │ cAdvisor (novo, leve)         │
│ Alertmanager → email        │             │ promtail (novo, envia logs    │
│ Loki (logs)                 │◄─HTTPS──────┤   via HTTPS p/ Loki)          │
│ cron: domain-expiry check   │             └──────────────────────────────┘
│ cron: PageSpeed Insights    │
└────────────────────────────┘             app.avila.inc (no VPS produção)
         │ HTTPS (API key de serviço)       ┌──────────────────────────────┐
         └──────────────────────────────────► /api/integrations/seo-audit  │
                                             │ expande DomainAsset/          │
                                             │ IntegrationConnection já      │
                                             │ existentes                    │
                                             └──────────────────────────────┘
```

Princípio: o VPS de observabilidade **puxa** dados (pull) — nunca sobrecarrega produção com agentes pesados. `node_exporter`/`cAdvisor`/`promtail` são leves (poucos MB de RAM). Prometheus, Grafana, Loki, Lighthouse ficam inteiramente no VPS novo.

Túnel **WireGuard** entre os dois VPS para métricas/logs — não expor `node_exporter`/`cAdvisor` publicamente.

## Fase 1 — Quick win: uptime + SSL + status HTTP + infra clássica (maior impacto, menor esforço)

Cobre de uma vez: uptime, certificado SSL (+ expiração), status HTTP, tempo de resposta, métricas de servidor, logs.

1. **Provisionar VPS Hetzner CPX21**, Ubuntu 24.04 LTS, Docker + Compose plugin.
2. **`docker-compose.yml`** no novo VPS com: `caddy` (proxy + TLS automático para `obs.avilaops.com` + Basic Auth na frente do Grafana), `grafana` (`GF_SECURITY_ADMIN_PASSWORD` via `.env` fora do git, `GF_USERS_ALLOW_SIGN_UP=false`, `GF_AUTH_ANONYMOUS_ENABLED=false`), `prometheus`, `blackbox_exporter`, `alertmanager`, `loki` + config local `node_exporter`.
3. **WireGuard** entre os dois VPS; instalar `node_exporter` + `cAdvisor` no VPS de produção (containers leves, sem exposição pública — só acessíveis via IP do túnel).
4. **`prometheus/targets/domains.yml`** (via `file_sd_configs`) listando os ~20 domínios do portfólio para o job `blackbox-http` (módulo `http_2xx`, que já retorna `probe_ssl_earliest_cert_expiry` de graça). Domínios com DNS ainda pendente (ex. estado atual de `maprojetos.com.br`/`cifrainssdeobras.com.br` até a migração ser concluída) entram desde o dia 1 — aparecem como falha, o que serve de lembrete visual.
5. **`alert.rules.yml`**: `ProbeDown` (>5min fora do ar), `SSLCertExpiringSoon` (<14 dias), `HighResponseTime` (>3s por >10min). Alertmanager configurado com receiver de **email** (usar Resend, já disponível no ambiente, ou SMTP existente).
6. **Expiração de domínio/hospedagem**: script cron simples (Node/TS) no VPS de observabilidade, rodando 1x/dia, que consulta `whois` (ou API Cloudflare para os domínios já lá) e grava em `DomainAsset.expiresAt` via `app.avila.inc` — sem criar tabela nova, campo já existe.
7. Dashboards prontos do grafana.com: "Blackbox Exporter" (7587), "Node Exporter Full" (1860), "Docker Container" (893).

**Critério de saída**: Grafana em `obs.avilaops.com` só acessível com auth, mostrando uptime/SSL/latência dos ~20 domínios + CPU/RAM/disco dos dois VPS, com um alerta de teste chegando por email de ponta a ponta.

## Fase 2 — SEO técnico centralizado em `app.avila.inc`

Expandir, não recriar — reaproveitar `DomainAsset`, `IntegrationConnection`, e o padrão já usado em `src/lib/indexnow.ts`/`search-console.ts`.

1. **`app.avila.inc/src/lib/seo-audit.ts`**: nova função que itera `prisma.domainAsset.findMany()` e roda por domínio os checks já provados em `brilhax.com/scripts/check-live-seo.mjs` (robots.txt, sitemap.xml, llms.txt, favicons, manifest.json, canonical da home, JSON-LD/schema.org, Open Graph, Twitter Card).
2. Persistir em `IntegrationConnection` com `provider: 'seo_audit'`, reaproveitando `metadata: Json` para o detalhe — sem migration de schema.
3. **UI**: nova seção "Auditoria técnica" em `src/app/operacao/seo/page.tsx`, ao lado dos painéis de IndexNow/Search Console já existentes.
4. **Agendamento**: rota `/api/integrations/seo-audit/run` (mesmo padrão de `/api/integrations/indexnow`), chamada por cron **no VPS de observabilidade** via HTTPS com API key de serviço dedicada (não sessão de admin humana).
5. Sites hoje sem sitemap/robots (cifra, Despolarizamed, wabusiness, tagflow, etc.) aparecerão como "ausente" — isso vira a lista de trabalho priorizada automaticamente, não precisa ser corrigido antes desta fase.
6. **Links quebrados / páginas órfãs**: extensão do mesmo script com um crawler leve (ex. `linkinator`, fila limitada por domínio), rodando semanalmente (não diário, custo de crawling é maior). Mesma persistência, `provider: 'link_audit'`.

**Critério de saída**: painel `/operacao/seo` mostra, por domínio, status de robots/sitemap/llms/OG/Twitter/favicon/manifest/canonical/schema, atualizado diariamente.

## Fase 3 — Core Web Vitals

Usar a **API pública do PageSpeed Insights** (Google roda o Lighthouse do lado deles, resposta em JSON, sem custo de CPU local) em vez de Lighthouse CI local — mais barato e simples para ~20 domínios.

1. Cron semanal no VPS de observabilidade chamando a API do PageSpeed Insights para home (+1-2 páginas-chave) de cada domínio.
2. Gravar métricas-chave (LCP, CLS, INP, performance score) via a mesma rota de API de `app.avila.inc`/`IntegrationConnection` (`provider: 'lighthouse'`) — um painel só, não fragmentar.
3. Lighthouse CI local (container com Chromium headless) fica como opção futura, só se precisar testar staging/preview que a API pública não alcança.

**Critério de saída**: Core Web Vitals de todos os domínios de produção coletados semanalmente, visíveis no painel de SEO, com alerta simples se performance score cair abaixo de um limiar.

## Fase 4 — Integrações de API adicionais

1. **Bing Webmaster Tools**: `src/lib/bing-webmaster.ts` espelhando `search-console.ts` (API REST com API key, mais simples que OAuth do GSC). `provider: 'bing_webmaster'`.
2. **Cobertura completa de Search Console/IndexNow** nos ~20 domínios (hoje provavelmente só `avila.inc` está verificado) — trabalho de configuração, não de código novo.
3. **Histórico de indexação**: já coberto por `lastSyncedAt`/`lastSyncStatus` existentes — só precisa de uma view de timeline no painel.
4. **Backlinks / posição de palavras-chave**: backlog explícito, não comprometido nesta rodada — exige serviço terceiro pago (Ahrefs/SEMrush), sem caminho gratuito viável. Revisitar se/quando houver orçamento.

## Notas de execução

- Trazer a infraestrutura para controle de versão: hoje o Caddyfile de produção só existe ao vivo no VPS. Puxar via SSH e commitar (junto com o `docker-compose.yml` do novo VPS, configs de Prometheus/blackbox/Alertmanager/Grafana) numa pasta de infra versionada — evita repetir o problema atual de config sem histórico.
- Loki (não OpenSearch) é a escolha para logs: volume de log do portfólio é baixo-médio, busca full-text sofisticada não é necessidade real hoje, e Loki é uma fração do custo de RAM do OpenSearch (que pediria 2-4GB só para si, apertado num CPX21). Reavaliar só se o volume/necessidade crescer muito.
- Tempo (tracing distribuído) do template do Saude Pet **não** entra no novo compose — overkill para sites majoritariamente estáticos/CRUD; senha hardcoded do Grafana nesse template (`admin123`) também não deve ser reaproveitada.
- Execução no servidor (provisionar VPS, aplicar WireGuard, subir containers) segue o mesmo modelo já usado nesta sessão: preparar tudo localmente (compose, configs) e aplicar via SSH com a chave `hetzner_avilaops`.

## Verificação

- Fase 1: `curl -u user:pass https://obs.avilaops.com` retorna Grafana; painel "Blackbox Exporter" mostra os ~20 domínios; forçar um alerta de teste (`amtool alert add` ou derrubar um target temporariamente) e confirmar recebimento por email.
- Fase 2: rodar `/api/integrations/seo-audit/run` manualmente uma vez, conferir que `IntegrationConnection` populou linhas com `provider: 'seo_audit'` e que o painel `/operacao/seo` renderiza os resultados.
- Fase 3: rodar o cron de PageSpeed manualmente contra 1 domínio, conferir gravação e exibição no painel.
- Fase 4: testar submissão de teste ao Bing Webmaster API contra `avila.inc` e confirmar `status: ACTIVE` gravado.
