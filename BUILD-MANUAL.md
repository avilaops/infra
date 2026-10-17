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
`lojas.avilaops.com`, 8 delas em 08/10).

**Desde 09/10/2026 07:30 UTC (tarefa 262) o `/usr/local/sbin/avila-deploy` instalado é o
`scripts/deploy-container.sh` do commit `44624fd`** (315 linhas, sha256 `170359f2…e3347`), no
lugar da versão de 18/09/2026 (152 linhas, sha256 `c55888b5…493dc3`). Passou a ter
`preflight`, `ensure_space`, `prune_old_images`, a cópia do `.env` para o Prisma e o dump
antes da migração: **o deploy do `lojas` pelo Actions deixou de migrar sem dump**. Primeiros
deploys de verdade com ele, em 09/10: `tms.avilaops.com` (12:07 UTC, sem migração; ficaram
duas imagens do repositório), `app.avilaops.com` (12:40 UTC) e `auth.avilaops.com` (13:24 UTC),
estes dois com a migração pelo Prisma lendo a cópia do `.env`. `lojas`, `saudepet-backend` e
`mail` ainda não tinham publicado (tarefa 291). O que entrou no script depois do `44624fd` (entrada padrão fechada em
`preflight`, `ensure_space` e `docker image rm`, tarefa 291) **não está instalado**.

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
`gha-deploy` via `sudo`) se instala do mesmo jeito, com os nomes trocados e o modo 755. Última
instalação: 09/10/2026 07:30 UTC (tarefa 262, commit `44624fd`); cópia de volta e comandos em
`applications/README.md`.

## Builds dos agentes: somente no GitHub Actions

O `creators` coordena agentes e não é um executor de CI. Compilação, testes
pesados, suites E2E e criação de imagens devem executar em runners hospedados
pelo GitHub. Isso inclui `npm run build`, `pnpm build`, `turbo build`,
`next build`, `cargo build`, `cargo test` e `docker build`.

O fluxo obrigatório é:

1. enviar a alteração para uma branch do repositório;
2. executar ou aguardar o workflow do GitHub Actions;
3. corrigir falhas no workflow;
4. publicar somente o artefato ou a imagem versionada aprovada pelo CI.

O servidor pode executar verificações leves que não compilam o projeto. Se o
GitHub Actions estiver indisponível por cobrança, cota ou configuração, o build
fica bloqueado até a causa ser resolvida. A indisponibilidade do runner não
autoriza transferir o build para o `creators` ou para um servidor de produção.

O `build-pesado` foi removido do repositório e do `creators`: serializar o build
no mesmo host ainda consome recursos que pertencem aos agentes. Em 17/10/2026,
builds concorrentes no `creators` esgotaram memória e swap, acionaram o OOM
killer e exigiram reinício do host.

## Histórico

- 08/10/2026: `auth.avilaops.com` publicado por este caminho no commit
  `4552b575f3c39822db4050c919cef16686998cfc`, com os dois scripts de servidor
  chamados à mão.
- 08/10/2026: `publicar-manual.ps1` rodou inteiro de uma vez e publicou o
  `auth.avilaops.com` no commit `9c214e261b8435162e7c619da613905991b2c971`
  (build, transferência e troca, container saudável). O script publica o que
  estiver em `origin/main` na hora: conferir com `git log` antes de rodar, porque
  entra também o que outra pessoa mesclou.
