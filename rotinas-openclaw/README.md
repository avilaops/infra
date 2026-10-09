# Rotinas do OpenClaw (servidor `creators`)

Scripts **sem LLM** que mantêm o ciclo da equipe de agentes andando sozinho. Rodam
como jobs de comando do cron do OpenClaw, com o usuário `avops`, **não desta pasta**, e
sim da cópia publicada da `main` do GitHub:
`~/.local/share/rotinas-openclaw/atual/rotinas-openclaw` (ver "Publicação").

| Script | Quando | O que faz | Onde grava |
|---|---|---|---|
| `varredura_repos.py` | de hora em hora (min. 05) | Percorre `~/projetos`: branch atual, arquivos sem commit, branches `claude/…`/`openclaw/…`/`codex/…`, PRs abertos e CI | tabelas `repo_*`; página `~/.agents/shared/rotinas/repos.md` |
| `ciclo_roadmap.py` | de hora em hora (min. 20) | Para cada projeto de `PROJETOS-ATIVOS.md`, mantém um item especificado e (se `ativo`) um em desenvolvimento, abrindo tarefas no quadro | tabela `roadmap_ciclo`, `equipe_tarefas`; página `~/.agents/shared/rotinas/roadmap.md` |
| `vigia_saude.py` | a cada 15 min | Memória, swap, disco, gateway (ativo, memória, mortes por sinal/OOM), certificado de `agentes.avilaops.com`, última rodada do backup dos bancos do `applications`, se as rotinas publicadas acompanham a `main` e o resultado da conferência diária da suíte (`suite_da_main.py`). No fim, chama a limpeza de disco (`limpeza_disco.py`), que só age com o `/` acima de 75% | tabela `saude_medidas`; abre tarefa para `ops` só quando passa do limite; log da limpeza em `~/.local/state/rotinas-openclaw/limpeza-disco.log` |
| `backup_agentes.sh` | todo dia às 03:40 | Copia `openclaw.json`, exporta as rotinas, compacta os workspaces e faz `pg_dump` do banco `agentes`; confere cada arquivo | `~/backups/openclaw/AAAA-MM-DD/` (modo 700), 7 dias |
| `publica_rotinas.sh` | a cada 10 min (min. 07, 17, …) | Busca a `main` do GitHub e, se mudou, confere a sintaxe, roda os testes das rotinas daquele commit, publica a pasta `rotinas-openclaw` e troca o link `atual`. No fim chama o `suite_da_main.py` | `~/.local/share/rotinas-openclaw/` |
| `suite_da_main.py` | sem job próprio: o `publica_rotinas.sh` chama no fim de cada rodada, e ele só roda uma vez por dia ou quando o commit publicado muda | Extrai o commit publicado inteiro e roda a suíte toda (`python3 -m unittest discover -s tests`); não abre tarefa, quem alerta é o vigia (`vigia:suite-main`) | `~/.local/state/rotinas-openclaw/suite-main` |

Nenhum deles chama agente. Quem gasta modelo é a equipe, ao executar as tarefas que
estes scripts deixam `aberta` no quadro (o heartbeat do `coordinator` despacha uma
por batida).

## Publicação

Commit local ou edição sem commit em `~/projetos/infra` **não** entra em produção. O que
os jobs executam é o que está na `main` do GitHub, depois da conferência e do push:

```
~/.local/share/rotinas-openclaw/
  repo.git/            repositório próprio (bare), só recebe `git fetch` do GitHub
  releases/<commit>/   pasta rotinas-openclaw (e o teste) extraída daquele commit, sem escrita
  atual -> releases/<commit>     os jobs usam atual/rotinas-openclaw como pasta de trabalho
```

