# Scripts e serviços do servidor `apps-noclient`

Cópia versionada do que roda no `apps-noclient` (`204.168.249.111`, `10.10.0.4` na
WireGuard) fora de qualquer deploy. Quem manda é o arquivo instalado: mudou lá, muda aqui
no mesmo commit (e o contrário).

| Arquivo | Instalado em | Quem roda |
|---|---|---|
| `backup-todos-bancos.sh` | `/usr/local/bin/backup-todos-bancos.sh` (`root:root`, 755) | `/etc/cron.d/backup-todos-bancos`, 03:30 UTC |
| `infisical/docker-compose.yml` | `/opt/infisical/docker-compose.yml` (`root:root`, 644) | `docker compose` em `/opt/infisical` |
| `mcp-avilaops-com/docker-compose.yml` | `/opt/mcp-avilaops-com/docker-compose.yml` (`root:root`, 644) | Docker Compose, contêiner `mcp-avilaops-com-web` |

```bash
ssh apps-noclient sha256sum /usr/local/bin/backup-todos-bancos.sh /opt/infisical/docker-compose.yml   # instalado
ssh apps-noclient sha256sum /opt/mcp-avilaops-com/docker-compose.yml                                  # instalado
sha256sum apps-noclient/backup-todos-bancos.sh apps-noclient/infisical/docker-compose.yml             # versionado
sha256sum apps-noclient/mcp-avilaops-com/docker-compose.yml                                           # versionado
```

## MCP único da Ávila Ops

Desde 24/10/2026, `mcp.avilaops.com` reside integralmente neste servidor. O
registro A aponta para `204.168.249.111`; o Caddy encaminha para o contêiner na
rede `edge`, em `172.31.0.18:8080`. Compose e trecho do Caddy ficam em
`apps-noclient/mcp-avilaops-com/`.

O diretório instalado é `/opt/mcp-avilaops-com`. O `.env` (600) guarda o
segredo OIDC e tokens internos; não é versionado. O estado de clientes OAuth e
tokens em hash fica no volume `mcp-avilaops-com-dados`. O backend GSC continua
em `/opt/mcp-gsc`, acessado localmente por `10.10.0.4:8091`.

Verificação sem credencial:

```bash
curl -fsS https://mcp.avilaops.com/health
curl -sS -o /dev/null -w '%{http_code}\n' -X POST https://mcp.avilaops.com/mcp  # 401
```

O `/usr/local/bin/sync-r2.sh` do `apps-noclient` (04:00 UTC, `/etc/cron.d/avila-r2-backup`)
ainda não tem cópia nesta pasta. Ele manda `/opt/backups/db` para
`r2:avilaops-backups/apps-noclient/db` com `rclone copy`: o expurgo local não apaga o remoto.

## Backup

O `backup-todos-bancos.sh` grava em `/opt/backups/db`, com retenção local de 7 dias, e a
lista é explícita: banco, volume ou pasta que não entrar no script não é copiado. Cada
rodada escreve uma linha `ok=N erros=N` em `/var/log/backup-bancos.log`.

### Infisical

O Infisical (cofre de segredos, provedor do External secrets do `n8n.avilaops.com`) está na
rotina desde 10/10/2026. São duas peças, e uma não serve sem a outra:

| Peça | Arquivo em `/opt/backups/db` | Conteúdo |
|---|---|---|
| Banco | `host-infisical-AAAAMMDD.sql.gz` | `pg_dump` do banco `infisical` do Postgres do host |
| Chave | `dir-infisical-env-AAAAMMDD.tar.gz` (600) | `/opt/infisical/.env` e `docker-compose.yml` |

O `.env` guarda a `ENCRYPTION_KEY`: sem ela o dump do banco é ilegível. O `.env` fica fora
do Git; a cópia dele só existe no servidor, em `/opt/backups/db` e no R2.

Conferido em 12/10/2026, sobre os arquivos da rodada das 03:30 UTC (`ok=13 erros=0`):

