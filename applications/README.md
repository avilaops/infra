# Scripts instalados no servidor `applications`

Cópia versionada de script que roda no `applications` fora de qualquer deploy. Quem manda é
o arquivo instalado: mudou lá, muda aqui no mesmo commit (e o contrário).

| Arquivo | Instalado em | Quem roda |
|---|---|---|
| `rotacionar-backups.sh` | `/opt/avilaops-scripts/rotacionar-backups.sh` (`root:root`, 755) | `avilaops-rotacionar-backups.timer`, 03:40 UTC |
| `sync-r2.sh` | `/usr/local/bin/sync-r2.sh` (`root:root`, 750) | `/etc/cron.d/avila-r2-backup`, 04:00 UTC |

O `avila-deploy` não tem cópia nesta pasta: o versionado é `scripts/deploy-container.sh` (ver
"`avila-deploy`" abaixo).

`rotacionar-backups.teste.sh` monta um diretório temporário com arquivos falsos e confere a
regra (família, gzip vazio, gzip truncado, nome com espaço ou hífen, nome que é só a data,
`.dump`). Roda junto
com os testes do repositório (`tests/test_rotacionar_backups.py`), que também confere a
sintaxe.

```bash
bash applications/rotacionar-backups.teste.sh applications/rotacionar-backups.sh   # teste
ssh applications sha256sum /opt/avilaops-scripts/rotacionar-backups.sh             # instalado
sha256sum applications/rotacionar-backups.sh                                       # versionado
```

Para instalar uma versão nova: teste aqui, cópia do instalado em
`/opt/backups/rotacionar-backups.sh.bak-AAAAMMDD-tNNN`, `scp` + `sudo install -o root -g root
-m 755`, `DRY=1` uma vez no servidor (só lista, não apaga) e conferir os dois `sha256sum`.

Só `.gz` passa por teste de integridade (`gzip -t`). Os `.dump` contam pelo tamanho: em
07/10/2026 eram 3, cópias avulsas feitas à mão, cada uma sozinha na própria família (então
nunca disputam o lugar de "mais recente com conteúdo"), e as três passam no `pg_restore -l`.

## `sync-r2.sh`

Copia `/opt/backups/db` e `/var/backups/cliente_portal` para o bucket `avilaops-backups` do R2
com `rclone copy` (nunca apaga no destino). Versionado desde a tarefa 247 (08/10/2026), que
tirou do envio os `pre-migracao-*.sql.gz` (dump que o deploy faz antes de migração pendente).
Por que fora do R2 e não com regra de expiração: o script só copia e o bucket não tem expurgo
(em 08/10/2026, `db/` tinha 865 objetos e 83 GB, uns 55 por família, o mais antigo de 15/08), então cada
dump ficaria lá para sempre; regra de ciclo de vida exige credencial da Cloudflare que a equipe
não tem no servidor; e o dump de antes da migração só serve para desfazer a migração nas horas
seguintes: o estado novo sobe no dump diário das 3h30.

O teste (`tests/test_sync_r2.py`) roda o script com um `rclone` de mentira e confere os filtros
e a ordem deles. No servidor, antes de instalar, a conferência é só de listagem:

```bash
rclone lsf /opt/backups/db --include "*.sql.gz" --include "*.tar.gz" | sort | sha256sum          # filtro antigo
rclone lsf /opt/backups/db --filter "- pre-migracao-*" --filter "+ *.sql.gz" --filter "+ *.tar.gz" --filter "- *" | sort | sha256sum
```

Sem nenhum `pre-migracao-*` no diretório as duas somas têm de ser iguais (em 08/10/2026: 82
arquivos, iguais).

Instalado em 08/10/2026 19:10 UTC (sha256 `c473dfac…8b7e6`, `root:root` 750). O servidor tem
rclone 1.75 e o teste local foi com 1.60, então a prova foi refeita lá antes da troca, em pasta
temporária com `rclone lsf -R --files-only`: o filtro novo tirou os `pre-migracao-*` da raiz e
de subpasta e manteve os `.sql.gz` e `.tar.gz` comuns.

Instalação e volta são a mesma troca atômica (nunca `cp` por cima do arquivo em uso), fora da
janela das 04:00 UTC e sem sync em curso. O padrão com colchete evita que o `pgrep` case com o
próprio shell; mesmo assim, rodar por `ssh applications bash -s < roteiro`, porque um
`ssh applications '…sync-r2.sh…'` põe o nome na linha de comando do shell remoto.

```bash
pgrep -af "[s]ync-r2|[r]clone" || echo nenhum
# instalar: mandar applications/sync-r2.sh para /usr/local/bin/.sync-r2.sh.novo; voltar:
cp -p /opt/backups/sync-r2.sh.bak-20261008-t247 /usr/local/bin/.sync-r2.sh.novo
bash -n /usr/local/bin/.sync-r2.sh.novo && chown root:root /usr/local/bin/.sync-r2.sh.novo \
  && chmod 750 /usr/local/bin/.sync-r2.sh.novo \
  && mv /usr/local/bin/.sync-r2.sh.novo /usr/local/bin/sync-r2.sh \
  || rm -f /usr/local/bin/.sync-r2.sh.novo
sha256sum /usr/local/bin/sync-r2.sh
```

## `avila-deploy`

