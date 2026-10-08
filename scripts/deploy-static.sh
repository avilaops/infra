#!/usr/bin/env bash
# A imagem ja foi autenticada e baixada pelo dispatcher.
set -euo pipefail
[[ $EUID == 0 && $# == 3 ]] || exit 1
domain=$1
image=$2
web_root=$3
[[ "$domain" =~ ^[a-z0-9][a-z0-9.-]*$ ]] || exit 1
[[ "$web_root" == /var/www/* && "$web_root" != *..* ]] || exit 1
[[ -d "$web_root" ]] || { echo 'Diretorio do site nao instalado.' >&2; exit 1; }
digest=${image##*@sha256:}
[[ "$digest" =~ ^[a-f0-9]{64}$ ]] || exit 1
releases="/var/www/.releases/$domain"
install -d -m 755 /var/www/.releases
install -d -m 755 "$releases"
release=$(mktemp -d "$releases/$digest.XXXXXX")
temporary_container=''
temporary_link="$web_root.next"
cleanup() {
  if [[ -n "$temporary_container" ]]; then docker rm "$temporary_container" >/dev/null; fi
  rm -f "$temporary_link"
}
trap cleanup EXIT
temporary_container=$(docker create --network none --entrypoint /bin/true "$image")
docker cp "$temporary_container:/usr/share/nginx/html/." "$release/"
docker rm "$temporary_container" >/dev/null
temporary_container=''
[[ -s "$release/index.html" ]] || { echo 'Imagem sem index.html.' >&2; exit 1; }
printf '%s\n' "$digest" > "$release/avila-deploy-version.txt"
chown -R root:root "$release"
chmod -R a+rX "$release"

if [[ -L "$web_root" ]]; then
  previous=$(readlink -f "$web_root")
else
  previous="$releases/anterior-$(date +%s%N)"
  mv -T "$web_root" "$previous"
fi
rollback() {
  local status=$?
  trap - EXIT INT TERM
  ln -sfn "$previous" "$temporary_link"
  mv -Tf "$temporary_link" "$web_root"
  cleanup
  echo 'Deploy do site falhou. Versao anterior restaurada.' >&2
  (( status != 0 )) || status=1
  exit "$status"
}
trap rollback EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
ln -sfn "$release" "$temporary_link"
mv -Tf "$temporary_link" "$web_root"
verified=false
for (( attempt=0; attempt<20; attempt++ )); do
  version=$(curl --silent --fail --max-time 5 --resolve "$domain:443:127.0.0.1" "https://$domain/avila-deploy-version.txt" || true)
  if [[ "$version" == "$digest" ]] && curl --silent --fail --max-time 5 --resolve "$domain:443:127.0.0.1" "https://$domain/" >/dev/null; then
    verified=true
    break
  fi
  sleep 3
done
[[ "$verified" == true ]] || exit 1
trap cleanup EXIT
trap - INT TERM
# Versao aprovada: as anteriores so serviam para o rollback acima. Quem guarda
# versoes e o GitHub; no servidor elas acumulavam na raiz de 38 GB.
for old in "$releases"/*; do
  [[ "$old" == "$release" ]] || rm -rf -- "$old"
done
printf 'Site publicado: %s %s\n' "$domain" "$digest"