- o dump restaurou sem erro em um banco temporário, com os mesmos números do banco em uso
  (769 tabelas, 3 segredos, 4 projetos, 1 usuário); o banco temporário foi removido;
- o `.env` dentro do `.tar.gz` é idêntico ao `/opt/infisical/.env`;
- os dois arquivos de 10, 11 e 12/10 estão em `r2:avilaops-backups/apps-noclient/db`.

Para repetir a conferência:

```bash
ssh apps-noclient
T=infisical_teste_restauracao_$(date -u +%Y%m%d)
sudo -u postgres createdb "$T"
zcat /opt/backups/db/host-infisical-$(date -u +%Y%m%d).sql.gz | sudo -u postgres psql -q -v ON_ERROR_STOP=1 -d "$T"
sudo -u postgres psql -Atc 'select count(*) from secrets_v2' -d "$T"
sudo -u postgres psql -Atc 'select count(*) from secrets_v2' -d infisical
sudo -u postgres dropdb "$T"
tar -xzOf /opt/backups/db/dir-infisical-env-$(date -u +%Y%m%d).tar.gz .env | cmp - /opt/infisical/.env
rclone lsl r2:avilaops-backups/apps-noclient/db --include '*infisical*'
```

O teste não cobre subir um Infisical novo a partir da cópia (banco restaurado mais `.env`) e
ler um segredo por ele.

## Vigia do disco

O servidor hospeda produção e é a máquina de build das sessões, em 38 GB. Em
10/10/2026 o disco chegou a 100% num deploy e o Redis do notas recusou
gravações. Desde 12/10/2026 há uma rotina olhando.

| Peça | Onde |
|---|---|
| Script | `vigia-disco.sh` → `/usr/local/bin/vigia-disco` |
| Agendamento | `vigia-disco.service` e `vigia-disco.timer` (a cada 15 minutos) |
| Configuração | `/etc/vigia-disco.env` (600): `VIGIA_DISCO_WEBHOOK` e os limites |
| Log | `/var/log/vigia-disco.log` (só escreve quando age) |
| Aviso | fluxo do n8n "Infra - Aviso de disco" (`fvitbhOh8h8WkGVV`), que manda WhatsApp ao Nicolas |

Como age:

- Abaixo de 80% não faz nada.
- A partir de 80%, e só sem build rodando, apaga o que se refaz sozinho: cache
  de build do Docker, imagens sem contêiner, volumes anônimos sem contêiner,
  cache do npm e pastas de `/opt/build` e `/root/build-*` paradas há mais de 48
  horas e sem contêiner montando.
- Se depois disso o disco ainda estiver em 90% ou mais, avisa, no máximo uma
  vez a cada 6 horas. Quando volta para baixo de 80%, avisa uma vez que
  normalizou.

Nunca toca em volume com nome, `/opt/backups`, `/opt/<produto>` nem banco.
Pasta de build nesse servidor é descartável: não deixe nela nada que só exista
ali.

```bash
vigia-disco --simula                       # mostra o que faria agora
VIGIA_DISCO_LIMPA=1 vigia-disco --simula   # simula disco cheio
systemctl list-timers vigia-disco.timer
```

O webhook do fluxo não tem senha além do caminho. Por isso o n8n monta o texto
com campos conferidos (nome de servidor conhecido, números dentro da faixa) e
descarta o resto.

## Inventário de capacidades

Desde 16/10/2026 o `/usr/local/bin/inventario-capacidades` manda ao n8n, todo dia às 12:00 UTC,
as skills e o `.mcp.json` dos projetos em `/opt` (hoje só o `mcp-gsc`; não há agente instalado
no `root`). Timer `inventario-capacidades.timer`, configuração em `/etc/inventario-capacidades.env`
e log em `/var/log/inventario-capacidades.log`. Detalhes em
[`inventario-capacidades/README.md`](../inventario-capacidades/README.md).
