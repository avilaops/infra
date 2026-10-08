#!/usr/bin/env bash
# Recebe somente imagens ja verificadas e baixadas pelo dispatcher.
set -euo pipefail
[[ $EUID == 0 && $# == 2 ]] || exit 1
application=$1
image=$2
# shellcheck source=/dev/null
source "/etc/avilaops/deploy/$application.conf"
: "${APP_ROOT:?}" "${SYSTEMD_UNITS:?}" "${ENTRYPOINT:?}" "${HEALTH_URL:?}"
[[ "$APP_ROOT" == /opt/* && "$APP_ROOT" != *..* && -d "$APP_ROOT" ]] || exit 1
read -r -a units <<< "$SYSTEMD_UNITS"
for unit in "${units[@]}"; do [[ "$unit" =~ ^[a-zA-Z0-9@_.-]+\.service$ ]] || exit 1; done
releases="/opt/.releases/$application"
install -d -m 755 /opt/.releases
install -d -m 755 "$releases"
release=$(mktemp -d "$releases/release.XXXXXX")
temporary_container=''
temporary_link="$APP_ROOT.next"
cleanup() {
  if [[ -n "$temporary_container" ]]; then docker rm "$temporary_container" >/dev/null; fi
  rm -f "$temporary_link"
}
trap cleanup EXIT
temporary_container=$(docker create --network none --entrypoint /bin/true "$image")
docker cp "$temporary_container:/app/." "$release/"
docker rm "$temporary_container" >/dev/null
temporary_container=''
[[ -s "$release/$ENTRYPOINT" ]] || { echo 'Imagem sem ponto de entrada.' >&2; exit 1; }
chmod -R a+rX "$release"
for filename in .env .env.production; do
  if [[ -f "$APP_ROOT/$filename" ]]; then
    ln -sfn "$(readlink -f "$APP_ROOT/$filename")" "$release/$filename"
  fi
done
if [[ -d "$release/.next" ]]; then
  install -d -o "${APP_USER:-root}" -m 755 "$release/.next/cache"
fi
if [[ -L "$APP_ROOT" ]]; then
  previous=$(readlink -f "$APP_ROOT")
else
  previous="$releases/anterior-$(date +%s%N)"
  # Ajusta os links antes de mover o diretorio que guarda o ambiente original.
  for filename in .env .env.production; do
    if [[ -f "$APP_ROOT/$filename" && ! -L "$APP_ROOT/$filename" ]]; then
      ln -sfn "$previous/$filename" "$release/$filename"
    fi
  done
  mv -T "$APP_ROOT" "$previous"
fi
units_active() {
  for unit in "${units[@]}"; do systemctl is-active --quiet "$unit" || return 1; done
}
healthy() {
  local attempt code
  for (( attempt=0; attempt<30; attempt++ )); do
    code=$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 5 "$HEALTH_URL" || true)
    if [[ "$code" =~ ^2[0-9]{2}$ || "$code" == "${HEALTH_STATUS:-200}" ]] && units_active; then
      # shellcheck disable=SC2016
      if [[ "${SMTP_CHECK:-false}" != true ]] || timeout 5 bash -c 'IFS= read -r line < /dev/tcp/127.0.0.1/25; [[ "$line" == 220* ]]'; then
        return 0
      fi
    fi
    sleep 3
  done
  return 1
}
rollback() {
  local status=$?
  trap - EXIT INT TERM
  ln -sfn "$previous" "$temporary_link"
  mv -Tf "$temporary_link" "$APP_ROOT"
  if systemctl restart "${units[@]}" && healthy; then
    echo 'Versao anterior restaurada.' >&2
  else
    echo 'Falha ao restaurar servicos. Intervencao necessaria.' >&2
  fi
  cleanup
  (( status != 0 )) || status=1
  exit "$status"
}
trap rollback EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
ln -sfn "$release" "$temporary_link"
mv -Tf "$temporary_link" "$APP_ROOT"
systemctl restart "${units[@]}"
healthy || exit 1
trap cleanup EXIT
trap - INT TERM
# Versao aprovada: as anteriores so serviam para o rollback acima. Quem guarda
# versoes e o GitHub; no servidor elas acumulavam na raiz de 38 GB. Fica tambem
# a pasta que guarda o .env de verdade: no primeiro deploy ele continua dentro
# do "anterior-*" e as releases seguintes so apontam para ele.
keep=("$release")
for filename in .env .env.production; do
  [[ -L "$release/$filename" ]] && keep+=("$(dirname "$(readlink -f "$release/$filename")")")
done
for old in "$releases"/*; do
  for kept in "${keep[@]}"; do [[ "$old" == "$kept" ]] && continue 2; done
  rm -rf -- "$old"
done
printf 'Servicos publicados: %s %s\n' "$application" "$image"
