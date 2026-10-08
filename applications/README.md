# Scripts instalados no servidor `applications`

Cópia versionada de script que roda no `applications` fora de qualquer deploy. Quem manda é
o arquivo instalado: mudou lá, muda aqui no mesmo commit (e o contrário).

| Arquivo | Instalado em | Quem roda |
|---|---|---|
| `rotacionar-backups.sh` | `/opt/avilaops-scripts/rotacionar-backups.sh` (`root:root`, 755) | `avilaops-rotacionar-backups.timer`, 03:40 UTC |

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
