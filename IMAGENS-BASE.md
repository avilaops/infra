# Imagens base

As aplicações não baixam imagem base do Docker Hub. Usam a cópia em
`ghcr.io/avilaops/base/<imagem>`, mantida por
`.github/workflows/imagens-base.yml`.

## Por quê

O Docker Hub limita download anônimo por IP, e os runners do GitHub dividem IP
com todo mundo. Em 09/10/2026 o build do `lojas.avilaops.com` falhou três vezes
seguidas com `429 Too Many Requests` em `FROM node:22-bookworm-slim`, antes de
compilar qualquer coisa. Build parado é deploy parado.

Login no Docker Hub em cada aplicação exigiria o mesmo token copiado em dezenas
de repositórios: os repositórios `avilaops` não têm secret compartilhado.
O espelho concentra o token aqui e deixa as aplicações sem secret.

## Como funciona

- Todo dia às 05:17 UTC, e a cada mudança no workflow, cada imagem da matriz é
  copiada com `docker buildx imagetools create`: o índice multi-arquitetura
  inteiro, com o mesmo digest da origem. É a imagem oficial, não um rebuild.
- O job confere que o digest da cópia é igual ao da origem.
- O login no Docker Hub usa os secrets `DOCKERHUB_USERNAME` e
  `DOCKERHUB_TOKEN` deste repositório (token só leitura, "Public Repo
  Read-only").
- Pull request só testa a cópia (`--dry-run`); quem publica é a `main`.

## Usar numa aplicação

```dockerfile
FROM ghcr.io/avilaops/base/node:22-bookworm-slim AS base
```

O pacote é público: o build da aplicação baixa sem login e sem secret.

## Acrescentar uma imagem

1. Inclua `nome:tag` na matriz de `.github/workflows/imagens-base.yml`. Só
   imagens oficiais (`docker.io/library/...`).
2. Depois que o workflow rodar na `main`, deixe o pacote novo público:
   github.com/avilaops?tab=packages, abra `base/<nome>`, **Package settings** >
   **Change visibility** > **Public**. Pacote novo nasce privado no GHCR, e
   pacote privado faz o build de outro repositório falhar com `denied`.
3. Troque o `FROM` da aplicação.

## Trocar o token do Docker Hub

Gere outro em hub.docker.com > Account settings > Personal access tokens
(permissão Public Repo Read-only), atualize `DOCKERHUB_TOKEN` em
Settings > Secrets and variables > Actions deste repositório e revogue o
antigo. Nenhuma aplicação precisa mudar.
