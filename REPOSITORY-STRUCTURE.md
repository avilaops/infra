# Estrutura do repositório

Este repositório é a fonte central da infraestrutura da Avila Ops. Cada área tem uma responsabilidade própria:

```text
.github/workflows/       workflows reutilizáveis de build, GHCR e SSH
deploy/production/       destinos autorizados no servidor de aplicações
monitoring/              stack de observabilidade do servidor dedicado
producao-agents/         coletores instalados no servidor de aplicações
levantamento/            inventário somente leitura dos servidores
scripts/                 publicação, rollback e verificações
docs/                    documentação operacional futura
```

O servidor de aplicações é `178.105.82.48`. O servidor de observabilidade é `23.88.60.193`. Segredos, arquivos `.env`, dumps e dados de volumes ficam fora do GitHub.

O Compose em `docker-compose.yml` é executado no servidor de observabilidade. O Compose em `producao-agents/` é executado no servidor de aplicações e não contém os aplicativos dos clientes.