`publica_rotinas.sh` busca a `main`, extrai a pasta e `tests/test_rotinas_openclaw.py`,
confere a sintaxe (`.py` e `.sh`), roda esses testes contra a cópia extraída e só então
troca o link, de uma vez. Falhou em qualquer passo (rede, GitHub, commit sem a pasta ou
sem o arquivo de testes, erro de sintaxe, teste que não passa: saída 4): sai com código
diferente de zero **sem tocar no link**, e os jobs continuam na versão anterior. Guarda
as 3 últimas versões, sempre com a atual e a anterior.

A versão publicada fica **sem permissão de escrita** (`chmod -R a-w`), para ninguém
editar direto em `releases/<commit>` e o Python não deixar `__pycache__` lá: mudança é
commit e push. O script devolve a escrita só na hora de apagar uma versão antiga; para
apagar à mão, `chmod -R u+w releases/<commit>` antes do `rm -rf`.

- Depois do push, a publicação sai sozinha em até 10 minutos. Para não esperar:
  `~/.local/share/rotinas-openclaw/atual/rotinas-openclaw/publica_rotinas.sh`.
- Ver o que está no ar: `readlink ~/.local/share/rotinas-openclaw/atual`.
- Desfazer uma versão ruim: `git revert` + push (o caminho normal). O link pode ser
  apontado à mão para a versão anterior (`ln -sfn releases/<commit> atual`), mas a
  próxima publicação volta para a `main`.
- **Se o próprio `publica_rotinas.sh` publicado estiver com erro de lógica** (passa na
  sintaxe e nos testes, mas falha ou não troca o link), ele não consegue publicar a
  própria correção. Para destravar: corrigir em `~/projetos/infra`, commit e push na
  `main`, e rodar à mão a cópia da árvore de trabalho,
  `~/projetos/infra/rotinas-openclaw/publica_rotinas.sh` (ela também busca do GitHub,
  então só publica o que já está na `main`). Conferir com `readlink
  ~/.local/share/rotinas-openclaw/atual` e `openclaw cron runs
  cb9e0c56-d2fa-4812-9f91-84504a29f581 --limit 3`. Quem avisa que travou é o vigia
  (`vigia:rotinas-desatualizadas`, abaixo).
- Variáveis para teste: `ROTINAS_PUBLICADO` (destino), `ROTINAS_ORIGEM` (URL ou pasta do
  repositório), `ROTINAS_RAMO`, `ROTINAS_GUARDAR`.

## Instalar ou atualizar

```bash
psql -d agentes -v ON_ERROR_STOP=1 -f schema.sql      # idempotente
python3 -m unittest discover -s ../tests -v           # antes de enviar para a main
```

Os jobs estão no cron do OpenClaw (`openclaw cron list`). Para recriar um deles:

```bash
openclaw cron add --name "Varredura dos repositórios" --cron "5 * * * *" --tz America/Sao_Paulo \
  --command "python3 varredura_repos.py" \
  --command-cwd ~/.local/share/rotinas-openclaw/atual/rotinas-openclaw \
  --timeout-seconds 300 --no-deliver
```

## Rodar à mão

```bash
python3 varredura_repos.py --sem-banco --saida /tmp/repos.md   # só gera a página
python3 vigia_saude.py --sem-banco                             # só mede e imprime (limpeza só simula)
# sem --sem-banco é a rodada de verdade: grava no banco e a limpeza de disco APAGA
python3 limpeza_disco.py --limiar 50                           # simula a limpeza com outro limiar
python3 ciclo_roadmap.py --simula                              # mostra o que abriria
BACKUP_DESTINO=/tmp/bk ./backup_agentes.sh                     # backup em outra pasta
ROTINAS_PUBLICADO=/tmp/pub ./publica_rotinas.sh                # publica em outra pasta
python3 suite_da_main.py --forca --simula                      # roda a suíte da main publicada, sem gravar
ROTINAS_SUITE_ESTADO=/tmp/suite python3 vigia_saude.py --sem-banco   # o que o vigia diria de outro estado
```

