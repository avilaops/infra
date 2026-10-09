# Inventário dos servidores

Levantado por acesso SSH direto às máquinas em 16/08/2026 e **reconferido contra a
produção em 07/10/2026 01:50 UTC (06/10 22:50 em Brasília)**, só leitura
(`docker ps -a`, `ss -ltn`, `caddy adapt`, `/etc/avilaops/deploy`, `systemctl`,
`ufw status`, `crontab -l`, `/etc/cron.d`, `ls` e `du` em `/opt` e `/var/www`).
Tudo aqui foi lido do servidor, não de documentação. O que não pôde ser relido
nesta conferência está marcado como **não reconferido**.

Este arquivo não guarda senha, token nem chave: só o lugar onde cada segredo vive
(seção 0). As versões anteriores a 07/10/2026 traziam 7 senhas em texto puro e
continuam no histórico do git; essas 7 credenciais (marcadas na tabela da
seção 0) devem ser tratadas como vazadas. Em 07/10/2026 (tarefa 161) foram
trocadas as duas de Postgres que estavam em uso (`cifra` e `postgres` do
`plataforma`); o que falta está na última coluna daquela tabela.

---

## 0. Acessos e onde vivem os segredos

### Drives mapeados (Samba pelo túnel)

| Letra | Caminho | Servidor |
| --- | --- | --- |
| `P:` | `\\10.10.0.2\raiz` | produção (`ubuntu-4gb-nbg1-1`) |
| `I:` | `\\10.10.0.1\raiz` | era o infra (16/08/2026). Em 07/10/2026 o infra está sem sinal (seção 9) e quem tem `10.10.0.1` na `wg0` e um `smbd` com o compartilhamento `raiz` é o `creators` (seção 1). Para onde o `I:` vai hoje depende da configuração da WireGuard no PC, **não conferida** |

Postura do Samba na produção, relida em 07/10/2026 (`/etc/samba/smb.conf`,
`ss -ltn`, `ufw status`):

| Item | Estado |
| --- | --- |
| Interfaces | `127.0.0.1/8` e `10.10.0.2/24`, `bind interfaces only = yes` (a `wg0` é ponto a ponto, sem broadcast, por isso vai por endereço e não por nome) |
| Escuta | `127.0.0.1:445` e `10.10.0.2:445`; nada no IP público |
| Criptografia | `smb encrypt = required`, `server min protocol = SMB3_00` |
| UFW | `445/tcp` liberado só na `wg0` |
| Compartilhamentos | `raiz` e `servidor` (os dois em `/`) e `adminer` (`/var/www/adminer`), `valid users = nicolas` |

No Windows o túnel é o serviço `WireGuardTunnel$avilaops` (sobe no boot; o
`.conf` precisa de leitura para `SYSTEM`).

### pgAdmin

Arquivo de importação: `C:\Users\nicol\Documents\pgadmin-avilaops-servers.json`
(Tools → Import/Export Servers). Cada conexão usa o túnel SSH nativo do pgAdmin
com a chave ED25519 do Nicolas; não depende da WireGuard. O arquivo não carrega
senha.

Os endereços `172.x` de contêiner mudam quando o contêiner é recriado; a saída
definitiva é publicar o banco em `127.0.0.1:porta` no compose.

### Onde vive cada credencial

| Credencial | Usuário | Onde vive (nunca neste arquivo) | Estado em 07/10/2026 | Exposta no histórico do git? |
| --- | --- | --- | --- | --- |
| Samba, produção (e infra, **não reconferido**) | `nicolas` | `/var/lib/samba/private/passdb.tdb` em cada servidor; no PC, Gerenciador de Credenciais do Windows | em uso na produção | **sim** — **não trocada** (decisão da tarefa 161: derrubaria os drives `P:`/`I:`); `445` só em `127.0.0.1` e `10.10.0.2`, conferido em 07/10/2026 |
| Postgres do host | `postgres` | sem arquivo: role do cluster `18/main`; local e `127.0.0.1` entram por `trust` no `pg_hba.conf` | em uso | não (a célula de senha dizia "nenhuma") |
| Samba do `creators` | `nicolas` | `/var/lib/samba/private/passdb.tdb` no `creators` | `smbd` em `127.0.0.1`, `10.66.0.10` e `10.10.0.1`; `ufw` libera a `445` na `wg0` só para `10.66.0.1` e `10.10.0.3`; nenhuma sessão aberta em 07/10/2026 | **não** — senha **diferente** da exposta (hash do `passdb.tdb` comparado em 07/10/2026, tarefa 180, sem conectar) |
| Postgres `mello` | `mello` | `/opt/mello/.env`, variável `DB_PASSWORD` (compose em `/opt/mello/docker-compose.yml`) | contêiner não existe mais; role `mello` não existe em nenhum cluster (07/10/2026) | **sim** — nada a trocar; o `.env` parado ainda guarda o valor exposto: gerar senha nova antes de religar |
| Postgres `agenda_crm` | `agenda_crm` | era `/opt/agenda-crm` (diretório não existe mais) | contêiner removido; role não existe em nenhum cluster (07/10/2026) | **sim** — nada a trocar |
| Postgres `plataforma` | `postgres` | `/opt/minas-espetinhos/.env` (modo 600), variáveis `POSTGRES_PASSWORD` e `MIGRATION_DATABASE_URL` (role `postgres`), `PLATAFORMA_APP_PASSWORD` e `PLATAFORMA_PLATFORM_PASSWORD`; recriado em 07/10/2026 a partir de `/root/minas-env-backup-1786798100`; cópia anterior à troca em `/opt/minas-espetinhos/.env.bak-20261007-t161` (modo 600, guarda o valor antigo) | em uso (religado em 07/10/2026) | **sim** — **trocada em 07/10/2026** (tarefa 161); o `.env` recriado tinha mantido o valor exposto |
| Postgres `cifra_calculadora` | `cifra` | `/opt/cifra/.env`, variável `DB_PASSWORD` (desde 07/10/2026, tarefa 197; o `.env` guarda também `CALCULADORA_ADMIN_PASSWORD`); `/opt/cifra/docker-compose.yml` só referencia `${DB_PASSWORD:?…}` em `POSTGRES_PASSWORD` e `DATABASE_URL`, sem valor; os dois em modo 600. Cópias em `/opt/cifra`, modo 600: `docker-compose.yml.bak-20261007-t197` (guarda o valor **em uso**), `.env.bak-20261007-t197` e `docker-compose.yml.bak-20261007-t161` (guarda o valor antigo) | em uso | **sim** — **trocada em 07/10/2026** (tarefa 161) |
| Postgres `medusa_store` | `medusa` | era `/opt/brilhax-stack` (diretório não existe mais) | contêiner removido em 16/09/2026; role não existe em nenhum cluster (07/10/2026) | **sim** — nada a trocar |
| Postgres `n8n` (infra) | `n8n` | `/opt/n8n/.env` no servidor infra, variável `DB_POSTGRESDB_PASSWORD` — **não reconferido** | ver seção 9 | **sim** — **pendente**: servidor infra inalcançável (sem ping, 22 fechada); a role `n8n` do Postgres do host da produção tem outra senha (conferido em 07/10/2026) |
| Bancos `erp` e `lojas` | — | `/opt/erp/.env` e `/opt/lojas/.env`, variável `DATABASE_URL` (modo `600`, root) | em uso | não |
| Tokens de integração | — | `/etc/avilaops/tokens.env` (lido pelos crons do root) | em uso | não |
| Evolution API (WhatsApp) | `evolution_avilaops_com` | `/opt/evolution-avilaops-com/.env` (modo 600, root): `DATABASE_CONNECTION_URI` (senha da role) e `AUTHENTICATION_API_KEY` (header `apikey`); os dois gerados no servidor em 09/10/2026 | em uso | não |

