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

## Limites

- Só aplicações em container. Estático e systemd continuam só pelo Actions.
- Build com `build-args` ou segredos de build (GTM, GA, `JOBS_API_URL`) não é
  coberto: o `avila-build` passa apenas `GIT_SHA`.
- A imagem não vai para o GHCR. O registro fica sem essa versão; a referência é
  a tag `sha-<commit>` e o próprio commit no GitHub.
- Nada de código fica nos servidores: a pasta do build some ao terminar, o
  arquivo é apagado na origem depois de transferido e no destino depois de
  carregado. O que sobrar de um dia para o outro o `avila-build` remove.
- `dump_if_pending`, `run_migrations` e `healthy` em `deploy-container-local.sh` são cópia das de
  `deploy-container.sh`. Mudou lá, muda aqui.

## Instalar ou atualizar os scripts nos servidores

```bash
ssh apps-noclient 'tr -d "\r" > /usr/local/sbin/avila-build && chmod 755 /usr/local/sbin/avila-build' < scripts/build-noclient.sh
ssh applications 'tr -d "\r" > /usr/local/sbin/avila-deploy-local && chmod 700 /usr/local/sbin/avila-deploy-local' < scripts/deploy-container-local.sh
```

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
  `BUILD_PESADO_HEAP_MB` (padrão 1024), `BUILD_PESADO_LOCK`.
- Dentro de `docker build` o ambiente de quem chama não entra e o `--memory` é
  ignorado pelo BuildKit: o teto só vale se o Dockerfile declarar
  `ARG NODE_OPTIONS` e `ENV NODE_OPTIONS=$NODE_OPTIONS` no estágio de build. A
  trava vale de qualquer jeito.
- A trava só protege quem passa por ela: build disparado sem o `build-pesado`
  continua concorrendo.

Instalar ou atualizar no `creators` (atalho para o script do repositório):

```bash
ln -sfn ~/projetos/infra/scripts/build-pesado.sh ~/.local/bin/build-pesado
```

## Histórico

- 08/10/2026: `auth.avilaops.com` publicado por este caminho no commit
  `4552b575f3c39822db4050c919cef16686998cfc`, com os dois scripts de servidor
  chamados à mão.
- 08/10/2026: `publicar-manual.ps1` rodou inteiro de uma vez e publicou o
  `auth.avilaops.com` no commit `9c214e261b8435162e7c619da613905991b2c971`
  (build, transferência e troca, container saudável). O script publica o que
  estiver em `origin/main` na hora: conferir com `git log` antes de rodar, porque
  entra também o que outra pessoa mesclou.