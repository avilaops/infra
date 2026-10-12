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