A última coluna diz se o **valor** da credencial esteve em texto puro neste
arquivo, em versões anteriores ao commit `aabc782` (06/10/2026). São **7**: a do
Samba e as de seis roles de Postgres (`mello`, `agenda_crm`, `postgres` do
`plataforma`, `cifra`, `medusa` e `n8n`). Só essas precisam de troca por causa do
histórico; as demais nunca tiveram valor escrito aqui. Das 7, estavam em uso a do
Samba, a do `cifra_calculadora` e a do `plataforma` (o `.env` recriado em
07/10/2026 tinha mantido o valor antigo). Situação depois da tarefa 161
(07/10/2026): `cifra` e `postgres` do `plataforma` **trocadas** (a senha antiga
não autentica mais); `mello`, `agenda_crm` e `medusa` sem role em nenhum cluster
da produção nem do `creators`, e nenhum dos 7 valores bate com o verificador de
role alguma; **Samba não trocada** e **`n8n` do infra pendente**.

---

## 1. Os dois servidores

| | Produção | Infra (**não reconferido**, dados de 16/08/2026) |
| --- | --- | --- |
| Nome no Hetzner | `ubuntu-4gb-nbg1-1` | `infra` (`ubuntu-4gb-nbg1-2`) |
| IP público | `178.105.82.48` | `23.88.60.193` |
| IP no túnel | `10.10.0.2` | `10.10.0.1` (em 16/08/2026; hoje o `creators` também usa esse endereço, ver abaixo) |
| Tipo | CX23 (não reconferido) | CX23 |
| CPU | 2 vCPU Intel Xeon Skylake (não reconferido) | 2 vCPU AMD EPYC Rome |
| RAM | 3,7 GB — 2,4 usados | 3,7 GB — 1,7 usados |
| Swap | 5 GB — **2,3 GB em uso** | **nenhum** |
| Disco raiz | 38 GB — 58% usado | 38 GB — 26% usado |
| Disco extra | volume 79 GB — 62% (`/var/lib/docker`) | — |
| Sistema | Ubuntu 26.04 LTS, kernel 7.0.0-15 | Ubuntu 26.04 LTS, kernel 7.0.0-29 |
| No ar desde | 15/07/2026 | 12/08/2026 |
| Custo | € 5,49/mês (não reconferido) | € 5,49/mês |
| Localização | Nuremberg, `eu-central` | Nuremberg, `eu-central` |

WireGuard na faixa `10.10.0.0/24`, porta 51820. Pares vistos da produção em
07/10/2026 (`wg show wg0`): `10.10.0.3` e `10.10.0.4` (`204.168.249.111`) com
handshake há menos de um minuto; `10.10.0.1` (infra, `23.88.60.193`) com último
handshake **há 29 dias**.

O mesmo endereço `10.10.0.1/24` está hoje também na `wg0` do servidor `creators`
(`62.238.119.61`, o do OpenClaw; `Address = 10.66.0.10/24, 10.10.0.1/24`),
conferido em 07/10/2026 (tarefa 180). Lá há pares para `10.10.0.2`, `10.10.0.3` e
`10.10.0.4`, **sem nenhum handshake**: a produção continua com o par `10.10.0.1`
apontado para o infra antigo, então produção e `creators` não se falam por essa
faixa. O PC do Nicolas fala com o `creators` pela faixa `10.66.0.0/24`
(`10.66.0.1`).

Usuários com shell na produção: `root`, `postgres`, `deploy` (uid 1002),
`gha-deploy` (uid 1003).

---

## 2. Domínios — 44 hostnames em 30 blocos do Caddy

Conferido em 07/10/2026 com `caddy adapt --config /etc/caddy/Caddyfile` (44
hostnames) e leitura dos blocos: 28 no `Caddyfile` (um deles o genérico
`https://`) e 2 em `/etc/caddy/lojas.d/*.caddy`.

Atualização de 08/10/2026 (tarefa 252): `n8n.avilaops.com` passou a ser servido
por este Caddy (bloco próprio no `Caddyfile`, n8n nativo da seção 4) e o
registro A resolve para `178.105.82.48` (`dig` em `1.1.1.1` e `8.8.8.8`). O
`caddy adapt` dessa data lista 48 hostnames; a contagem e as tabelas abaixo são
as de 07/10/2026 e não foram refeitas.

### Avila Ops

| Domínio | Destino |
| --- | --- |
| `avilaops.com` | estático `/var/www/avilaops.com` |
| `www.avilaops.com` | estático `/var/www/avilaops.com` |
| `app.avilaops.com` | `127.0.0.1:3004` |
| `app.avila.inc` | `127.0.0.1:3004` |
| `auth.avilaops.com` | `172.31.0.10:3010` (rede `edge`) |
| `erp.avilaops.com` | `127.0.0.1:3140` (contêiner `erp`; não é mais Odoo) |
| `jobs.avilaops.com` | estático `/var/www/jobs.avilaops.com` |
| `mail.avilaops.com` | `127.0.0.1:3042` (API) e `127.0.0.1:3041` (webmail) |
| `mail-mcp.avilaops.com` | `127.0.0.1:8790` — **nada escutando** |
| `mta-sts.avilaops.com` | resposta fixa do Caddy (política MTA-STS) |
| `lojas.avilaops.com` | `127.0.0.1:3080`, com um caminho para `127.0.0.1:5191` (`vedashow-sync`) |
| `*.lojas.avilaops.com` | `127.0.0.1:3080` |
| `pkvedacoes.avilaops.com` | redirect 301 → `pkvedacoes.com.br` |
| `avila.inc` | redirect 301 → `avilaops.com` |
| `www.avila.inc` | redirect 301 → `avilaops.com` |

### Lojas com domínio próprio (`/etc/caddy/lojas.d/`)

| Domínio | Destino |
| --- | --- |
| `brilhax.com` | `127.0.0.1:3080` |
| `www.brilhax.com` | `127.0.0.1:3080` |
| `pkvedacoes.com.br` | `127.0.0.1:3080` |
| `www.pkvedacoes.com.br` | `127.0.0.1:3080` |
| `vedashow.com.br` | `127.0.0.1:3080` |
| `www.vedashow.com.br` | `127.0.0.1:3080` |

Teto de corpo no proxy (08/10/2026, tarefa 215): os três blocos do lojas
(`lojas.avilaops.com, *.lojas.avilaops.com` no `Caddyfile`, `brilhax.caddy` e
`dominios.caddy` em `lojas.d/`) importam o snippet `(lojas_teto_corpo)` do
`Caddyfile`: `request_body { max_size 13MiB }` só em `/api/painel/*`, 413 acima
disso com ou sem `content-length`. O maior teto da aplicação é 12 MiB + 64 KiB
(planilha, `src/lib/limites-upload.ts` do lojas); se ele subir, este sobe junto.
O `dominios.caddy` é gerado por `/opt/lojas/caddy-sync.sh`, que no servidor tem
a linha `import lojas_teto_corpo` e diverge do `deploy/caddy-sync.sh` do
repositório do lojas. Só `lojas.avilaops.com` passa pela Cloudflare; os domínios
próprios resolvem direto para o servidor.

### SaúdePet

| Domínio | Destino |
| --- | --- |
| `saudepet.app.br` | `127.0.0.1:3005` |
| `www.saudepet.app.br` | redirect 301 → `saudepet.app.br` |

### Sorroche

