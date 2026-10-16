# Rotinas do servidor creators

Tarefas que o cron do usuário `avops` roda no `creators`. Cada rotina tem a
própria pasta, com o script e, quando chama o Claude Code, as instruções.

| Rotina | Quando | O que faz |
|---|---|---|
| [`planejador-redigir`](planejador-redigir/) | minutos 20 e 50 de cada hora | Reescreve, canal por canal, os rascunhos que o planejador de posts criou com texto de modelo. Não publica nada. |

## planejador-redigir

- **Depende de:** Claude Code logado no usuário `avops`, servidor MCP `avilaops`
  registrado nele (`claude mcp list`) e a chave do planejador em
  `~/.agents/claude/out/planejador.env`.
- **Só gasta quando há trabalho:** antes de chamar o Claude, pergunta a
  `https://n8n.avilaops.com/webhook/planejador-a-redigir` quantas peças esperam
  redação e sai se for zero.
- **Ferramentas liberadas:** `planejador_rascunhos`, `planejador_redigir` e
  `WebFetch` restrito a `avilaops.com`.
- **Registro:** `~/.local/state/rotinas/planejador-redigir.log`.
- **Instalação no cron:**

  ```cron
  20,50 * * * * $HOME/projetos/infra/creators/rotinas/planejador-redigir/rodar.sh
  ```

- **Regras de redação:** [`planejador-redigir/prompt.md`](planejador-redigir/prompt.md).
  Mudar o tom ou o tamanho de um canal é editar esse arquivo.

## Inventário de capacidades (systemd, não cron)

O `/usr/local/bin/inventario-capacidades` manda ao n8n, todo dia às 12:00 UTC, os plugins,
skills, hooks e MCPs do Claude Code e da `~/.agents` do `avops`, e as skills dos projetos em
`/opt`. Roda pelo `inventario-capacidades.timer` do sistema com o usuário `avops`
(`/etc/systemd/system/inventario-capacidades.service.d/usuario.conf`); a configuração
`/etc/inventario-capacidades.env` é `root:avops` 640 e o log
`/var/log/inventario-capacidades.log` é do `avops`. Detalhes em
[`inventario-capacidades/README.md`](../../inventario-capacidades/README.md).
