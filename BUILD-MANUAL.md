# Build e deploy manual pelo apps-noclient

Por decisão do Nicolas em 08/10/2026, este é o caminho de deploy em uso: build no
`apps-noclient` e troca manual no servidor de aplicações. Os workflows do GitHub
Actions (`.github/workflows/container.yml` e `deploy-ssh.yml`) continuam no
repositório, mas não são pré-requisito para publicar.

A regra "build não roda no servidor de produção" continua valendo: o build roda
no `apps-noclient`, que não atende cliente, e o servidor de aplicações só recebe
a imagem pronta.

## Em um comando

Na máquina de quem publica, com `apps-noclient` e `applications` no
`~/.ssh/config`:

```powershell
.\scripts\publicar-manual.ps1 -Repositorio D:\Projetos\auth.avilaops.com -Aplicacao auth.avilaops.com
```

Publica o commit de `origin/main`. Código local não mesclado não entra.

## O que acontece

| Passo | Onde | Script | O que faz |
| --- | --- | --- | --- |
| Build | `apps-noclient` | `/usr/local/sbin/avila-build` (`scripts/build-noclient.sh`) | recebe o commit por `git archive`, constrói `ghcr.io/avilaops/<repo>:sha-<commit>` com os mesmos rótulos do workflow e grava um `.tar.gz` com checksum |
| Transferência | entre os dois | dentro do `publicar-manual.ps1` | `scp` direto de servidor para servidor, com chave criada na hora, aceita só do IP do build e removida em seguida |
| Deploy | `applications` | `/usr/local/sbin/avila-deploy-local` (`scripts/deploy-container-local.sh`) | confere o checksum, carrega a imagem, roda migração se a aplicação tiver `MIGRATE_ENV_FILE`, troca o container, verifica a saúde e volta à imagem anterior se falhar |

O deploy lê a mesma configuração do `avila-deploy`
(`/etc/avilaops/deploy/<aplicacao>.conf`, versionada em `deploy/production/`) e
só aceita imagem do `IMAGE_REPOSITORY` daquela aplicação.