`/usr/local/sbin/avila-deploy` (`root:root`, 755) é o deploy que o GitHub Actions chama pelo
`gha-deploy` via `sudo`. O versionado é `scripts/deploy-container.sh`; os testes são
`tests/test_deploy.py`.

| Quando (UTC) | Tarefa | Commit | sha256 instalado | Cópia de volta |
|---|---|---|---|---|
| 18/09/2026 20:29 | — | versão de 18/09 (152 linhas) | `c55888b5…493dc3` | `/usr/local/sbin/avila-deploy.bak-20260916-160213` (a de 14/09) |
| 09/10/2026 07:30:28 | 262 | `44624fd` (315 linhas) | `170359f2…e3347` | `/opt/backups/avila-deploy.bak-20261009-t262` (`c55888b5…493dc3`) |

O que veio depois do `44624fd` em `scripts/deploy-container.sh` (tarefa 291: `preflight`,
`ensure_space` e `docker image rm` com a entrada padrão fechada) **não está instalado**;
enquanto não for, `sha256sum` do instalado e do versionado diferem.

```bash
ssh applications sha256sum /usr/local/sbin/avila-deploy   # instalado
git show 44624fd:scripts/deploy-container.sh | sha256sum   # o que foi instalado na 262
```

Troca e volta são sempre por nome provisório e `mv` (atômico), nunca `cp` por cima, sem
deploy em curso e com o roteiro pela entrada padrão (`ssh applications bash -s < roteiro`),
porque o `pgrep` casa com a linha de comando de um `ssh applications '…avila-deploy…'`:

```bash
pgrep -af "[a]vila-deploy" || echo nenhum
for l in /var/lib/avilaops/deploy/*/lock; do flock -n "$l" true || echo "PRESA: $l"; done
# voltar para a versão de 18/09:
cp -p /opt/backups/avila-deploy.bak-20261009-t262 /usr/local/sbin/.avila-deploy.novo
bash -n /usr/local/sbin/.avila-deploy.novo && chown root:root /usr/local/sbin/.avila-deploy.novo \
  && chmod 755 /usr/local/sbin/.avila-deploy.novo \
  && mv /usr/local/sbin/.avila-deploy.novo /usr/local/sbin/avila-deploy \
  || rm -f /usr/local/sbin/.avila-deploy.novo
sha256sum /usr/local/sbin/avila-deploy   # c55888b5…493dc3
```

Critério de volta: deploy que falhe por motivo do script novo (`preflight`, espaço,
`Environment variable not found`, `Variavel de banco…`, `Banco da URL…` sem que o `.env`
tenha mudado). Falha de migração de verdade ou de saúde do serviço não é motivo. A volta não
desfaz o que os deploys já fizeram: imagem antiga removida pela poda só volta por pull do
GHCR, e os `pre-migracao-*` ficam até a rotação.

Depois de cada sucesso em modo container ficam só a imagem em uso e a anterior de cada
repositório (conferido em 09/10/2026: `tms` de 12 para 2, `app` de 14 para 2, `auth` de 8
para 2).

## `evolution-avilaops-com/`

Compose da Evolution API (gateway não-oficial de WhatsApp), instalado em
`/opt/evolution-avilaops-com` (`root:root`; `.env` em modo 600). Não passa pelo
`avila-deploy`: é imagem de terceiro com tag fixa (`evoapicloud/evolution-api:v2.3.7`), sem
build e sem domínio público. Só o n8n nativo a chama, em `http://172.31.0.14:8080`, para
publicar Status do WhatsApp (instância `status`).

O `.env.example` é o `.env` instalado sem os dois segredos (senha da role
`evolution_avilaops_com` e `AUTHENTICATION_API_KEY`), que são gerados no servidor. Para mudar
o compose ou o `.env`: cópia do instalado em `/opt/backups/`, `scp`, e:

```bash
ssh applications 'cd /opt/evolution-avilaops-com && docker compose config -q && docker compose up -d'
ssh applications 'curl -s -m 5 http://172.31.0.14:8080/'    # 200 com a versão
```

O banco fica no Postgres do host e entra no dump diário (`backup-todos-bancos.sh`). A sessão
do WhatsApp fica no volume `evolution-avilaops-com-instances`; sem ele o número precisa ser
pareado de novo (`GET /instance/connect/status` devolve o QR).

## `whatsapp-avilaops-com/`

Compose do serviço próprio de envio do WhatsApp (código em `avilaops/whatsapp.avilaops.com`),
instalado em `/opt/whatsapp-avilaops-com`. Envia para destinos cadastrados no `.env` (a conversa
do Nicolas e o canal da Avila Ops); só o n8n nativo chama, em `http://172.31.0.15:8080`.

Publica-se como os outros containers (`BUILD-MANUAL.md`, destino em
`deploy/production/whatsapp.avilaops.com.conf`). **A primeira subida foi à mão**: o
`avila-deploy-local` consulta a imagem do container em uso e para quando ele ainda não existe.
A imagem foi carregada por ele, o `image.yml` escrito em
`/var/lib/avilaops/deploy/whatsapp.avilaops.com/` e o `docker compose up -d` rodado com os dois
arquivos. Dali em diante o deploy normal funciona.

Sem banco: o único estado é a sessão do WhatsApp, no volume `whatsapp-avilaops-com-sessao`.
Parear de novo: `POST /parear` (ver o README do serviço).