| Domínio | Destino |
| --- | --- |
| `sorroche.beauty` | GitHub Pages (repo `avilaops/sorroche.beauty`), fora do Hetzner desde 2026-10-06 |
| `www.sorroche.beauty` | GitHub Pages (CNAME `avilaops.github.io`) |
| `app.sorroche.beauty` | desligado: bloco retirado do Caddy em 2026-09-16, sem contêiner; o DNS (Cloudflare, proxy) segue apontando e responde 525 |

### Comandeiro e Brasa Mineira

O contêiner que atende a `3040` (`minas-espetinhos-app-1`) foi removido em
06/10/2026 02:08 UTC e **religado em 07/10/2026 03:23 UTC** (tarefa 153), com
a imagem do último deploy (`ghcr.io/avilaops/app.comandeiro.com.br@sha256:bfe4aede…`,
commit `49fe297`, de 14/09) e o banco `plataforma` restaurado do dump de 04/10.
O compose é `/opt/minas-espetinhos/docker-compose.prod.yml` (cópia do commit
`49fe297`; o arquivo e o `.env` tinham saído do diretório em 16/09). O
`ifood-poller` não foi religado: depende de `COMANDEIRO_ADMIN_TOKEN`, que não
está no `.env` recriado.

| Domínio | Destino |
| --- | --- |
| `comandeiro.com` | estático `/var/www/comandeiro.com` |
| `www.comandeiro.com` | estático `/var/www/comandeiro.com` |
| `comandeiro.com.br` | estático `/var/www/comandeiro.com.br` |
| `www.comandeiro.com.br` | estático `/var/www/comandeiro.com.br` |
| `app.comandeiro.com.br` | `127.0.0.1:3040` (`minas-espetinhos-app-1`) |
| `brasa.comandeiro.com.br` | `127.0.0.1:3040` (`minas-espetinhos-app-1`) |
| `brasamineira.com.br` | `127.0.0.1:3040` (`minas-espetinhos-app-1`) |
| `www.brasamineira.com.br` | redirect 301 → `brasamineira.com.br` |
| `https://` (qualquer outro host) | `127.0.0.1:3040` (`minas-espetinhos-app-1`) |

### CIFRA

| Domínio | Destino |
| --- | --- |
| `cifrainssdeobras.com.br` | `127.0.0.1:3010` |
| `www.cifrainssdeobras.com.br` | `127.0.0.1:3010` |
| `app.cifrainssdeobras.com.br` | `127.0.0.1:3011` (bloco só `http://`; o TLS é da Cloudflare) |

### DespolarizaMed

| Domínio | Destino |
| --- | --- |
| `despolarizamed.com.br` | `127.0.0.1:3100` |
| `www.despolarizamed.com.br` | `127.0.0.1:3100` |

### Mello Transportes

| Domínio | Destino |
| --- | --- |
| `mellotransportesriopreto.com.br` | `172.31.0.12:3000` (rede `edge`) |
| `www.mellotransportesriopreto.com.br` | redirect 301 → `mellotransportesriopreto.com.br` |

### Sites de cliente (estáticos)

| Domínio | Destino |
| --- | --- |
| `gabrielarincao.com.br` | estático `/var/www/gabrielarincao.com.br` |
| `www.gabrielarincao.com.br` | estático `/var/www/gabrielarincao.com.br` |
| `maprojetos.com.br` | estático `/var/www/maprojetos.com.br` |
| `www.maprojetos.com.br` | estático `/var/www/maprojetos.com.br` |
| `seteeseteengenharia.com.br` | estático `/var/www/seteeseteengenharia.com.br` |
| `www.seteeseteengenharia.com.br` | estático `/var/www/seteeseteengenharia.com.br` |

### Saíram do Caddy desde 16/08/2026

`wa.avilaops.com`, `cliente.avilaops.com`, `cliente.avila.inc`,
`entrar.avilaops.com`, `crm.avilaops.com`, `docs.avilaops.com`,
`irlquest.avilaops.com`, `minas.avilaops.com`, `poc.avilaops.com`,
`agricola.avilaops.com`, `api-agricola.avilaops.com`, `odoo.avilaops.com`,
`partsagricola.com.br`, `www.`, `api.` e `erp.partsagricola.com.br`,
`erp.brilhax.com`, `admin.saudepet.app.br` e `minas.comandeiro.com.br`.

### Destinos de deploy (`/etc/avilaops/deploy`, 19 `.conf` ativos)

| Arquivo | Modo | Alvo |
| --- | --- | --- |
| `app.avilaops.com.conf` | compose | `app-avilaops-app-1`, `/opt/app-avilaops` |
| `auth.avilaops.com.conf` | compose | `auth-avilaops-com-web`, `/opt/auth-avilaops` |
| `cifrainssdeobras.com.br.conf` | compose | `cifra-cifra-website-1`, `/opt/cifra` |
| `app.cifrainssdeobras.com.br.conf` | compose | `cifra-cifra-calculadora-1`, `/opt/cifra` |
| `despolarizamed.com.br.conf` | compose | `despolarizamed-web`, `/opt/despolarizamed` |
| `lojas.avilaops.com.conf` | compose | `lojas-avilaops`, `/opt/lojas` |
| `mellotransportesriopreto.com.br.conf` | compose | `mellotransportesriopreto-com-br-web`, `/opt/mellotransportesriopreto-com-br` |
| `saudepet.app.br.conf` | compose | `saudepet-web-1`, `/opt/saudepet` |
| `saudepet-backend.conf` | compose | `saudepet-backend-1`, `/opt/saudepet` |
| `saudepet-transcricao.conf` | compose | `saudepet-transcricao-1`, `/opt/saudepet` |
| `app.comandeiro.com.br.conf` | compose | `minas-espetinhos-app-1`, `/opt/minas-espetinhos` |
| `mail.avilaops.com.conf` | systemd | saúde em `127.0.0.1:3042` |
| `webmail.avilaops.com.conf` | systemd | saúde em `127.0.0.1:3041` |
| `pkvedacoes.avilaops.com.conf` | systemd | saúde em `127.0.0.1:3003` — **nada escutando**, unit `pkvedacoes-site` não existe |
| `avilaops.com.conf` | estático | `WEB_ROOT=/var/www/avila.inc` — o Caddy serve `/var/www/avilaops.com` e `/var/www/avila.inc` não existe |
| `gabrielarincao.com.br.conf` | estático | `/var/www/gabrielarincao.com.br` |
| `jobs.avilaops.com.conf` | estático | `/var/www/jobs.avilaops.com` |
| `maprojetos.com.br.conf` | estático | `/var/www/maprojetos.com.br` |
| `seteeseteengenharia.com.br.conf` | estático | `/var/www/seteeseteengenharia.com.br` |

No mesmo diretório há ainda `brilhax.com.conf.retirado-20260919`,
`entrar.avilaops.com.conf.retirado-20260919` e três `.conf.bak-20260916-160241`
(`lojas`, `mail`, `saudepet-backend`). O estado de cada deploy fica em
`/var/lib/avilaops/deploy/<aplicação>/` (18 diretórios).

---

## 3. Produção — containers (15 rodando, nenhum parado)

Conferido em 07/10/2026 11:34 UTC com `docker ps -a` (12 de aplicação, 2 de banco
e o `node_exporter`). Em 16/08/2026 eram 26
rodando e 2 parados.

### Aplicações web