Todos imprimem uma linha JSON com o resultado e saem com código diferente de zero
quando falham, que é o que o cron do OpenClaw registra.

## Limites do vigia

| Checagem | Limite padrão | Variável |
|---|---|---|
| Folga de memória (RAM disponível + swap livre) | menos de 1500 MB | `VIGIA_FOLGA_MIN_MB` |
| Disco `/` | mais de 85% | `VIGIA_DISCO_MAX_PCT` |
| Memória do gateway (RSS + swap do processo, `VmRSS` + `VmSwap` do `MainPID`) | mais de 2800 MB | `VIGIA_GATEWAY_MEM_MAX_MB` |
| Gateway morto por sinal/OOM | qualquer morte desde a medida anterior | — |
| Certificado público | menos de 14 dias | `VIGIA_CERT_MIN_DIAS` |
| Backup dos bancos do `applications` (`vigia:backup-applications`) | última rodada com saída diferente de 0, sem rodada há mais de 26 h, ou log sem leitura por SSH há mais de 26 h | `VIGIA_BACKUP_MAX_HORAS`, `VIGIA_BACKUP_HOST`, `VIGIA_BACKUP_LOG`, `VIGIA_BACKUP_ESTADO` |
| Suíte da `main` publicada (`vigia:suite-main`) | última conferência com falha (ou interrompida pelo tempo), sem conferência há mais de 30 h, ou versão no ar há mais de 2 h sem nenhuma conferência | `VIGIA_SUITE_MAX_HORAS`, `ROTINAS_SUITE_ESTADO` |
| Rotinas publicadas (`vigia:rotinas-desatualizadas`) | job `Publica rotinas` com erro há mais de 60 min, ou link `atual` diferente da `main` do GitHub | `VIGIA_ROTINAS_ERRO_MAX_MIN`, `VIGIA_ROTINAS_TOLERANCIA_MIN`, `VIGIA_ROTINAS_JOB` |

Tarefa aberta pelo vigia tem `chave` `vigia:<assunto>`: enquanto estiver aberta, novas
medidas só atualizam a nota. Quando o valor volta ao normal a tarefa é concluída
sozinha, exceto a de gateway morto, que espera alguém olhar a causa.

O backup é lido por SSH (`tail` de `/var/log/backup-bancos.log` no `applications`): a
última linha de cada rodada é `saida=N`, gravada pelo próprio
`/usr/local/bin/backup-todos-bancos.sh` ao sair, inclusive quando morre no meio. Se o
SSH não responder, a medida não abre nem encerra tarefa; a hora da última leitura boa
fica em `~/.local/state/rotinas-openclaw/backup-ultima-leitura` (`VIGIA_BACKUP_ESTADO`) e,
passadas 26 h sem leitura (chave revogada, host renomeado, log truncado), a tarefa é
aberta do mesmo jeito. Sem esse arquivo a contagem começa na rodada em que ele é criado.
A hora é gravada num temporário e trocada de uma vez: se não der para gravar (disco cheio,
pasta sem escrita), a hora anterior continua lá e o campo `backup` da medida diz "não foi
possível guardar a hora da leitura", porque aí o alerta das 26 h sem leitura não é confiável.
Linha de resultado com data sem fuso conta como data ilegível (alerta), não derruba o vigia.

As rotinas publicadas são conferidas com `openclaw cron runs` (últimas 12 execuções do
job `Publica rotinas`), `readlink` do link `atual` e `git ls-remote` na `main` do GitHub.
Logo depois de um push o link fica alguns minutos atrás da `main`, e isso não é alerta:
a diferença só conta quando a última execução do job não deu certo ou começou há mais de
20 min (`VIGIA_ROTINAS_TOLERANCIA_MIN`, duas batidas do job). A espera só vale para push
posterior à última execução: cada execução diz no resumo o commit que viu na `main`
(`{"publicado":false,"commit":"…"}`); se ela já viu o commit de agora (ou não disse
nenhum) e o link não foi para ele, o publicador saiu com 0 sem publicar e o alerta abre na
hora. Link `atual` cujo alvo não é um commit (40 hexadecimais) também alerta na hora,
mesmo sem o GitHub. Sem resposta do GitHub ou do gateway, a medida não abre nem encerra
tarefa; o erro há mais de 60 min abre mesmo sem o GitHub. As duas leituras
(`openclaw cron runs` e `git ls-remote`) têm limite de 10 s cada; estourou, conta como
sem resposta.

