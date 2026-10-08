# Análise da infraestrutura — 26/08/2026

> **Documento descartável.** Gerado para leitura e análise. Pode apagar depois.
> Os dados vêm da coleta automática de `levantamento/saida/2026-08-26_0354/`
> (três hosts, modo rápido, 230s). Nenhuma senha aparece aqui — só onde ela está.
>
> **Atualização de 07/10/2026 (Sorroche).** Os números abaixo são o retrato de 26/08.
> Depois disso o Sorroche saiu do Hetzner: `sorroche.beauty` está no GitHub Pages,
> o `app.sorroche.beauty` foi desligado em 16/09/2026 (contêineres `sorroche-web` e
> `sorroche-app` removidos) e `/opt/sorroche` foi arquivado em
> `/opt/arquivo/sorroche-20261006/` no servidor `applications`. O banco
> `sorroche_app` foi mantido. As menções ao Sorroche estão anotadas no próprio trecho.
> Estado atual: `INVENTARIO-SERVIDORES.md`.

---

## Panorama

| | **local** | **producao** | **infra** |
|---|---|---|---|
| Nome | `HP` | `ubuntu-4gb-nbg1-1` | `ubuntu-4gb-nbg1-2` |
| Endereço | notebook | `178.105.82.48` · wg `10.10.0.2` | `23.88.60.193` · wg `10.10.0.1` |
| Sistema | Windows 11 Home 26200 | Ubuntu 26.04 LTS | Ubuntu 26.04 LTS |
| CPU | i5-1334U, 12 threads | Xeon Skylake | Xeon Skylake |
| RAM | 7,7 GB — **1,8 GB livre** | 3,8 GB — 1,2 GB livre | 3,8 GB — 2,0 GB livre |
| Disco | C: **95,4%** · D: 73,8% | / 56% · **Docker 87%** | / 56% |
| Arquivos `.env` | 117 | **134** | 5 |
| Hostnames servidos | — | 43 | 2 |
| Bancos PostgreSQL | 22 | 15 host + 6 container | 2 container |
| Repositórios git | 51 | 2 | 1 |
| Containers | — | 27 | 11 |

Papel de cada um: **producao** roda tudo que é cliente; **infra** roda observabilidade
(Grafana, Prometheus, Loki, Alertmanager) e n8n; **local** é onde o código é escrito.
Os três estão ligados pela WireGuard `10.10.0.0/24`.

---

## Alertas, do mais grave ao menos

### 1. Disco do Docker em produção: 87%, restam 5,0 GB

```
/dev/sdb   40G   33G usados   5,0G livres   87%
```

É o disco que já derrubou o `app.avilaops.com` uma vez, quando um build encheu
o volume no meio de um `docker compose up`. Estava em 80% de manhã.

A composição mudou de forma relevante:

| | Antes (hoje cedo) | Agora |
|---|---|---|
| Imagens | 23,94 GB | 17,91 GB |
| Cache de build | 6,72 GB | 2,43 GB |
| **Volumes** | 441 MB | **13,44 GB** |

Imagens e cache diminuíram — alguém limpou, ou eu limpei. Mas os **volumes
saltaram de 441 MB para 13,44 GB**. Isso não é build: é dado. Vale descobrir
qual volume cresceu antes de mexer em qualquer coisa, porque limpar volume
apaga dado de verdade.

### 2. Disco C: do notebook em 95,4% — restam 17 GB de 375 GB

O culpado é um só:

```
C:\Users\nicol\AppData\Local\Docker\wsl\disk\docker_data.vhdx    53,34 GB
```

E o Docker Desktop **está parado** (`com.docker.service = Stopped`, distro
`docker-desktop` parada). São 53 GB de disco virtual ocioso.

Depois dele, caches puramente descartáveis: `npm-cache` 20,67 GB,
`.gradle` 4,89 GB, `Yarn` 4,20 GB, `pnpm-cache` 1,01 GB. Somam ~31 GB.

Recuperável sem perder nada: **~84 GB**, que levaria o C: de 95% para ~73%.

### 3. Segredos de produção em texto claro

| Host | Tipo | Ocorrências |
|---|---|---|
| local | **Stripe secreta de produção** (`sk_live_`) | **2** |
| local | chave privada | 4 |
| local | GitHub | 20 |
| local | Google API | 18 |
| local | Resend | 7 |
| producao | GitHub | 22 |
| producao | Google API | 22 |
| producao | Resend | 13 |
| producao | chave privada | 7 |
| infra | chave privada | 1 |
| infra | Resend | 2 |