| Container | Imagem | Porta no host |
| --- | --- | --- |
| `app-avilaops-app-1` | `avilaops-app:370a57f` | `127.0.0.1:3004` |
| `auth-avilaops-com-web` | `ghcr.io/avilaops/auth.avilaops.com:70dee0d` | rede `edge` `172.31.0.10:3010` |
| `mellotransportesriopreto-com-br-web` | por id (`2395ef84b3de`) | rede `edge` `172.31.0.12:3000` |
| `erp` | `erp:latest` | `127.0.0.1:3140` |
| `lojas-avilaops` | por id (`829e0a8c0101`) | `127.0.0.1:3080` |
| `despolarizamed-web` | por id (`17b1ec7e1004`) | `127.0.0.1:3100` |
| `saudepet-web-1` | por id (`a37746ceecb0`) | `127.0.0.1:3005` |
| `saudepet-backend-1` | por id (`ab03c6b463ff`) | interna `3000` |
| `saudepet-transcricao-1` | por id (`e95dd0f3c651`) | interna `8100` |
| `cifra-cifra-website-1` | por id (`67bbe90ed51d`) | `127.0.0.1:3010` |
| `cifra-cifra-calculadora-1` | por id (`40410e0001ed`) | `127.0.0.1:3011` |
| `minas-espetinhos-app-1` | por id (`ghcr.io/avilaops/app.comandeiro.com.br@sha256:bfe4aede…`) | `127.0.0.1:3040` |
| `evolution-avilaops-com-web` | `evoapicloud/evolution-api:v2.3.7` | rede `edge` `172.31.0.14:8080`, sem domínio e sem bloco no Caddy |

"Por id" é imagem fixada por digest pelo deploy (o `docker ps` mostra só o id).
Os três contêineres `cifra-*` foram recriados em 06/10/2026 (tarefa 146) e os
dois `minas-espetinhos-*` em 07/10/2026 (tarefa 153).

Atualização de 09/10/2026: entrou o `evolution-avilaops-com-web` (Evolution API,
gateway não-oficial de WhatsApp), em `/opt/evolution-avilaops-com`, com compose
versionado em `applications/evolution-avilaops-com/`. Só o n8n nativo (seção 4)
o chama, em `http://172.31.0.14:8080`, para publicar Status do WhatsApp; a
instância se chama `status`. Limite de 640 MB de memória (usava 212 MB logo
depois de subir). O `docker ps` dessa data mostra 18 contêineres rodando; a
contagem do título e as tabelas são as de 07/10/2026 e não foram refeitas.

### Bancos em container

| Container | Imagem | Porta |
| --- | --- | --- |
| `cifra-cifra-db-1` | `postgres:16-alpine` | interna `5432`, sem porta no host |
| `minas-espetinhos-db-1` | `postgres:18-alpine` | interna `5432`, sem porta no host |

### Monitoramento

| Container | Estado |
| --- | --- |
| `node_exporter` | no ar há 7 semanas, publicado em `10.10.0.2:9100` |

### Saíram desde 16/08/2026

`cliente-avilaops-com-cliente-avila-inc-1`, `entrar-avilaops-com-web`,
`agenda-crm-app`, `agenda-crm-db`, `minas-espetinhos-migrate-1`,
`minas-espetinhos-ifood-poller-1`, `irlquest-app-1`,
`irlquest-db-1`, `brilhax-site-1`, `brilhax-medusa-1`,
`brilhax-brilhax-builder-1`, `brilhax-postgres-1`, `brilhax-redis-1`, os três
`odoo-avilaops-*` e `cadvisor`. `app-avila-inc-app-1` virou
`app-avilaops-app-1`.

### Redes Docker

`edge` (`172.31.0.0/24`; IPs fixos em 09/10/2026: `.10` auth, `.11` tms, `.12`
mello, `.13` crm, `.14` evolution), `app-avilaops_default`, `cifra_default`,
`minas-espetinhos_default`,
`despolarizamed_default`, `saudepet_default` e as
padrão `bridge`, `host`, `none`. `erp`, `lojas-avilaops` e `node_exporter` estão
na `bridge`. A `n8n_default` (sem contêiner) foi removida em 08/10/2026 com as
sobras do n8n em container.

---

## 4. Produção — serviços fora do Docker

Conferido em 07/10/2026 com `systemctl list-units --type=service --state=running`.
As linhas `n8n` e `avila-n8n-modules` são de 08/10/2026 (tarefas 238 e 252,
conferidas com `systemctl is-active` e `ss -ltnp`); o restante da tabela não foi
refeito nessa data.

| Serviço | O que é | Onde |
| --- | --- | --- |
| `avila-mail-mta` | MTA: SMTP entrada 25, submission 587/465, IMAP/POP | `/opt/avila-mail` |
| `avila-mail-api` | API de e-mail, atrás de `mail.avilaops.com` | `127.0.0.1:3042` |
| `avila-webmail` | webmail de `mail.avilaops.com` (Next.js) | `/opt/avila-webmail` → `127.0.0.1:3041` |
| `avila-publicacoes-worker` | worker de publicações do app | `node /opt/app-avilaops/scripts/publicacoes-worker.mjs`, usuário `avila-publicacoes-worker` |
| `vedashow-sync` | recebe o cadastro do ERP da Vedashow e atualiza preço e estoque da loja | `python3 /opt/vedashow-sync/etl/receptor_erp.py` → `127.0.0.1:5191`, usuário `vedashow-sync` |
| `coturn` | TURN da videochamada do SaúdePet | `0.0.0.0:3478` tcp/udp, relay `49160–49200/udp` |
| `smbd` | Samba dos drives mapeados (seção 0) | `10.10.0.2:445` e `127.0.0.1:445` |
| `caddy` | proxy reverso de todos os domínios | `/usr/bin/caddy`, config em `/etc/caddy/Caddyfile` e `/etc/caddy/lojas.d/` |
| `postgresql@18-main` | Postgres do host | `127.0.0.1:5432`, `[::1]:5432` e `172.17.0.1:5432` |
| `redis-server` | Redis do host | `127.0.0.1:6379` |
| `cloudflared` | túnel Cloudflare | `127.0.0.1:20241` |
| `fail2ban` | bloqueio de força bruta | — |
| `n8n` | n8n nativo de `n8n.avilaops.com` (desde 08/10/2026) | `/opt/n8n` → `127.0.0.1:5678`; broker de runners em `127.0.0.1:5680` |
| `avila-n8n-modules` | módulos Ávila do n8n (Control Plane), atrás de `n8n.avilaops.com/avila*` | `/opt/n8n/modules` → `127.0.0.1:5679` (`LISTEN_HOST=127.0.0.1` na unit; desde a tarefa 252 o padrão do código também é `127.0.0.1`) |

Saíram desde 16/08/2026: `agricola-medusa`, `agricola-storefront` e
`jurisflow-poc` (sem unit rodando, sem diretório em `/opt`, portas `9002`,
`3006` e `3012` sem ouvinte). O `nginx` está instalado e `inactive`.

Units em falha: `avila-mail-cert-sync.service`, `cloud-init-hotplugd.service` e
`pkvedacoes-site.service` (unit não existe mais, `not-found`).

### Por que estão fora do Docker

**Serviço de e-mail — motivo técnico.** O MTA precisa das portas 25, 465, 587,
110, 143, 993 e 995 na interface pública com o IP de origem verdadeiro. Em
container, o `docker-proxy` reescreve a origem e o antispam passa a ver todo
remetente vindo da rede interna do Docker. É a exceção prevista na Norma de
Plataforma.

**Caddy — legado, e já previsto na Norma.** Roda no host, e é por isso que todo
app precisa publicar porta em `127.0.0.1` ou entrar na rede `edge`: o Caddy no
host não resolve nome de container.

**Postgres, Redis, cloudflared, fail2ban, coturn, Samba** — infraestrutura de
host.

