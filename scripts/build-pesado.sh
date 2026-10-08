#!/usr/bin/env bash
# Um build pesado por vez no servidor dos agentes (creators, 4 GB de RAM), com
# teto de heap no Node. Instalado como ~/.local/bin/build-pesado.
#
# Uso: build-pesado <comando> [argumentos...]
#   build-pesado npm run build
#   build-pesado docker build --build-arg NODE_OPTIONS=--max-old-space-size=1024 -t <imagem> .
#
# Existe porque o gateway do OpenClaw foi morto por OOM em 08/10/2026 com tres
# builds de Node ao mesmo tempo (tarefas 231 e 239). Todo build, teste longo ou
# docker build disparado aqui passa por este script: quem chega depois espera a
# trava, e desiste com erro se ela nao soltar no prazo.
#
# O teto de heap vale para o Node que roda direto no servidor. Dentro de
# `docker build` o ambiente de quem chama nao entra: o Dockerfile precisa
# declarar `ARG NODE_OPTIONS` e `ENV NODE_OPTIONS=$NODE_OPTIONS` no estagio de
# build e receber o valor por --build-arg (o --memory e ignorado pelo BuildKit).
#
# Variaveis (todas opcionais):
#   BUILD_PESADO_LOCK      arquivo da trava       (padrao /var/lock/build-pesado.lock)
#   BUILD_PESADO_ESPERA_S  espera maxima, em s    (padrao 1800)
#   BUILD_PESADO_HEAP_MB   teto do heap do Node   (padrao 1024)
#
# Saida: a do comando; 75 se a trava nao soltou no prazo; 64 em erro de uso.
set -euo pipefail

aviso() { printf 'build-pesado: %s\n' "$*" >&2; }

(( $# > 0 )) || { aviso 'uso: build-pesado <comando> [argumentos...]'; exit 64; }

lock=${BUILD_PESADO_LOCK:-/var/lock/build-pesado.lock}
espera=${BUILD_PESADO_ESPERA_S:-1800}
heap=${BUILD_PESADO_HEAP_MB:-1024}
[[ "$espera" =~ ^[0-9]+$ ]] || { aviso 'BUILD_PESADO_ESPERA_S precisa ser um numero inteiro de segundos.'; exit 64; }
[[ "$heap" =~ ^[1-9][0-9]*$ ]] || { aviso 'BUILD_PESADO_HEAP_MB precisa ser um numero inteiro de MB.'; exit 64; }

# Quem chama ja escolheu um teto: fica o dele. Os demais valores de
# NODE_OPTIONS sao preservados.
if [[ "${NODE_OPTIONS:-}" != *max-old-space-size* && "${NODE_OPTIONS:-}" != *max_old_space_size* ]]; then
  export NODE_OPTIONS="${NODE_OPTIONS:+$NODE_OPTIONS }--max-old-space-size=$heap"
fi

# Script que ja esta sob a trava e chama outro build por aqui nao espera por si
# mesmo.
if [[ "${BUILD_PESADO_TRAVA:-}" == "$lock" ]]; then
  exec "$@"
fi

# A trava e aberta so para leitura: /var/lock tem sticky bit e o kernel
# (fs.protected_regular) recusa abrir para escrita o arquivo de outro usuario,
# inclusive para root.
if [[ ! -e "$lock" ]]; then
  ( umask 022; : >> "$lock" ) 2>/dev/null || true
fi
[[ -r "$lock" ]] || { aviso "nao consegui criar nem ler a trava $lock."; exit 73; }
exec 9< "$lock"

dono="$lock.dono"
quem_segura() { cat "$dono" 2>/dev/null || printf 'desconhecido'; }

if ! flock -n 9; then
  aviso "outro build pesado em andamento ($(quem_segura)). Aguardando a trava $lock por ate ${espera}s."
  inicio=$SECONDS
  until flock -w "$(( espera - (SECONDS - inicio) < 60 ? espera - (SECONDS - inicio) : 60 ))" 9; do
    decorrido=$(( SECONDS - inicio ))
    if (( decorrido >= espera )); then
      aviso "desisti: a trava $lock nao soltou em ${espera}s ($(quem_segura)). Nada foi executado."
      exit 75
    fi
    aviso "ainda aguardando a trava (${decorrido}s de ${espera}s; $(quem_segura))."
  done
  aviso "trava liberada depois de $(( SECONDS - inicio ))s. Seguindo."
fi

rm -f "$dono" 2>/dev/null || true
printf 'pid %s, desde %s: %s\n' "$$" "$(date '+%d/%m %H:%M:%S')" "$*" > "$dono" 2>/dev/null || true

# O comando roda sem o descritor da trava: processo que ele deixar para tras
# nao segura a trava depois que o build termina.
export BUILD_PESADO_TRAVA="$lock"
filho=
trap '[[ -z "$filho" ]] || kill -TERM "$filho" 2>/dev/null || true' INT TERM
"$@" <&0 9<&- &
filho=$!
status=0
wait "$filho" || status=$?
# Um sinal interrompe o wait antes do comando terminar: espera de novo, para
# so soltar a trava com o build realmente encerrado.
while kill -0 "$filho" 2>/dev/null; do
  status=0
  wait "$filho" || status=$?
done
rm -f "$dono" 2>/dev/null || true
exit "$status"
