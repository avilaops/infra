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