---

## 5. Produção — bancos de dados

**A lista e os tamanhos dos bancos do cluster não foram reconferidos em
07/10/2026** (a conferência não abriu conexão com banco). O que foi relido é o
que a rotina de backup cobre (`/usr/local/bin/backup-todos-bancos.sh` e os
arquivos de `/opt/backups/db` da rodada de 06/10/2026).

### Postgres do host — bancos na rotina de backup (8)

| Banco | Usado por |
| --- | --- |
| `avila_mail` | servidor de e-mail |
| `avilaops-auth` | `auth.avilaops.com` |
| `cliente_portal` | `app.avilaops.com` + `auth.avilaops.com` |
| `erp` | `erp.avilaops.com` (primeiro dump em 06/10/2026) |
| `lojas` | `lojas.avilaops.com` e lojas com domínio próprio |
| `despolarizamed` | `despolarizamed.com.br` |
| `saudepet` | SaúdePet |
| `evolution_avilaops_com` | Evolution API (`evolution-avilaops-com-web`), schema `evolution_api`; criado em 09/10/2026 e incluído na rotina no mesmo dia (cópia do script em `/opt/backups/backup-todos-bancos.sh.bak-20261009-antes-evolution`) |
| `sorroche_app` | Sorroche — app desligado; banco mantido e ainda na rotina diária. Dump retido em `/opt/backups/retidos-sorroche-20261006/` |

O `pg_hba.conf` cita ainda um banco `migdolus` (regra própria para
`172.16.0.0/12`), que não está na rotina de backup. Os bancos de 16/08/2026 que
não aparecem mais na rotina (`agricola_medusa`, `partsagricola`, `avilaops`,
`brilhax`, `pkvedacoes_medusa`, `pkvedacoes_site`, `agricola`, `jurisflow_poc`)
não foram reconferidos: podem ter sido apagados ou só estar sem backup.

`cliente_portal` guarda a tabela `portal_clients`, que é a identidade
compartilhada entre `app.avilaops.com` e `auth.avilaops.com`.

### Postgres em container

| Banco | Container | Estado |
| --- | --- | --- |
| `cifra_calculadora` | `cifra-cifra-db-1` | no ar, com dump diário |
| `plataforma` | `minas-espetinhos-db-1` | no ar desde 07/10/2026 (restaurado do dump de 04/10), com dump diário; se o contêiner sumir, o script acusa falha |
| `medusa_store` | `brilhax-postgres-1` | contêiner removido de propósito em 16/09/2026; o script o ignora |

### Volumes Docker nomeados (5)

`cifra_cifra-pgdata` (recriado em 06/10/2026 a partir de dump),
`minas-espetinhos_db-data` e `minas-espetinhos_storage` (do Comandeiro, religado em
07/10/2026), `saudepet_transcricao-modelos` e `saudepet_uploads-data`. Conferido em
07/10/2026 com `docker volume ls`; há ainda um volume anônimo, criado em 07/10/2026 e
em uso pelo `minas-espetinhos-db-1`. Em 09/10/2026 entrou o
`evolution-avilaops-com-instances` (sessão do WhatsApp da Evolution: perder o
volume obriga a parear o número de novo).

---

## 6. Produção — agendamentos

Conferido em 07/10/2026 (`crontab -l`, `/etc/cron.d`, `systemctl list-timers`).

### Crontab do root

```cron
0 3 * * *     /bin/bash /opt/saudepet/backup.sh
0 * * * *     /opt/odoo-avilaops/vitrine/atualizar.py partsagricola        ← /opt/odoo-avilaops NÃO EXISTE
*/5 * * * *   /bin/bash /opt/saudepet/monitor.sh
*/15 * * * *  /opt/odoo-avilaops/integracao-partsagricola/vigia.sh         ← /opt/odoo-avilaops NÃO EXISTE
```

As duas linhas do `odoo-avilaops` carregam antes `/etc/avilaops/tokens.env`.

### `/etc/cron.d`

```cron
20 3 * * *     /opt/avila-rclone/sync-drive.sh
30 3 * * *     /usr/local/bin/backup-todos-bancos.sh
40 3 * * *     /opt/minas-espetinhos/scripts/backup.sh
0  4 * * *     /usr/local/bin/sync-r2.sh
10 4 * * 0     /opt/minas-espetinhos/scripts/backup.sh --check
*/5 * * * *    /opt/minas-espetinhos/scripts/health-check.sh
```

O `health-check.sh` é chamado por `/etc/cron.d/minas-espetinhos-health` (root), não
pelo crontab do root. Desde 07/10/2026 20:17 UTC (tarefa 207) a cópia instalada é
idêntica à de `scripts/health-check.sh` na `main` do `app.comandeiro.com.br`
(commit `6059e64`). O push não atualiza essa cópia: é cópia manual da ops. Cópia
anterior em `/opt/backups/minas-health-check.sh.bak-20261007-t207` (movida de
`/opt/minas-espetinhos/scripts` em 08/10/2026, tarefa 210, para ficar fora do alcance
do `rsync --delete` do `scripts/deploy.sh` antigo).

Mais os do sistema (`e2scrub_all`, `php`).

### Timers do systemd (próprios)

`lojas-caddy-sync.timer`, `avila-monitoring.timer`, `avila-mail-tls.timer`,
`avila-mail-cert-sync.timer`, `avilaops-rotacionar-backups.timer` e
`cloudflared-update.timer`.

O `backup-todos-bancos.sh` é o que cobre o parque: faz `pg_dump` dos 8 bancos
do host listados na seção 5 **e** dos bancos em container, gravando em
`/opt/backups/db/` (84 arquivos, 705 MB em 07/10/2026). O `sync-r2.sh` manda
para o R2 da Cloudflare às 4h, e o `sync-drive.sh` para o Google Drive.

Toda rodada termina com a linha `saida=N` em `/var/log/backup-bancos.log`
(gravada pelo próprio script, inclusive quando ele morre no meio). O vigia de
saúde do `creators` (`rotinas-openclaw/vigia_saude.py`) lê esse log por SSH a
cada 15 minutos e abre a tarefa `vigia:backup-applications` para a raia `ops`
quando a saída é diferente de 0, quando não há rodada há mais de 26 horas ou
quando o próprio log fica mais de 26 horas sem ser lido (SSH sem resposta).

O `/opt/avilaops-scripts/rotacionar-backups.sh` (timer das 03:40) preserva o
arquivo mais recente **com conteúdo** de cada família: maior que os 20 bytes
de um gzip vazio e, se for `.gz`, aprovado no `gzip -t` (tarefa 172; só o
tamanho deixava um gzip truncado tomar o lugar do dump bom). Até 07/10/2026
preservava o mais recente pela data, mesmo vazio, e apagava os dumps bons da
família aos 7 dias. Só o escolhido de cada família passa pelo `gzip -t`.
Cópia versionada e teste em `applications/` deste repositório (tarefa 186).

**Família `pre-migracao-*` (08/10/2026, tarefa 241).** O `/usr/local/sbin/avila-deploy-local`
(cópia de `scripts/deploy-container-local.sh`; desde 08/10/2026 19:10 UTC a do commit
`fbba3e0`, tarefa 247, sha256 `ff66f321…d7fbd`, antes a do `61cd0c1`) faz `pg_dump` do banco
antes de aplicar migração pendente, para os destinos cujo `.conf` define
`MIGRATE_DUMP_DB`. Hoje só o `lojas.avilaops.com.conf` define (`MIGRATE_DUMP_DB=lojas`).
O arquivo sai em `/opt/backups/db/pre-migracao-<aplicação>-AAAAMMDD-HHMMSS.sql.gz`, root,
modo 600, e só depois de conferido (`gzip -t` e marcador de fim do `pg_dump`) a migração
roda; dump que falha para o deploy sem migrar. Sem migração pendente não há dump.

