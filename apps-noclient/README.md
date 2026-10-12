# Scripts e serviços do servidor `apps-noclient`

Cópia versionada do que roda no `apps-noclient` (`204.168.249.111`, `10.10.0.4` na
WireGuard) fora de qualquer deploy. Quem manda é o arquivo instalado: mudou lá, muda aqui
no mesmo commit (e o contrário).

| Arquivo | Instalado em | Quem roda |
|---|---|---|
| `backup-todos-bancos.sh` | `/usr/local/bin/backup-todos-bancos.sh` (`root:root`, 755) | `/etc/cron.d/backup-todos-bancos`, 03:30 UTC |
| `infisical/docker-compose.yml` | `/opt/infisical/docker-compose.yml` (`root:root`, 644) | `docker compose` em `/opt/infisical` |

```bash
ssh apps-noclient sha256sum /usr/local/bin/backup-todos-bancos.sh /opt/infisical/docker-compose.yml   # instalado
sha256sum apps-noclient/backup-todos-bancos.sh apps-noclient/infisical/docker-compose.yml             # versionado
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