## Suíte da main publicada

O publicador só roda os testes no commit novo, e só `tests/test_rotinas_openclaw.py`.
Teste que estraga com o relógio (data fixa que vence) deixava a `main` vermelha sem
ninguém ver até o push seguinte, que aí não era publicado: foi assim de 08/10/2026
05:30 UTC até a tarefa 202. Por isso o `publica_rotinas.sh` chama, no fim de cada rodada,
o `suite_da_main.py` da versão publicada. Ele sai na hora quando não há o que fazer e
roda a suíte **inteira** do commit publicado (todos os `tests/test_*.py`, extraídos do
`repo.git` do publicador para uma pasta temporária, apagada no fim) quando:

- o commit publicado mudou desde a última conferência (até 10 min depois de cada push); ou
- a última conferência tem mais de 24 h (`ROTINAS_SUITE_INTERVALO_H`); ou
- a última conferência falhou há mais de 1 h (`ROTINAS_SUITE_REPETE_H`): falha do
  momento (suíte lenta por aperto de memória, por exemplo) não fica um dia em alerta.

O resultado (hora, commit, código de saída, quantidade de testes, até 8 testes que
falharam) fica em `~/.local/state/rotinas-openclaw/suite-main` (`ROTINAS_SUITE_ESTADO`),
gravado de uma vez (temporário de nome próprio na mesma pasta + troca). O conferidor não
abre tarefa e nunca muda a saída nem o resumo do publicador: quem lê o arquivo é o vigia, a cada 15 min, e abre `vigia:suite-main` para a
raia `ops` com o nome dos testes que falharam. A tarefa fecha sozinha na conferência
seguinte que passar (o push da correção já dispara uma).

- A suíte leva uns 25 s e tem limite de 75 s (`ROTINAS_SUITE_LIMITE_S`); extrair o commit
  tem 10 s para o `git archive` e 10 s para o `tar`. Passou do limite, a suíte inteira é
  morta (ela e os processos dos testes), fica gravada como falha (saída 124) e alerta.
- A conferência só usa o tempo que sobra do job do publicador: o teto é
  `ROTINAS_JOB_LIMITE_S` (120 s, igual ao limite do job no OpenClaw) menos o que a rodada
  já gastou e 10 s de folga para o resumo. Com menos de 30 s sobrando ela nem começa;
  interrompida no teto, não grava nada. Nos dois casos a rodada seguinte (10 min) tenta
  de novo, e o resumo e a saída do publicador não mudam.
- Interrompida por sinal (`SIGTERM`, `SIGHUP`: teto do publicador ou job morto), mata a
  suíte e apaga a pasta extraída antes de sair. Os temporários dos próprios testes ficam
  dentro dessa pasta (`TMPDIR`) e somem junto. Sobra de `suite-main-*` com mais de 1 h
  (morte sem chance de limpar) é apagada no começo da conferência seguinte.
- Se o job for morto antes de gravar (ou o `repo.git` não tiver o commit, ou `/tmp`
  estiver cheio), o estado antigo fica e a rodada seguinte tenta de novo; passadas 30 h
  sem conferência o vigia alerta do mesmo jeito.
- Arquivo de estado estragado (JSON inválido, bytes que não são UTF-8, `falhas` que não é
  lista) é alerta "ilegível", nunca derruba o vigia nem fica mudo; só falha de leitura do
  arquivo (permissão, E/S) conta como sem leitura.