- Tamanho medido em 08/10/2026: o dump diário `host-lojas-20261008.sql.gz` tem 5,9 MB
  (banco `lojas` com 239 MB no Postgres 18 do host); cada `pre-migracao-lojas…` deve ter o
  mesmo tamanho. `/opt/backups/db` tinha 85 arquivos, 772 MiB, e o disco 11 GB livres (72%).
- Retenção: não há rotina nova. O `rotacionar-backups.sh` trata `pre-migracao-<aplicação>`
  como uma família: o mais recente com conteúdo fica, os outros saem com mais de 7 dias.
  Não há teto por quantidade: cada deploy que encontra pendência gera um dump, inclusive
  tentativa repetida.
- Saída do servidor: **os `pre-migracao-*` não saem do servidor** (tarefa 247, instalado em
  08/10/2026 19:10 UTC). O `/usr/local/bin/sync-r2.sh` (cópia de `applications/sync-r2.sh`,
  sha256 `c473dfac…8b7e6`) copia `*.sql.gz` e `*.tar.gz` de `/opt/backups/db` para o R2 às 4h
  com `rclone copy` e o filtro `- pre-migracao-*` na frente. Motivo: o script só copia e o
  bucket não expira nada, então cada dump ficaria no R2 para sempre. Provado com o rclone
  1.75 do servidor, só por listagem: em pasta temporária o filtro novo tira os
  `pre-migracao-*` (raiz e subpasta) e mantém os outros; em `/opt/backups/db` as duas
  listagens são iguais (82 arquivos). Nenhum objeto foi apagado do R2. O `sync-drive.sh` citado acima não foi encontrado em
  08/10/2026: `/opt/avila-rclone` não existe e nenhum arquivo de `/etc/cron.d` cita `drive`.
- Pendência: o `/usr/local/sbin/avila-deploy` (`scripts/deploy-container.sh`, usado pelo
  GitHub Actions) **não foi atualizado** e continua na versão de 18/09/2026, sem o dump.
  **Correção de 08/10/2026 (tarefa 247): o Actions não está parado.** O `journalctl` do
  `applications` mostra 114 execuções do `avila-deploy` pelo `gha-deploy` de 01/10 a 08/10,
  39 do `lojas.avilaops.com` (8 em 08/10, a última às 17:10 UTC, `image.yml` por digest do
  GHCR). Enquanto o `lojas` for publicado pelo Actions, a migração dele roda **sem** dump:
  nenhum `pre-migracao-*` existe em `/opt/backups/db` nem no R2. A versão do repositório tem
  131 linhas a mais que a instalada e nunca rodou em produção; instalar é tarefa própria, com
  revisão do que mudou desde 18/09 (comandos no `BUILD-MANUAL.md`).
- Desde a tarefa 247 o `avila-deploy-local` confere, em todo deploy de destino com
  `MIGRATE_DUMP_DB`, se o banco da URL de migração é o mesmo; diferente, para antes de
  migrar e de trocar o container. A URL vai ao Prisma por ambiente, não pela linha de comando.
- Volta: `/opt/backups/avila-deploy-local.bak-20261008-t247` (versão da 241, sha256
  `e95b19e6…4806e`) e `/opt/backups/sync-r2.sh.bak-20261008-t247` (sha256 `90d0e346…0bf1a`),
  sempre por troca atômica e sem deploy nem sync em curso (comandos em
  `applications/README.md`). Da 241: `/opt/backups/avila-deploy-local.bak-20261008-t241` e
  `/opt/backups/lojas.avilaops.com.conf.bak-20261008-t241`. Desligar só o dump: apagar a
  linha `MIGRATE_DUMP_DB=lojas` do `.conf`.

### Script manual que apaga: `limpeza-fase1.sh`, arquivado em `/opt/arquivo` (tarefas 236, 249 e 264, 08 e 09/10/2026)

**Estado desde a tarefa 249 (08/10/2026 19:33 UTC):** o script saiu de `/root` e está
arquivado em **`/opt/arquivo/limpeza-fase1-20261008.sh`**, modo 600 (sem bit de execução),
dono root, sha256 `f2d0c3e2…cb7c59` (igual antes e depois de mover; o conteúdo não foi
editado). `/root/limpeza-fase1.sh` não existe mais. Não foi rodado nem apagado.

Não está agendado: não está em cron, timer nem unit, e nenhum outro script o chama
(conferido em 08/10/2026, antes de mover, com `crontab -l` do root, `grep` em `/etc/cron*`,
`/var/spool/cron`, `/etc/systemd`, `/usr/lib/systemd/system`, `/usr/local/bin`,
`/usr/local/sbin`, `/root` e `/opt`, `systemctl list-timers --all` e `atq`). Rodou uma vez,
em 27/08/2026 14:27 UTC (`/var/log/limpeza-avilaops.log`); os alvos de caminho fixo dele já
não existem. **Não é para rodar de novo: rodado à mão (`bash /opt/arquivo/limpeza-fase1-20261008.sh`)
ele ainda apaga** as 14 cópias de retorno listadas em "Segue no alcance", mais caches e logs
rotacionados. Tirar o bit de execução e mudar o caminho só evita a execução por engano.

- Alcance do trecho ".env e Caddyfile de sobra": `find /opt /root /var/www -maxdepth 4` por
  `.env.bak*`, `.env.*.bak*`, `.env.backup-*`, `.env.*.before-*`, `.env.quebrado.bak`,
  `Caddyfile.bak-*`, `docker-compose.yml.bak*`, `docker-compose.yml.backup*` e `*.bak-1786*`.
  Até 08/10/2026 isso incluía `/opt/backups`: 12 cópias de retorno sairiam (10
  `Caddyfile.bak-*`, `docker-compose.yml.bak-20260827-remove-fiscalteste` e
  `.env.bak-20260827-remove-fiscalteste`).
- O que mudou: esse `find` ganhou `\( -path /opt/backups -prune \) -o` e `-print` explícito,
  então não desce mais em `/opt/backups`. Ensaio só de listagem depois da mudança: 14
  arquivos, nenhum em `/opt/backups`. Nada foi apagado e o script não foi executado.
- A contagem "restantes" no fim do script (linha 73) ainda desce em `/opt/backups`, mas só
  imprime um número (`find … | wc -l`). Com o script arquivado, não foi corrigida.
- Versão anterior à 236: desde a tarefa 264 (09/10/2026 07:46 UTC) está em
  **`/opt/arquivo/limpeza-fase1-antes-t236-20261008.sh`**, modo 600 (sem bit de execução),
  dono root, 4400 bytes, sha256
  `71b2a6d012f05bfcb669b1d4c77b7bfbccd25c70b8915133859f31e304965ce0` (igual antes e depois
  de mover; não foi rodada, editada nem apagada). `/root/limpeza-fase1.sh.antes-t236-20261008`
  não existe mais, e não sobra script de limpeza em `/root`. **É a versão que ainda desce em
  `/opt/backups`** e não deve voltar ao uso.
- Volta da 264: `mv /opt/arquivo/limpeza-fase1-antes-t236-20261008.sh
  /root/limpeza-fase1.sh.antes-t236-20261008` (o modo 600 fica como está; não há motivo
  previsto para fazer isso).
- Volta da 249: `mv /opt/arquivo/limpeza-fase1-20261008.sh /root/limpeza-fase1.sh && chmod 755
  /root/limpeza-fase1.sh` (não há motivo previsto para fazer isso).
