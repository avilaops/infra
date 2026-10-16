# Inventário de capacidades

Uma vez por dia, cada máquina da casa levanta o que os agentes de IA dela têm instalado e
manda para o n8n. A tabela "Biblioteca de capacidades - inventario" vira a consulta técnica
de tudo o que existe e onde: plugin, skill, hook e servidor MCP, por máquina e por agente.

| Peça | Onde |
|---|---|
| Script | `inventario_capacidades.py` (Python 3, só biblioteca padrão) |
| Fluxo do n8n | "[Ops] Inventário de capacidades — Ávila Ops" (`ZetiNz59Zav32aNm`) |
| Tabela | "Biblioteca de capacidades - inventario" (`2PSYRmNvcYugylfS`) |
| Aviso | WhatsApp do Nicolas, pelo mesmo serviço do "Infra - Aviso de disco", só quando algo muda |

## O que entra

| Agente | De onde |
|---|---|
| `claude` | plugins instalados e os sincronizados da conta claude.ai, `~/.claude/skills`, hooks do `settings.json`, MCPs do `~/.claude.json` |
| `codex` | plugins do `config.toml` (com a pasta em `plugins/cache`), `~/.codex/skills`, `hooks.json`, `mcp_servers` |
| `cursor` | `~/.cursor/skills`, `~/.cursor/skills-cursor`, `mcp.json`, plugins em `plugins/cache` |
| `grok` | `~/.grok/skills` |
| `agents` | `~/.agents/skills`, a pasta compartilhada que Codex, Cursor e Grok leem |
| projetos | em cada pasta de `INVENTARIO_PROJETOS`: `.agents/`, `.claude/`, `.codex/`, `.cursor/`, `.grok/skills`, `skills/` na raiz e o `.mcp.json` |

Dentro de cada plugin entram também as skills, os hooks e os MCPs que ele traz, com o nome
`plugin:item`. Plugin ligado na configuração mas sem pasta aparece com origem
`(pasta ausente)`.

Só saem nome, versão, caminho e um hash curto do conteúdo. De servidor MCP vai só o programa
ou o endereço sem parâmetros; argumento, variável de ambiente e cabeçalho ficam na máquina,
porque é ali que costuma morar token. De hook vai só o programa, e o hash cobre o comando
inteiro.

## Como a tabela é mantida

A chave de cada linha é `maquina|agente|escopo|tipo|nome`. A cada envio o fluxo:

- insere o item que a tabela ainda não tinha;
- atualiza versão, hash e origem do item que mudou;
- marca como `removido` (sem apagar) o item que sumiu da máquina, e volta para `ativo` se ele
  reaparecer;
- avisa no WhatsApp quando alguma dessas três coisas acontece.

| Coluna | Conteúdo |
|---|---|
| `maquina` | `pc-nicolas`, `apps-noclient`, `applications` ou `creators` |
| `agente` | `claude`, `codex`, `cursor`, `grok`, `agents` ou `repositorio` |
| `escopo` | `global` ou `projeto:<pasta>` |
| `tipo` | `plugin`, `skill`, `hook` ou `mcp` |
| `situacao` | `ativo`, `desligado` (plugin desligado e o que vem nele) ou `removido` |
| `visto_em` | quando o item entrou ou mudou pela última vez |

As 285 linhas com `maquina` vazia são o levantamento único de 14/10/2026 no servidor do
n8n, feito para a migração de skills para o n8n. O fluxo não mexe nelas.

O webhook não tem senha além do caminho. Por isso o fluxo só aceita máquina da lista, até
8.000 itens e campos no formato esperado; o resto é descartado.

## Instalação

A configuração fica fora do Git:

```text
INVENTARIO_WEBHOOK=https://n8n.avilaops.com/webhook/<caminho do fluxo>
INVENTARIO_MAQUINA=pc-nicolas
INVENTARIO_PROJETOS=D:\Projetos
```

O caminho do webhook está no nó "Inventário da máquina" do fluxo. `INVENTARIO_PROJETOS`
aceita várias pastas, separadas por `;` no Windows e `:` no Linux.

### PC Windows

Configuração em `%APPDATA%\inventario-capacidades\config.env`. Depois:

```powershell
powershell -ExecutionPolicy Bypass -File inventario-capacidades\instalar-windows.ps1
```

O instalador copia o script para `%LOCALAPPDATA%\inventario-capacidades` e registra a
tarefa agendada "Inventario de capacidades": todo dia às 09:00 e 10 minutos depois do logon,
com `pythonw` (sem janela). Log em `%LOCALAPPDATA%\inventario-capacidades\inventario.log`.
Rode o instalador de novo depois de mudar o script.

### Servidor Linux

Configuração em `/etc/inventario-capacidades.env` (600). Depois:

```bash
install -m 755 inventario-capacidades/inventario_capacidades.py /usr/local/bin/inventario-capacidades
install -m 644 inventario-capacidades/inventario-capacidades.service inventario-capacidades/inventario-capacidades.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now inventario-capacidades.timer
```

Roda todo dia às 12:00 UTC (com até 15 minutos de folga). Log em
`/var/log/inventario-capacidades.log`.

## Conferir

```bash
inventario-capacidades --simula                   # mostra o resumo, sem enviar
inventario-capacidades --simula --saida inv.json  # e grava o JSON que seria enviado
```

A resposta do n8n traz `novos`, `alterados`, `removidos` e `voltaram`. Duas rodadas seguidas
sem mudança na máquina respondem tudo zero.
