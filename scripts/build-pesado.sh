#!/usr/bin/env bash
# Um build pesado por vez no servidor dos agentes (creators, 4 GB de RAM), com
# teto de heap no Node. Instalado como ~/.local/bin/build-pesado por
# scripts/instalar-build-pesado.sh (copia de um commit ja enviado a main).
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
#   BUILD_PESADO_CARENCIA_S  prazo, em s, para o comando encerrar depois de um
#                            sinal, antes do KILL (padrao 30)
#
# Sinais: HUP, INT, QUIT e TERM recebidos aqui viram TERM para o grupo de
# processos do comando (o comando e tudo o que ele disparou), e a trava so e
# solta com esse grupo vazio. Quem nao encerrar no prazo de carencia leva KILL.
# Limite: KILL (ou outro sinal que nao da para tratar) neste script solta a
# trava na hora e deixa o comando rodando; para interromper um build, use TERM
# no build-pesado, ou mate o grupo do comando (o pid do comando e o do grupo).
#
# Saida: a do comando; 128+sinal se foi interrompido por sinal; 75 se a trava
# nao soltou no prazo; 64 em erro de uso.
set -euo pipefail

aviso() { printf 'build-pesado: %s\n' "$*" >&2; }

(( $# > 0 )) || { aviso 'uso: build-pesado <comando> [argumentos...]'; exit 64; }

lock=${BUILD_PESADO_LOCK:-/var/lock/build-pesado.lock}
espera=${BUILD_PESADO_ESPERA_S:-1800}
heap=${BUILD_PESADO_HEAP_MB:-1024}
carencia=${BUILD_PESADO_CARENCIA_S:-30}
# Sem zero a esquerda: o bash leria "08" como octal invalido na conta da espera.
inteiro='^(0|[1-9][0-9]{0,8})$'
[[ "$espera" =~ $inteiro ]] || { aviso 'BUILD_PESADO_ESPERA_S precisa ser um numero inteiro de segundos, sem zero a esquerda.'; exit 64; }
[[ "$carencia" =~ $inteiro ]] || { aviso 'BUILD_PESADO_CARENCIA_S precisa ser um numero inteiro de segundos, sem zero a esquerda.'; exit 64; }
[[ "$heap" =~ ^[1-9][0-9]*$ ]] || { aviso 'BUILD_PESADO_HEAP_MB precisa ser um numero inteiro de MB.'; exit 64; }

# Quem chama ja escolheu um teto: fica o dele. Os demais valores de
# NODE_OPTIONS sao preservados.
if [[ "${NODE_OPTIONS:-}" != *max-old-space-size* && "${NODE_OPTIONS:-}" != *max_old_space_size* ]]; then
  export NODE_OPTIONS="${NODE_OPTIONS:+$NODE_OPTIONS }--max-old-space-size=$heap"
fi

# Script que ja esta sob a trava e chama outro build por aqui nao espera por si
# mesmo. So vale se o build-pesado que pegou a trava ainda esta vivo e e
# ancestral deste processo: a variavel sozinha pode ter sido herdada por um
# shell ou daemon que sobrou de um build ja encerrado (e que, orfao, passa a
# ser filho do pid 1 ou de um subreaper, nunca de um build-pesado).
sob_a_trava_de() {
  local alvo=$1 p=$$
  [[ "$alvo" =~ ^[1-9][0-9]*$ ]] && (( alvo > 1 )) || return 1
  [[ "$(tr '\0' ' ' < "/proc/$alvo/cmdline" 2>/dev/null)" == *build-pesado* ]] || return 1
  while [[ "$p" =~ ^[0-9]+$ ]] && (( p > 1 )); do
    p=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ') || return 1
    [[ "$p" == "$alvo" ]] && return 0
  done
  return 1
}
if [[ "${BUILD_PESADO_TRAVA:-}" == "$lock" ]] && sob_a_trava_de "${BUILD_PESADO_TRAVA_PID:-}"; then
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

travado=0
if flock -n 9; then
  travado=1
else
  aviso "outro build pesado em andamento ($(quem_segura)). Aguardando a trava $lock por ate ${espera}s."
  inicio=$SECONDS
  while :; do
    decorrido=$(( SECONDS - inicio ))
    if (( decorrido >= espera )); then
      aviso "desisti: a trava $lock nao soltou em ${espera}s ($(quem_segura)). Nada foi executado."
      exit 75
    fi
    passo=$(( espera - decorrido < 60 ? espera - decorrido : 60 ))
    if flock -w "$passo" 9; then
      travado=1
      break
    fi
    aviso "ainda aguardando a trava ($(( SECONDS - inicio ))s de ${espera}s; $(quem_segura))."
  done
  aviso "trava liberada depois de $(( SECONDS - inicio ))s. Seguindo."
fi
# Nenhum caminho chega ao comando sem a trava na mao.
(( travado == 1 )) || { aviso "nao peguei a trava $lock. Nada foi executado."; exit 70; }

# So o nome do comando e o diretorio: os argumentos podem carregar segredo
# (--build-arg TOKEN=...) e este arquivo e legivel por todos os usuarios.
rm -f "$dono" 2>/dev/null || true
printf 'pid %s, desde %s: %s, em %s\n' "$$" "$(date '+%d/%m %H:%M:%S')" "$1" "$PWD" > "$dono" 2>/dev/null || true

export BUILD_PESADO_TRAVA="$lock" BUILD_PESADO_TRAVA_PID="$$"
filho=
sinal=0
repassa() {
  sinal=$1
  [[ -n "$filho" ]] || return 0
  kill -TERM -- "-$filho" 2>/dev/null || kill -TERM "$filho" 2>/dev/null || true
}
trap 'repassa 1' HUP
trap 'repassa 2' INT
trap 'repassa 3' QUIT
trap 'repassa 15' TERM
# O comando roda em sessao e grupo de processos proprios (setsid), para o
# sinal alcancar tambem o que ele disparou, e sem o descritor da trava:
# processo que ele deixar para tras ao terminar por conta propria nao segura a
# trava.
setsid "$@" <&0 9<&- &
filho=$!
(( sinal == 0 )) || repassa "$sinal"
status=0
wait "$filho" || status=$?
if (( sinal != 0 )); then
  # Um sinal interrompe o wait antes do comando terminar: a trava so e solta
  # com o grupo inteiro encerrado.
  limite=$(( SECONDS + carencia ))
  forcado=0
  while kill -0 -- "-$filho" 2>/dev/null || kill -0 "$filho" 2>/dev/null; do
    if (( SECONDS >= limite )); then
      if (( forcado == 1 )); then
        aviso "o grupo $filho nao encerrou nem com KILL. Soltando a trava assim mesmo."
        break
      fi
      aviso "o comando nao encerrou em ${carencia}s depois do sinal. Enviando KILL ao grupo $filho."
      kill -KILL -- "-$filho" 2>/dev/null || true
      forcado=1
      limite=$(( SECONDS + carencia + 5 ))
    fi
    sleep 0.2
  done
  status=$(( 128 + sinal ))
fi
rm -f "$dono" 2>/dev/null || true
exit "$status"