- **Segue no alcance** (14 arquivos no ensaio da 236; não mexidos): as cópias `.env*.bak*` e
  `docker-compose.yml.bak*` ao lado dos serviços (`/opt/cifra`, `/opt/minas-espetinhos`,
  `/opt/erp`, `/opt/auth-avilaops`, `/opt/tms-avilaops-com`, `/opt/app-avilaops`), o
  `Caddyfile.bak-20261006-t148-…` de `/opt/arquivo/sorroche-20261006/` e os dois
  `/root/Caddyfile.bak-20260910*`. O script também apaga `/root/.npm`, `/root/.cache`, as
  listas do apt e logs rotacionados de `/var/log`.

---

## 7. Produção — rede

### Firewall (ufw, ativo) — conferido em 07/10/2026

| Porta | Origem |
| --- | --- |
| 22/tcp | qualquer |
| 80/tcp, 443/tcp | qualquer |
| 443/udp | qualquer (HTTP/3 do Caddy) |
| 25/tcp, 465/tcp, 587/tcp | qualquer (SMTP) |
| 110/tcp, 143/tcp, 993/tcp, 995/tcp | qualquer (POP/IMAP) |
| 3478/tcp, 3478/udp, 49160:49200/udp | qualquer (TURN do SaúdePet) |
| 51820/udp | qualquer (WireGuard) |
| 445/tcp | só na `wg0` |
| 5432 | `172.16.0.0/12` e `127.0.0.1` |

A regra do 5432 é o que permite container falar com o Postgres do host sem
expor o banco à internet. É também por isso que a rede `edge` foi fixada em
`172.31.0.0/24` — fora dessa faixa, o ufw bloquearia.

### Portas em escuta — `ss -ltn` em 07/10/2026 01:49 UTC

Públicas (TCP): `22`, `25`, `80`, `110`, `143`, `443`, `465`, `587`, `993`,
`995`, `3478`. UDP: `443`, `3478`, `51820`.

Só em `127.0.0.1`: `445` (Samba), `2019` (admin do Caddy), `3004`, `3005`,
`3010`, `3011`, `3041`, `3042`, `3080`, `3100`, `3140`, `5191`, `5432`, `6379`,
`20241`.

Só no túnel: `10.10.0.2:445` (Samba) e `10.10.0.2:9100` (node_exporter).

Na ponte do Docker: `172.17.0.1:5432` (Postgres do host para os contêineres).

Atualização de 08/10/2026 (tarefa 238): o n8n nativo acrescentou `5678`, `5679`
e `5680`, as três só em `127.0.0.1`. A `5679` (`avila-n8n-modules`) escutava em
`0.0.0.0` desde a instalação, fechada apenas pelo ufw; passou para `127.0.0.1`
com `LISTEN_HOST=127.0.0.1` na unit e o `listen` do `src/server.ts` lendo a
variável (repositório `avilaops/n8n.avilaops.com`). Cópias de antes em
`/opt/backups/`: `avila-n8n-modules.service.bak-20261008-t238`,
`n8n-modules-dist-server.js.bak-20261008-t238` e
`n8n-modules-src-server.ts.bak-20261008-t238`.

---

## 8. Produção — `/opt` e `/var/www`

Conferido em 07/10/2026 com `ls` e `du -sh`.

### `/opt` (20 diretórios)

| Diretório | Tamanho |
| --- | --- |
| `arquivo` | 997 MB (`sorroche-20261006`, arquivado em 2026-10-06) |
| `backups` | 705 MB só em `backups/db` |
| `lojas` | 618 MB |
| `saudepet` | 592 MB |
| `google` | 435 MB |
| `app-avilaops` | 323 MB |
| `despolarizamed` | 110 MB |
| `cifra` | 51 MB |
| `n8n` | 2,9 GB em 08/10/2026: **em uso** desde essa data pelo n8n nativo (`app`, `modules`, `override`, `arquivos`, `n8n.env`; seção 4). Em 07/10/2026 eram 16 MB do n8n em container, parado desde 19/09/2026; essas sobras e o `LEIA-ANTES-DE-SUBIR.txt` foram removidos |
| `minas-espetinhos` | 16 MB (Comandeiro, religado em 07/10/2026) |
| `erp` | 12 MB |
| `auth-avilaops` | 1,0 MB |
| `vedashow-sync` | 32 KB |
| `mello` | 12 KB |
| `containerd` | 12 KB |
| `avilaops-scripts` | 12 KB |
| `mellotransportesriopreto-com-br` | 8 KB |
| `evolution-avilaops-com` | 16 KB em 09/10/2026 (`docker-compose.yml`, `.env`, `.env.example`) |
| `avila-rclone` | 8 KB |
| `avila-webmail` | link |
| `avila-mail` | link |

### `/var/www`

Os sites estáticos publicados pelo deploy são links para
`/var/www/.releases/<site>/<versão>`.

| Diretório | Tamanho | Observação |
| --- | --- | --- |
| `cifra` | **1006 MB** | maior item |
| `comandeiro.com.br` | 1,6 MB | em uso |
| `comandeiro.com` | 508 KB | em uso |
| `adminer` | 508 KB | ferramenta de banco (compartilhada no Samba) |
| `saudepet-landing` | 24 KB | — |
| `acme` | 12 KB | — |
| `html` | 8 KB | padrão |
| `avilaops.com` | link | em uso |
| `gabrielarincao.com.br` | link | em uso |
| `jobs.avilaops.com` | link | em uso |
| `maprojetos.com.br` | link | em uso |
| `seteeseteengenharia.com.br` | link | em uso |

As cópias antigas (`*.bak-*`, `*.old`) de 16/08/2026 não existem mais.

---

## 9. Infra — `23.88.60.193`

**Não reconferido em 07/10/2026: não há acesso SSH a este servidor a partir da
equipe.** O que se viu de fora nessa data: sem resposta a ping e a `22/tcp`;
último handshake da WireGuard com a produção há 29 dias; `obs.avilaops.com` sem
registro DNS; `n8n.avilaops.com` apontava para `204.168.249.111` (outro servidor)
e respondia 502. Desde 08/10/2026 `n8n.avilaops.com` resolve para
`178.105.82.48` (produção) e é atendido pelo n8n nativo de lá (seções 2 e 4);
não depende mais deste servidor. Tudo abaixo é o levantamento de 16/08/2026 e pode não existir
mais.

### Containers (10 rodando, 1 parado)

**Observabilidade**

| Container | Imagem | Portas |
| --- | --- | --- |
| `observabilidade-caddy-1` | `caddy:2-alpine` | `0.0.0.0:80`, `0.0.0.0:443` |
| `observabilidade-grafana-1` | `grafana/grafana:latest` | interna `3000` |
| `observabilidade-prometheus-1` | `prom/prometheus:latest` | interna `9090` |
| `observabilidade-loki-1` | `grafana/loki:latest` | `10.10.0.1:3100` |
| `observabilidade-alertmanager-1` | `prom/alertmanager:latest` | interna `9093` |
| `observabilidade-node_exporter-1` | `prom/node-exporter:latest` | interna `9100` |
| `observabilidade-blackbox_exporter-1` | `prom/blackbox-exporter:latest` | interna `9115` |
| `observabilidade-promtail-obs-1` | `grafana/promtail:latest` | — |

**Automação**

| Container | Imagem |
| --- | --- |
| `n8n-n8n-1` | `docker.n8n.io/n8nio/n8n:latest` |
| `n8n-postgres-1` | `postgres:16-alpine` (banco `n8n`, 16 MB) |

