# Scripts instalados no servidor `applications`

Cópia versionada de script que roda no `applications` fora de qualquer deploy. Quem manda é
o arquivo instalado: mudou lá, muda aqui no mesmo commit (e o contrário).

| Arquivo | Instalado em | Quem roda |
|---|---|---|
| `rotacionar-backups.sh` | `/opt/avilaops-scripts/rotacionar-backups.sh` (`root:root`, 755) | `avilaops-rotacionar-backups.timer`, 03:40 UTC |
| `sync-r2.sh` | `/usr/local/bin/sync-r2.sh` (`root:root`, 750) | `/etc/cron.d/avila-r2-backup`, 04:00 UTC |

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