- O alerta diz que a `main` está vermelha **hoje**; conserto é commit e push, como
  qualquer outro. Enquanto ela estiver vermelha por um teste das rotinas, nenhum commit
  novo é publicado.
- À mão: `suite_da_main.py --forca` roda agora e grava; `--simula` roda e imprime sem
  gravar (o vigia não fica sabendo). Sai com 0 se passou ou não era hora, 1 se a suíte
  falhou, 2 se não deu para conferir.

## Limpeza de disco

`limpeza_disco.py` não tem job próprio: o vigia o chama a cada 15 min, **depois** de
gravar a medida e os alertas. Qualquer falha dela (até de importação) vira `erro` no
campo `limpeza` da linha JSON do vigia e não muda a saída do job. Ela só age com o `/`
acima de 75% (`LIMPEZA_DISCO_PCT`), medido como o `df` mostra (sobre o espaço que o
usuário pode ocupar). O `vigia:disco` (85%) usa a mesma conta desde a tarefa 187; antes
media sobre o disco inteiro, uns 4 pontos abaixo, e só alertava perto de 90% no `df`. As
medidas antigas de `saude_medidas.disco_pct` estão na conta antiga.

| Etapa | O que faz | Quando pula |
|---|---|---|
| `npm_cache` | `npm cache clean --force` (só `~/.npm/_cacache`; `_npx` fica) | há processo `npm`/`npx`/`pnpm`/`yarn` rodando; cache limpo há menos de 6 h (`LIMPEZA_NPM_INTERVALO_H`); sem `npm` no PATH |
| `tmp_cmake` | apaga em `/tmp` os diretórios que têm `CMakeCache.txt` e `CMakeFiles/`, parados há mais de 6 h (`LIMPEZA_TMP_HORAS`; conta o arquivo mais novo da árvore) | processo com pasta de trabalho, executável, arquivo aberto ou mapeado ali; pasta de outro usuário; pasta com `CMakeLists.txt`, `.git` ou `package.json` (build dentro do código); sem `CMakeFiles/`; `CMakeCache.txt` sem `CMAKE_HOME_DIRECTORY`, com caminho relativo ou com a fonte dentro da pasta; `CMakeLists.txt` em qualquer nível, fora de `_deps/` e `CMakeFiles/`; arquivo `.md`, `.txt`, `.patch`, `.diff`, `.c`, `.cc`, `.cpp`, `.cxx`, `.py` ou `.sh` solto na raiz do build (fora `CMakeCache.txt` e `install_manifest*.txt`); outro disco montado dentro; pasta trocada por outra depois de avaliada |
| `apt` | `sudo -n apt-get clean` | sem pacote em `/var/cache/apt/archives`; sem sudo sem senha para o comando (pula sem erro) |
| `docker_imagens` | `docker rmi <nome:tag>` (sem `-f`) de cada imagem com tag e sem contêiner | algum cliente `docker`/`docker-compose`/`docker-buildx` rodando (build, `save`, `load`, `run`); imagem com contêiner, mesmo parado; `postgres:18-alpine` e qualquer outro nome da mesma imagem; imagem sem tag; criada há menos de 2 h (`LIMPEZA_DOCKER_HORAS`); `docker images`, `ps` ou `inspect` sem resposta; contêiner criado entre a lista e o `rmi` |
| `claude_logs` | apaga os arquivos de `~/.cache/claude-cli-nodejs/*/mcp-logs*/` parados há mais de 1 dia (`LIMPEZA_LOGS_HORAS`); as pastas ficam | arquivo aberto por processo; fora de pasta `mcp-logs*`; de outro usuário; link simbólico |
| `node_compile_cache` | apaga os arquivos de `/tmp/node-compile-cache` não lidos nem gravados há mais de 1 dia (`LIMPEZA_LOGS_HORAS`) | arquivo aberto ou mapeado por processo; de outro usuário (os do root ficam) |
| `npx` | apaga entradas inteiras de `~/.npm/_npx` sem leitura nem gravação há mais de 12 h (`LIMPEZA_NPX_HORAS`) | `npm`/`npx` baixando (mesma regra do `npm_cache`); processo com a entrada na linha de comando (caso dos servidores MCP), como pasta de trabalho, executável ou arquivo aberto; conferido de novo na hora de apagar |
| `pnpm_store` | apaga de `~/.local/share/pnpm/store/v*/files` os arquivos com um só link (nenhum `node_modules` aponta para eles) há mais de 1 h (`LIMPEZA_PNPM_HORAS`) | `pnpm`/`npm`/`yarn` rodando; arquivo com mais de um link; aberto por processo; o índice do store não é tocado |
| `next` | apaga o `.next` dos repositórios de `~/projetos` (até 3 níveis abaixo da raiz) | árvore git suja (`git status --porcelain`, com os não versionados) ou git sem resposta; processo com pasta de trabalho, executável, arquivo aberto ou linha de comando dentro do repositório; sem `package.json` ao lado; `.next` não ignorado pelo git ou com arquivo versionado dentro; link simbólico; arquivo `*.antes-t*` dentro; conferido de novo na hora de apagar |
| `node_modules` | **só com o `/` acima de 80%** (`LIMPEZA_NODE_MODULES_PCT`, medido na hora da etapa): apaga o `node_modules` dos repositórios de `~/projetos` | tudo o que segura o `next`, e mais: tarefa `em_andamento` no quadro com o nome do repositório na coluna `repo` ou no pedido; quadro sem resposta (a etapa inteira não roda); último commit de qualquer ramo, local ou remoto, há menos de 24 h (`LIMPEZA_NODE_MODULES_HORAS`); arquivo do `node_modules` mexido há menos de 24 h; sem lockfile ao lado nem na raiz do repositório |