A chave `sk_live_` da Stripe permite criar cobrança, emitir reembolso e ler
dados de cliente. Está num arquivo `.env.bak-dominio-20260814-074638` esquecido.
**Rotacionar é o único remédio** — apagar o arquivo não invalida a chave.

As chaves privadas em produção incluem um `GOOGLE_SERVICE_ACCOUNT_JSON` completo
dentro de `/opt/backups/producao/app-sso-20260815-034138/.env.production`.

Os caminhos exatos e as linhas estão em `levantamento/saida/.../RELATORIO.md`,
seção "Segredos em texto claro" de cada host.

### 4. Noventa e seis arquivos `.env` de produção com permissão frouxa

| Modo | Qtd | O que significa |
|---|---|---|
| **666** | **48** | qualquer usuário do sistema **lê e escreve** |
| 644 | 37 | qualquer usuário lê |
| 664 | 10 | grupo escreve |
| 640 | 1 | grupo lê |

Os 48 em `666` são o problema: quem tiver qualquer conta na máquina pode trocar
a `DATABASE_URL` e redirecionar a aplicação para um banco sob seu controle.

Concentração: `agenda-crm` tem **68** arquivos `.env` (por causa de 8 pastas em
`releases/`, cada uma com 7), `brilhax-stack` 16, `saudepet` 14, `cifra` 8.

Mais **22 sobras** (`.env.bak-*`, `.env.production.before-*`, `.env.quebrado.bak`)
que só existem para vazar.

### 5. Postgres com `trust` no loopback

```
host   all   all   127.0.0.1   trust
```

Qualquer processo que consiga um shell na produção vira **superusuário do
PostgreSQL sem apresentar credencial**. É o que faz o pgAdmin conectar sem
senha — conveniente, e exatamente por isso perigoso.

Além disso há **dois superusuários**: `postgres` e `app_avilaops`. O segundo
não aparece em nenhum `.env` — é superusuário órfão.

Três roles com nome de hash (`_60684f45ce613f14`, `_70d2bc077bdfcc7c`,
`_e89d779f7a54d976`) sobraram de projetos que não existem mais. O `wabusiness`
aponta para um banco `_60684f45ce613f14` que **não existe**, e o container
também não existe.

### 6. Sistema desatualizado nos dois servidores

| | Atualizações pendentes | Reinício |
|---|---|---|
| producao | **34** | **pendente** |
| infra | 13 | **pendente** |

Reinício pendente significa kernel novo instalado e o antigo ainda rodando —
as correções de segurança não estão em vigor.

### 7. `fail2ban` protege só o SSH

Uma jail apenas (`sshd`) nos dois servidores. As portas 80/443 do Caddy e o
MTA em 25/587 ficam sem proteção contra força bruta. Para um servidor que
hospeda e-mail, isso é convite a ataque de dicionário.

### 8. Trabalho que existe em um lugar só

**No notebook — 82 commits nunca enviados:**

| Repositório | Commits |
|---|---|
| `partsagricola.com.br` | **48** |
| `minas.comandeiro.com.br` | 20 |
| `mail.avilaops.com` | 12 |
| `cifrainssdeobras.com.br/website` | 4 |
| `cliente.avilaops.com` | 1 |
| `app.avilaops.com` | 1 |

**Nos servidores — 199 commits nunca enviados:**

| Repositório | Commits |
|---|---|
| `/opt/saudepet` | **178** |
| `/opt/sorroche` (desde 06/10/2026 em `/opt/arquivo/sorroche-20261006/opt-sorroche`) | 21 |

Esses 199 são o caso mais grave: história de git que existe **apenas dentro da
máquina de produção**. Não estão no GitHub e não entram no backup de banco.
Se aquele servidor for embora, some. (07/10/2026: os 21 do Sorroche continuam
só naquele disco, agora dentro do diretório de arquivo.)

### 9. Certificados vencidos em disco

```
api-pkvedacoes.avilaops.com   -14 dias   (expirado)
intermediate                   -9 dias   (expirado)
```

O `api-pkvedacoes` é resíduo: o domínio já saiu do Caddy e os bancos
`pkvedacoes_*` também sumiram. Não quebra nada, mas suja o diretório.

### 10. Dezessete containers sem healthcheck