**Os dois caminhos estão em uso (conferido em 08/10/2026, tarefa 247).** O GitHub Actions
não está parado: de 01/10 a 08/10 o `avila-deploy` rodou 114 vezes no `applications` (39 do
`lojas.avilaops.com`, 8 delas em 08/10). O `/usr/local/sbin/avila-deploy` instalado é de
18/09/2026 (152 linhas) e está bem atrás de `scripts/deploy-container.sh` (283 linhas): não
tem `preflight`, `ensure_space`, `prune_old_images`, a cópia do `.env` para o Prisma nem o
dump antes da migração. Ou seja, **deploy do `lojas` pelo Actions migra sem dump**; só o
`avila-deploy-local` faz o dump. Instalar a versão do repositório troca o deploy de todas as
aplicações de uma vez e pede revisão própria do que mudou desde 18/09 (ver "Instalar ou
atualizar").

## Limites

- Só aplicações em container. Estático e systemd continuam só pelo Actions.
- Build com `build-args` ou segredos de build (GTM, GA, `JOBS_API_URL`) não é
  coberto: o `avila-build` passa apenas `GIT_SHA`.
- A imagem não vai para o GHCR. O registro fica sem essa versão; a referência é
  a tag `sha-<commit>` e o próprio commit no GitHub.
- Nada de código fica nos servidores: a pasta do build some ao terminar, o
  arquivo é apagado na origem depois de transferido e no destino depois de
  carregado. O que sobrar de um dia para o outro o `avila-build` remove.
- `dump_if_pending` e `healthy` em `deploy-container-local.sh` são cópia das de
  `deploy-container.sh` (há teste que exige a `dump_if_pending` igual nas duas). A
  `run_migrations` faz o mesmo papel com outra chamada do Prisma. Mudou lá, muda aqui.
- **Deploy que para na migração obriga a reenviar a imagem.** O `avila-deploy-local` apaga o
  `.tar.gz` logo depois do `docker load`, antes da migração. Se o deploy parar ali (dump que
  falha ou sai incompleto, banco da `DATABASE_URL` diferente de `MIGRATE_DUMP_DB`, migração
  que falha), o serviço segue na imagem anterior, nada foi migrado nem trocado, e o arquivo já
  não existe: corrigida a causa, rode o `publicar-manual.ps1` de novo (build e transferência
  inteiros). A imagem `sha-<commit>` fica carregada no Docker do servidor, mas o
  `avila-deploy-local` só aceita arquivo.

## Dump antes da migração (`MIGRATE_DUMP_DB`)

Destino cujo `.conf` define `MIGRATE_DUMP_DB=<banco>` (hoje só o `lojas.avilaops.com`) ganha
um `pg_dump` em `/opt/backups/db/pre-migracao-<aplicação>-AAAAMMDD-HHMMSS.sql.gz` antes de
migração pendente. Sem pendência não há dump. Regras que param o deploy antes de migrar:

- o nome do banco na `DATABASE_URL` do `MIGRATE_ENV_FILE` tem de ser igual a
  `MIGRATE_DUMP_DB` (conferido em todo deploy, com ou sem pendência); URL que não dá para ler
  (sem banco, senha com `?` ou `#` sem codificar, nome montado por `${VAR}`) também para.
  Só o nome é conferido: o dump sai sempre do Postgres local do host, então `MIGRATE_DUMP_DB`
  só serve para banco que mora nele;
- dump que falha, sai vazio, truncado ou não chega ao nome final.

A linha de sucesso no log é `==> dump conferido: <arquivo> (<bytes> bytes)`. Os arquivos saem
pela rotação local (o mais recente fica, os outros 7 dias) e, com o `sync-r2.sh` da tarefa 247
instalado, **não vão para o R2**
(`applications/sync-r2.sh`). Não há teto por quantidade, de propósito: quando uma migração
falha e o deploy é repetido, o dump que vale é o mais antigo, e um teto apagaria justamente
esse. Rever se o dump do `lojas` passar de 200 MB (hoje 5,9 MB) ou o disco do `applications`
ficar com menos de 5 GB livres.

## Instalar ou atualizar os scripts nos servidores

```bash
ssh apps-noclient 'tr -d "\r" > /usr/local/sbin/avila-build && chmod 755 /usr/local/sbin/avila-build' < scripts/build-noclient.sh
ssh applications 'tr -d "\r" > /usr/local/sbin/avila-deploy-local && chmod 700 /usr/local/sbin/avila-deploy-local' < scripts/deploy-container-local.sh
```

No `applications` o arquivo é trocado com deploy podendo estar em curso: prefira gravar ao
lado e trocar com `mv` (atômico), depois de conferir que nenhuma trava de
`/var/lib/avilaops/deploy/*/lock` está presa e de guardar a cópia de volta em `/opt/backups`:

```bash
ssh applications 'cp -p /usr/local/sbin/avila-deploy-local /opt/backups/avila-deploy-local.bak-AAAAMMDD-tNNN'
ssh applications 'tr -d "\r" > /usr/local/sbin/.avila-deploy-local.novo && bash -n /usr/local/sbin/.avila-deploy-local.novo \
  && chmod 700 /usr/local/sbin/.avila-deploy-local.novo && mv /usr/local/sbin/.avila-deploy-local.novo /usr/local/sbin/avila-deploy-local' < scripts/deploy-container-local.sh
```

O `/usr/local/sbin/avila-deploy` (`scripts/deploy-container.sh`, modo 755, chamado pelo
`gha-deploy` via `sudo`) se instala do mesmo jeito, com os nomes trocados. **Não foi
reinstalado desde 18/09/2026**: a versão do repositório nunca rodou em produção.

## Build no servidor dos agentes (`creators`): um por vez

Os dois scripts acima rodam no `apps-noclient` e no `applications`. No `creators`
(4 GB de RAM, onde rodam os agentes e o gateway do OpenClaw) não há script de
build deste repositório, mas agentes disparam `npm run build`, testes longos e
`docker build` à mão. Em 08/10/2026 três deles ao mesmo tempo zeraram a swap e o
kernel matou o gateway (tarefas 231 e 239). Por isso, **todo build pesado no
`creators` passa pelo `build-pesado`** (`scripts/build-pesado.sh`):

```bash
build-pesado npm run build
build-pesado docker build --build-arg NODE_OPTIONS=--max-old-space-size=1024 -t <imagem> .
build-pesado ./deploy/empacotar.sh      # script do produto que faz build por dentro
```

- Trava única em `/var/lock/build-pesado.lock` (`flock`). Quem chega depois avisa
  quem está com a trava, espera até 30 min e, se ela não soltar, sai com código
  75 sem executar nada.
- Acrescenta `--max-old-space-size=1024` ao `NODE_OPTIONS`, preservando o que já
  houver; se quem chama já definiu um `max-old-space-size`, fica o dele.
- Ajustes por variável: `BUILD_PESADO_ESPERA_S` (padrão 1800),
  `BUILD_PESADO_HEAP_MB` (padrão 1024), `BUILD_PESADO_CARENCIA_S` (padrão 30),
  `BUILD_PESADO_LOCK`. Os valores em segundos são inteiros sem zero à esquerda
  (`08` é recusado com código 64).
- O comando roda em sessão e grupo de processos próprios (`setsid`), sem
  terminal de controle: comando que pede senha em `/dev/tty` não funciona por
  aqui. `HUP`, `INT`, `QUIT` e `TERM` no `build-pesado` viram `TERM` para o
  grupo inteiro (o comando e o que ele disparou); a trava só solta com o grupo
  vazio, quem não encerrar em `BUILD_PESADO_CARENCIA_S` leva `KILL`, e a saída
  é 128 + o sinal.
- **`KILL` no `build-pesado`** (só nele ou no grupo de processos dele, que é
  como um executor de agente encerra no estouro de tempo): o script morre sem
  tratar, mas um vigia (ache com `pgrep -f build-pesado-vigia`; em sessão
  própria, com a trava na mão) percebe, manda `TERM` ao grupo do comando,
  `KILL` em quem não encerrar em `BUILD_PESADO_CARENCIA_S`, e só então solta a
  trava.
- **Limite do `KILL`:** `kill -9` no `build-pesado` **e** no vigia solta a
  trava na hora e deixa o build rodando. Para interromper um build, mande
  `TERM` ao `build-pesado`.
- Quem espera vê apenas o pid, a hora, o nome do comando e o diretório de quem
  está com a trava (`/var/lock/build-pesado.lock.dono`); os argumentos não são
  gravados, porque podem carregar segredo.
- Chamada aninhada (script sob a trava que chama `build-pesado` de novo) não
  espera por si mesma, desde que o `build-pesado` de fora ainda esteja vivo e
  seja ancestral. Processo que sobrou de um build encerrado entra na fila como
  qualquer outro.
- Dentro de `docker build` o ambiente de quem chama não entra e o `--memory` é
  ignorado pelo BuildKit: o teto só vale se o Dockerfile declarar
  `ARG NODE_OPTIONS` e `ENV NODE_OPTIONS=$NODE_OPTIONS` no estágio de build. A
  trava vale de qualquer jeito.
- A trava só protege quem passa por ela: build disparado sem o `build-pesado`
  continua concorrendo.

Instalar ou atualizar no `creators`: `~/.local/bin/build-pesado` é uma **cópia**
de um commit já enviado à `main`, não um link para o checkout (edição ainda não
revisada no infra não muda o que os agentes executam). Depois do push:

```bash
git -C ~/projetos/infra fetch origin main
~/projetos/infra/scripts/instalar-build-pesado.sh            # origin/main
~/projetos/infra/scripts/instalar-build-pesado.sh <commit>   # ou um commit específico
tail -n 1 ~/.local/bin/build-pesado                          # mostra o commit instalado
```

O instalador recusa (código 65) commit que não esteja em `origin/main` e troca o
arquivo de forma atômica; build em andamento segue com a versão que já abriu.

## Histórico

- 08/10/2026: `auth.avilaops.com` publicado por este caminho no commit
  `4552b575f3c39822db4050c919cef16686998cfc`, com os dois scripts de servidor
  chamados à mão.
- 08/10/2026: `publicar-manual.ps1` rodou inteiro de uma vez e publicou o
  `auth.avilaops.com` no commit `9c214e261b8435162e7c619da613905991b2c971`
  (build, transferência e troca, container saudável). O script publica o que
  estiver em `origin/main` na hora: conferir com `git log` antes de rodar, porque
  entra também o que outra pessoa mesclou.