- `npm exec`/`npx` que já subiu o programa (tem processo filho, caso dos servidores MCP
  das sessões, vivos por dias) não conta como npm rodando; sem filho, ainda está baixando
  e conta.
- Em `/tmp` a procura vai até 6 níveis, não segue link simbólico, não passa para outro
  disco, não entra em `node_modules` nem `.git` e nunca apaga o próprio `/tmp`. Ficam a
  fonte, os logs e as saídas de teste ao lado do build. O caminho não é configurável
  (nem por `TMPDIR`).
- **Não guarde nada em pasta de build em `/tmp`.** Use `cmake -B /tmp/<nome>/build` e
  deixe anotação, remendo e rascunho fora dela. A trava do arquivo solto só olha a raiz
  do build e só as extensões da tabela: arquivo de outro tipo, ou em subpasta, sai junto.
  Remendo feito em `_deps/<lib>-src/` (código que o CMake baixou) também sai: leve-o para
  o projeto.
- Nada além disso é tocado. Tudo o que as etapas `claude_logs` a `node_modules` apagam
  passa por `recusa()`, que barra `/opt/backups`, `/var/lib/docker` (volumes),
  `/var/lib/postgresql`, `~/.openclaw`, `~/.agents`, qualquer pasta que contenha uma
  dessas, arquivo ou pasta `*.antes-t*`, link simbólico, o que é de outro usuário e o que
  fica fora da pasta da etapa, mesmo que o caminho chegue lá por engano. O docker só
  recebe `images`, `ps`, `inspect`, `image inspect` e `rmi <nome:tag>`: volume, contêiner,
  `prune` e `-f` são recusados no próprio código. Em repositório só saem `.next` e
  `node_modules`; código, `.git` e `.work/` ficam.
- O `node_modules` é a única etapa cara de refazer (`npm ci`) e por isso é a última, só
  roda acima de 80% e mede o disco de novo na hora: se as etapas anteriores já baixaram o
  uso, ela não roda. Depois dela, `npm ci` (ou `corepack pnpm install`) refaz a pasta.