```
agenda-crm-app, lojas-avilaops, app-avila-inc-app-1, mello-app, mello-db,
cifra-cifra-website-1, odoo-avilaops-partsagricola-1, odoo-avilaops-avilaops-1,
odoo-avilaops-recorte-1, brilhax-brilhax-builder-1, sorroche-web,
brilhax-medusa-1, sorroche-app, node_exporter, cifra-cifra-calculadora-1,
cifra-cifra-db-1, brilhax-site-1
```

(07/10/2026: `sorroche-web` e `sorroche-app` não existem mais; o app foi
desligado em 16/09/2026.)

Sem healthcheck o Docker não sabe que a aplicação travou — o container continua
"Up" com a porta aberta e devolvendo erro. A Norma de Plataforma AvilaOps exige
healthcheck; esses 17 estão fora da norma.

Quatro rodam com tag `:latest` (`lojas-avilaops`, `mello-app`, `migdolus-painel`,
`prom/node-exporter`), o que impossibilita saber qual versão está no ar e voltar atrás.

---

## Domínios — 43 hostnames, 13 domínios registráveis

```
app.avila.inc                    lojas.avilaops.com
app.avilaops.com                 mail.avilaops.com
app.comandeiro.com.br            maprojetos.com.br
app.sorroche.beauty              mello.avilaops.com
auth.avilaops.com                mellotransportesriopreto.com.br
avila.inc                        migdolus.avilaops.com
avilaops.com                     minas.comandeiro.com.br
cifrainssdeobras.com.br          odoo.avilaops.com
comandeiro.com                   partsagricola.com.br
comandeiro.com.br                saudepet.app.br
crm.avilaops.com                 seteeseteengenharia.com.br
docs.avilaops.com                sorroche.beauty
entrar.avilaops.com              wa.avilaops.com
erp.avilaops.com                 + 13 variantes www.
erp.brilhax.com                  + 2 no infra (obs, n8n)
erp.partsagricola.com.br
gabrielarincao.com.br
jobs.avilaops.com
```

**Registráveis (13):** `avilaops.com`, `avila.inc`, `partsagricola.com.br`,
`saudepet.app.br`, `sorroche.beauty`, `comandeiro.com`, `comandeiro.com.br`,
`brilhax.com`, `cifrainssdeobras.com.br`, `gabrielarincao.com.br`,
`maprojetos.com.br`, `seteeseteengenharia.com.br`, `mellotransportesriopreto.com.br`.

Nenhum bloco morto agora — todos resolvem em DNS.

(07/10/2026: `sorroche.beauty` e `www.sorroche.beauty` não são mais servidos pelo
Hetzner, estão no GitHub Pages; `app.sorroche.beauty` saiu do Caddy em 16/09/2026
e responde 525. O domínio `sorroche.beauty` segue registrado.)

---

## Bancos de dados — 45 no total

### Produção, host (15 bancos)

| Banco | Tamanho | Dono |
|---|---|---|
| `agricola_medusa` | 319 MB | `agricola` |
| `partsagricola` | 232 MB | `odoo_partsagricola` |
| `avilaops` | 122 MB | `odoo_avilaops` |
| `brilhax` | 73 MB | `odoo_brilhax` |
| `migdolus` | 20 MB | `migdolus` |
| `saudepet` | 15 MB | `saudepet` |
| `cliente_portal` | 13 MB | `app_avila` |
| `saudepet_test` | 13 MB | `postgres` |
| `avila_mail` | 9,7 MB | `avila_mail` |
| `jurisflow_poc` | 9,3 MB | `jurisflow_poc_app` |
| `agricola` | 8,8 MB | `agricola` |
| `sorroche_app` (app desligado em 16/09/2026; banco mantido) | 8,7 MB | `sorroche_app` |
| `lojas` | 8,6 MB | `lojas` |
| `avilaops-auth` | 8,1 MB | `postgres` |
| `postgres` | 7,7 MB | `postgres` |

### Produção, containers

`agenda_crm` 8,7 MB · `mello` 8,1 MB · `plataforma` 11 MB
(mais `cifra_calculadora` e `medusa_store`, cujos containers não responderam à
consulta nesta coleta mas estão rodando)

### Infra, containers

`n8n` 19 MB

### Notebook (22 bancos, 803 MB)

`despolarizamed` 435 MB domina. Depois `avila_ops_store` 62 MB, `erpnext` 44 MB,
`agricola_medusa_test` 39 MB, e cópias de desenvolvimento de quase tudo que
está em produção: `saudepet`, `plataforma`, `cifra_calculadora`, `agenda_crm`,
`irlquest`, `medusa_store`, `pkvedacoes_medusa`.

