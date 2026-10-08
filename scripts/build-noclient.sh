#!/usr/bin/env bash
# Build manual de imagem no apps-noclient, quando o GitHub Actions nao pode
# construir. Instalado como /usr/local/sbin/avila-build.
#
# Uso (da maquina de quem publica, com o codigo do commit pela entrada padrao):
#   git archive --format=tar <sha> | ssh apps-noclient avila-build <repositorio> <sha> [dockerfile]
#
# Produz a MESMA imagem que .github/workflows/container.yml produziria:
# ghcr.io/avilaops/<repositorio>:sha-<sha de 40>, com os mesmos rotulos e o
# mesmo GIT_SHA. O que muda e o transporte: em vez de ir ao GHCR, a imagem sai
# num arquivo que scripts/deploy-container-local.sh carrega no destino.
#
# O codigo chega por tar e nao por `git clone` de proposito: este servidor nao
# guarda credencial do GitHub, e o que entra e exatamente o commit pedido.
set -euo pipefail
umask 077

fail() { printf '%s\n' "$*" >&2; exit 1; }

[[ $EUID == 0 ]] || fail 'O build precisa executar como root.'
[[ $# == 2 || $# == 3 ]] || fail 'Uso: avila-build <repositorio> <sha de 40> [dockerfile]'
repository=$1
sha=$2
dockerfile=${3:-Dockerfile}
[[ "$repository" =~ ^[a-z0-9][a-z0-9._-]*$ ]] || fail 'Repositorio invalido.'
[[ "$sha" =~ ^[a-f0-9]{40}$ ]] || fail 'O commit precisa ser o SHA completo, de 40 caracteres.'
[[ "$dockerfile" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ && "$dockerfile" != *..* ]] || fail 'Dockerfile invalido.'
[[ ! -t 0 ]] || fail 'O codigo do commit precisa vir pela entrada padrao (git archive).'

base=/var/lib/avilaops/build
artifacts=$base/artefatos
install -d -m 700 "$base" "$artifacts"
exec 9>"$base/$repository.lock"
flock -w 900 9 || fail 'Outro build deste repositorio continua em andamento.'

image="ghcr.io/avilaops/$repository:sha-$sha"
artifact="$artifacts/$repository-sha-$sha.tar.gz"
work=$(mktemp -d "$base/src.XXXXXX")
# O codigo-fonte nao fica no servidor: a pasta some inclusive quando o build falha.
trap 'rm -rf "$work"' EXIT

tar -x -C "$work"
[[ -f "$work/$dockerfile" ]] || fail 'Dockerfile nao encontrado no commit enviado.'

printf '==> construindo %s\n' "$image" >&2
# </dev/null: docker e gzip nao podem ler a entrada padrao, que ja foi consumida.
docker build \
  --platform linux/amd64 \
  --file "$work/$dockerfile" \
  --tag "$image" \
  --label "org.opencontainers.image.source=https://github.com/avilaops/$repository" \
  --label "org.opencontainers.image.revision=$sha" \
  --build-arg "GIT_SHA=$sha" \
  "$work" </dev/null >&2

docker save "$image" | gzip -1 > "$artifact.parcial"
mv "$artifact.parcial" "$artifact"

# Artefato e passagem, nao arquivo: quem publica apaga depois de transferir, e
# o que sobrar de um dia para o outro sai aqui. Versao antiga e republicar o commit.
find "$artifacts" -maxdepth 1 -type f -mmin +1440 -delete

printf 'IMAGEM=%s\n' "$image"
printf 'ARTEFATO=%s\n' "$artifact"
printf 'SHA256=%s\n' "$(sha256sum "$artifact" | cut -d' ' -f1)"