- O `.next` sai mesmo de repositório com commit recente: quem precisar roda `npm run build`.
- A leitura (`atime`) usada no `npx` e no `node-compile-cache` vem do disco montado com
  `relatime`: o sistema só a atualiza uma vez por dia. No `npx` (12 h) uma entrada pode
  parecer parada tendo sido lida depois; a trava que vale ali é a do processo. O que sair
  volta sozinho no próximo `npx`.
- Pasta segurada por uma trava barata aparece em `mantidos` com 0 MB: só se mede o que
  passou por todas elas. Imagem Docker conta o tamanho inteiro, mesmo dividindo camadas.
- As dez etapas dividem 60 s (`LIMPEZA_ORCAMENTO_S`; o job do vigia tem 90 s). O que não
  couber fica para a rodada seguinte: a procura em `/tmp`, a avaliação de cada build e a
  leitura de `/proc` param quando o tempo acaba, e sem saber quem usa a pasta nada é
  apagado. Só o apagar de uma pasta já começado não é interrompido.
- Um `npm ci` que comece no instante entre a checagem e o fim do `npm cache clean` pode
  falhar com `ENOENT`; rodar de novo resolve, nada se perde.
- Cada rodada que apagou algo ou falhou grava uma linha em
  `~/.local/state/rotinas-openclaw/limpeza-disco.log` (`LIMPEZA_ESTADO`; últimas 500):
  livre antes e depois, liberado por etapa e o que foi apagado e mantido. O resumo sai sempre no
  campo `limpeza` da linha do vigia (`openclaw cron runs f5f8d4fd-5ba9-4bfc-adb0-7afea58df761`).
- `LIMPEZA_DISCO` no job do vigia: `1` apaga (padrão), `simula` só lista, `0` desliga.
  `vigia_saude.py --sem-banco` sempre simula. **Sem `--sem-banco`, o vigia rodado à mão
  faz o mesmo que o job: grava no banco e apaga de verdade.**
- À mão, `limpeza_disco.py` só simula (ensaio): lista em `itens` o que apagaria, em
  `mantidos` o que ficou e por quê, e soma em `estimado_mb` quanto a rodada de verdade
  liberaria. Apagar exige `--executa`. `--limiar PCT` troca o limiar da rodada.

## Ciclo de roadmap

`~/.openclaw/workspace/roadmap/PROJETOS-ATIVOS.md` é a chave: `ativo` especifica e
desenvolve, `a confirmar` só especifica, qualquer outra palavra tira o projeto do ciclo.
O roadmap do repositório é o primeiro `roadmap*.md` (raiz ou um nível abaixo) da branch
principal, e só contam linhas `- [ ]`.

1. O script abre `roadmap:<repo>:spec` para a raia `roadmap`, com os próximos 8 itens.
   A raia escolhe o primeiro executável, escreve a especificação e fecha a tarefa com a
   nota começando por `item=<id>; pulados=<id>,<id>`.
2. Com o item especificado e o projeto `ativo`, o script abre `roadmap:<repo>:dev` para
   o `engineer`, que implementa, testa, marca `- [x]` no roadmap e envia para a `main`.
3. Quando o item some da lista de abertos na `main`, o ciclo o dá por concluído e volta
   ao passo 1.

No máximo 3 tarefas de roadmap abertas no quadro ao mesmo tempo (`ROADMAP_MAX_ABERTAS`).

## Restaurar um backup

```bash
cd ~/backups/openclaw/AAAA-MM-DD
pg_restore -d agentes --clean --if-exists agentes.dump
tar -C ~/.openclaw -xzf workspaces.tar.gz
cp openclaw.json ~/.openclaw/openclaw.json && openclaw config validate
```

O `openclaw.json` do backup contém a senha do gateway: a pasta fica em modo 700 e
**nunca** entra em repositório.