**Nada em SaaS.** Varri por Supabase, Neon, PlanetScale, Turso, Mongo Atlas,
Upstash, Railway e RDS: zero ocorrências. Todo o dado é seu, em máquina sua.

---

## O que mudou desde o inventário da manhã

Comparando com o `INVENTARIO-SERVIDORES.md` de 16/08/2026 (47 hostnames em 33
blocos do Caddy, 26 contêineres rodando e 2 parados), a produção tinha mexido bastante:

> **07/10/2026:** essa comparação vale para a versão antiga do inventário. O
> arquivo foi reescrito e reconferido contra a produção em 07/10/2026 e hoje
> registra 44 hostnames em 30 blocos do Caddy e 13 contêineres rodando, nenhum
> parado. A lista abaixo é histórico de 26/08, não diferença para o inventário atual.

**Entraram:** os domínios `lojas.avilaops.com`, `mello.avilaops.com`,
`migdolus.avilaops.com`, `app.comandeiro.com.br`, `mellotransportesriopreto.com.br`;
os bancos `migdolus`, `lojas`, `saudepet_test`; os containers `lojas-avilaops`,
`mello-app`, `mello-db`, `migdolus-painel`, `odoo-avilaops-recorte`, `brilhax-site`.

**Saíram:** os domínios `agricola.avilaops.com` (que estava sem DNS),
`api-agricola.avilaops.com`, `api.partsagricola.com.br`, `cliente.avilaops.com`,
`cliente.avila.inc`, `admin.saudepet.app.br`, `irlquest.avilaops.com`,
`minas.avilaops.com`, `poc.avilaops.com`; os bancos `pkvedacoes_medusa` e
`pkvedacoes_site` (os dois órfãos que eu tinha apontado).

**Cresceram:** `agricola_medusa` de 200 para 319 MB, `partsagricola` de 114
para 232 MB, volumes Docker de 441 MB para 13,44 GB.

Se foi você fazendo deploy, ótimo — só registrando. Se não foi, vale investigar.

---

## Acesso que ficou montado hoje

**Drives de rede** (funcionando, leitura e escrita testadas):

```
P:  ->  \\10.10.0.2\raiz    producao
I:  ->  \\10.10.0.1\raiz    infra
```

Samba escutando **só** na WireGuard, SMB3 com criptografia obrigatória,
445 fechado no IP público (verificado de fora), acesso anônimo negado.
Usuário `nicolas`. Túnel sobe no boot como serviço do Windows.

**pgAdmin:** arquivo pronto em `C:\Users\nicol\Documents\pgadmin-avilaops-servers.json`.
Sete servidores, todos por túnel SSH com a chave ED25519 existente.
Importar em `Tools > Import/Export Servers`.

---

## Ordem que eu seguiria

**Agora, sem risco:**

1. `chmod 600` nos 96 `.env` de produção — um comando, reversível
2. Apagar as 22 sobras `.env.bak-*` e os certificados vencidos
3. `npm cache clean --force` no notebook — libera 20,67 GB
4. Compactar ou remover o `docker_data.vhdx` parado — libera 53 GB

**Hoje, porque é trabalho em risco:**

5. `git push` nos 6 repositórios do notebook (82 commits)
6. Resolver os 199 commits presos em `/opt/saudepet` e `/opt/sorroche`
   (o do Sorroche está, desde 06/10/2026, em `/opt/arquivo/sorroche-20261006/opt-sorroche`)

**Esta semana:**

7. Rotacionar a chave `sk_live_` da Stripe
8. Descobrir qual volume Docker foi de 441 MB para 13,44 GB
9. `apt upgrade` e reinício agendado nos dois servidores
10. Jail do `fail2ban` para o MTA e para o Caddy

**Quando der:**

11. Healthcheck nos 17 containers fora da norma
12. Trocar `trust` por `scram-sha-256` no `pg_hba.conf` e remover o superusuário
    `app_avilaops`, as três roles órfãs e o projeto `wabusiness`
13. Migrar os builds para o GitHub Container Registry — nenhuma das duas máquinas
    tem RAM para compilar imagens Next de 3 GB, e é a causa raiz da queda de 12 minutos

---

*Para regenerar estes dados: `levantamento\levantar.ps1 -Rapido` (230s) ou sem
`-Rapido` para incluir as medições de peso (~7 min).*