Parado: `observabilidade-alertmanager-config-init-1` (job de inicialização,
saída 0 — esperado).

### Domínios

O Caddy deste servidor usa variáveis: `{$OBS_DOMAIN}` e `{$N8N_DOMAIN}` —
resolviam para `obs.avilaops.com` e `n8n.avilaops.com`.

### Serviços, rede, agendamentos

Fora do Docker só o essencial: `docker`, `containerd`, `fail2ban`, `atd`.

Firewall: `22`, `80`, `443`, `51820/udp`.

**Crontab vazio.**

### `/opt`

`avila-sync`, `backups`, `n8n`, `observabilidade`, `containerd`.

Volumes: `n8n_caddy-config`, `n8n_caddy-data`, `n8n_n8n-data`,
`n8n_postgres-data`, `observabilidade_alertmanager-config`,
`observabilidade_alertmanager-data`, `observabilidade_caddy-config`,
`observabilidade_caddy-data`, `observabilidade_grafana-data`,
`observabilidade_loki-data`, `observabilidade_prometheus-data`.

---

## 10. Problemas encontrados (produção, 07/10/2026)

### Quebrados

1. **Caddy aponta para porta sem ouvinte.** `mail-mcp.avilaops.com` vai para
   `127.0.0.1:8790` e responde 502. (Os endereços do Comandeiro, que iam para a
   `3040` sem ouvinte, voltaram a responder 200 em 07/10/2026.)

2. **Crons que chamam o que não existe.** Duas linhas do crontab do root chamam
   scripts em `/opt/odoo-avilaops`, que não existe mais (a cada hora e a cada 15
   minutos). (Os dois crons do minas-espetinhos voltaram a ter alvo em
   07/10/2026; o `health-check.sh` passou a consultar
   `brasa.comandeiro.com.br`, porque `minas.comandeiro.com.br` não responde
   desde 03/09 e o vigia estava preso em "fora do ar".)

3. **Três units em falha:** `avila-mail-cert-sync.service` (copia o certificado
   renovado para o MTA), `cloud-init-hotplugd.service` e
   `pkvedacoes-site.service` (unit removida).

4. **Destinos de deploy sem alvo:** `app.comandeiro.com.br.conf` (contêiner
   removido), `pkvedacoes.avilaops.com.conf` (unit não existe) e
   `avilaops.com.conf`, cujo `WEB_ROOT` é `/var/www/avila.inc`, diretório que
   não existe (o Caddy serve `/var/www/avilaops.com`).

### Riscos

5. **Sete senhas que estiveram neste arquivo seguem no histórico do git.** A do
   Samba dá leitura e escrita em `/` da produção para quem estiver no túnel e
   **continua valendo** (não trocada na tarefa 161). Das seis de Postgres, as duas
   em uso (`cifra` e `postgres` do `plataforma`) foram trocadas em 07/10/2026,
   três não têm mais role (`mello`, `agenda_crm`, `medusa`) e a do `n8n` do infra
   está pendente; o estado de cada uma está na coluna "Exposta no histórico do
   git?" da tabela da seção 0. As outras linhas daquela tabela nunca tiveram valor
   neste arquivo.

6. **`pg_hba.conf` com `trust` em `127.0.0.1`, `::1` e socket local.** Qualquer
   processo com shell na máquina vira superusuário do Postgres sem credencial.
   Trocar para `scram-sha-256` exige definir senha nas roles e testar cada
   aplicação.

7. **`/opt/cifra/docker-compose.yml` define `POSTGRES_PASSWORD` e
   `DATABASE_URL`.** O arquivo saiu do modo `666` para `600` em 07/10/2026
   (tarefa 161) e deixou de guardar a senha no mesmo dia (tarefa 197): as duas
   variáveis usam `${DB_PASSWORD:?defina DB_PASSWORD}` e o valor fica em
   `/opt/cifra/.env` (modo `600`). **Os dois deploys do cifra (site e
   calculadora) dependem dessa linha.** Sem `DB_PASSWORD` no `.env`, o
   `docker compose config` falha e o `avila-deploy` para antes de trocar o
   serviço; o `rollback` dele falha pelo mesmo motivo e imprime "Falha ao
   restaurar o servico. Intervencao necessaria.", mas nenhum contêiner foi
   tocado e o serviço segue no ar com a imagem antiga. Nesse caso, como root
   no `applications`: (1) confirmar que a linha falta
   (`grep -c '^DB_PASSWORD=' /opt/cifra/.env` dá `0`) e que os três
   contêineres `cifra-*` seguem de pé, com `127.0.0.1:3010/` e
   `127.0.0.1:3011/api/health` em 200; (2) repor a linha com o valor em uso,
   que está na `DATABASE_URL` do `cifra-cifra-calculadora-1`
   (`docker inspect`), copiando dentro do servidor sem imprimir e sem gerar
   senha nova, e manter o `.env` em `600`; (3) conferir
   `docker compose --project-directory /opt/cifra -p cifra -f /opt/cifra/docker-compose.yml -f /var/lib/avilaops/deploy/<aplicação>/image.yml config -q`
   com saída 0; (4) reexecutar o deploy. Não precisa de `up` manual, e `up`
   sem `--no-deps` recriaria o banco. O comando completo do passo 2 está na
   seção 7 do `AGENTS.md` do servidor de agentes (tarefa 208). A cópia
   `docker-compose.yml.bak-20261007-t197` (modo `600`) ainda guarda a senha em
   uso; ela e a `.env.bak-20261007-t197` só saem depois de um deploy
   bem-sucedido do cifra posterior a 07/10/2026 20:30 UTC (até 08/10/2026 não
   houve). O `cifra-cifra-db-1` foi criado com o
   `POSTGRES_PASSWORD` antigo no ambiente e será recriado no próximo `up` que
   inclua o serviço `cifra-db` (o volume fica). O diretório `/opt/cifra` e os demais arquivos dele, que eram
   graváveis por qualquer usuário (`777`/`666`), perderam a escrita de "outros"
   em 07/10/2026 (tarefa 180, `chmod -R o-w`): diretórios `775`/`755`, arquivos
   `664`/`644`/`600`, donos inalterados. Só `root` grava ali (deploy por `sudo` e
   Samba com `force user = root`); nenhum contêiner monta pasta de `/opt/cifra`.
   Restam 182 arquivos de `calculadora/` com dono `197609`, uid que não existe no
   servidor.

8. **Swap em 2,3 GB na produção**, com 3,7 GB de RAM e 15 contêineres
   (reconferido em 07/10/2026 12:04 UTC: 15 rodando, nenhum parado; swap em 2,2 GB).

9. **Servidor infra sem sinal** (seção 9): observabilidade e alertas podem
   estar fora; o `node_exporter` da produção segue publicado em
   `10.10.0.2:9100` sem que se saiba quem coleta.

### Sujeira

10. **`/var/www/cifra` com 1006 MB** e `/opt/mello`; arquivos
    `.retirado-20260919` e `.bak-20260916-160241` em `/etc/avilaops/deploy`.
    O `/opt/n8n` e a rede `n8n_default` saíram desta lista em 08/10/2026: a
    pasta está em uso pelo n8n nativo e a rede foi removida.

### Resolvidos desde 16/08/2026

`/opt/saudepet/backup.sh` agora existe; o `health_check.sh` do portal (que
tinha a chave do Resend embutida) saiu do crontab junto com
`/opt/cliente-avilaops-com`; o `cadvisor` foi removido; as cópias antigas de
`/var/www` e do webmail em `/opt` foram apagadas; o disco do Docker passou de
40 GB (75%) para 79 GB (62%).
