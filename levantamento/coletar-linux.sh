#!/usr/bin/env bash
# coletar-linux.sh - levantamento somente-leitura de um host Linux, saida em JSON.
# Uso: bash coletar-linux.sh [--rapido]
#
# Nunca imprime o VALOR de um segredo: apenas o arquivo, a linha e o tipo.
# Nao escreve nada fora de /tmp e nao altera nenhum servico.
set -u

MODO="${1:-}"
RAPIDO=0
[ "$MODO" = "--rapido" ] && RAPIDO=1

TMP=$(mktemp -d /tmp/levantamento.XXXXXX)
trap 'rm -rf "$TMP"' EXIT

# ---------------------------------------------------------------- helpers JSON
# jarr: cada linha da entrada padrao vira um elemento de um array JSON
jarr() {
  awk 'BEGIN { ORS = ""; print "[" }
       { gsub(/\\/, "\\\\"); gsub(/"/, "\\\""); gsub(/\t/, "\\t"); gsub(/\r/, "")
         if (n++) print ","
         printf "\"%s\"", $0 }
       END { print "]" }'
}

# jval: escapa um escalar para dentro de aspas JSON
jval() {
  printf '%s' "${1:-}" |
    awk 'BEGIN { ORS = "" }
         { gsub(/\\/, "\\\\"); gsub(/"/, "\\\""); gsub(/\t/, "\\t"); gsub(/\r/, "")
           if (n++) printf "\\n"
           print }'
}

campo()  { printf '  "%s": "%s",\n' "$1" "$(jval "$2")"; }
num()    { printf '  "%s": %s,\n' "$1" "${2:-0}"; }
lista()  { printf '  "%s": ' "$1"; jarr; printf ',\n'; }
sec()    { printf ' "%s": {\n' "$1"; }
fimsec() { printf '  "_": null\n },\n'; }

RAIZES="/opt /root /srv /home /var/www"
# dashboards do Grafana sao JSON de terceiros cheios de nomes de metrica que
# disparam falso positivo em varredura de segredo; ficam de fora.
EXCLUI='node_modules|/\.next/|/\.git/|/vendor/|/\.cache/|provisioning/dashboards'

echo "{"
campo "coletado_em"    "$(date -Is)"
campo "coletor_versao" "1.0.0"
campo "plataforma"     "linux"
campo "modo"           "$([ $RAPIDO -eq 1 ] && echo rapido || echo completo)"

# ------------------------------------------------------------- 1. identidade
sec identidade
campo "hostname"     "$(hostname)"
campo "so"           "$(. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-desconhecido}")"
campo "kernel"       "$(uname -r)"
campo "arquitetura"  "$(uname -m)"
campo "ligado_ha"    "$(uptime -p 2>/dev/null | sed 's/^up //')"
campo "carga"        "$(cut -d' ' -f1-3 /proc/loadavg)"
num   "cpus"         "$(nproc 2>/dev/null || echo 0)"
campo "cpu_modelo"   "$(awk -F: '/model name/ { print $2; exit }' /proc/cpuinfo | sed 's/^ *//')"
num   "ram_mb"       "$(awk '/MemTotal/ { print int($2/1024) }' /proc/meminfo)"
num   "ram_livre_mb" "$(awk '/MemAvailable/ { print int($2/1024) }' /proc/meminfo)"
num   "swap_mb"      "$(awk '/SwapTotal/ { print int($2/1024) }' /proc/meminfo)"
num   "swap_usado_mb" "$(awk '/SwapTotal/ { t=$2 } /SwapFree/ { f=$2 } END { print int((t-f)/1024) }' /proc/meminfo)"
campo "pressao_memoria" "$(awk '/^some/ { print; exit }' /proc/pressure/memory 2>/dev/null)"
campo "pressao_io"      "$(awk '/^some/ { print; exit }' /proc/pressure/io 2>/dev/null)"
fimsec

# ---------------------------------------------------------- 2. armazenamento
sec armazenamento
lista "sistemas_de_arquivos" < <(
  df -h -x tmpfs -x devtmpfs -x squashfs -x overlay 2>/dev/null |
    awk 'NR > 1 { printf "%s|%s|%s|%s|%s|%s\n", $1, $2, $3, $4, $5, $6 }'
)
lista "blocos" < <(lsblk -no NAME,SIZE,TYPE,MOUNTPOINT 2>/dev/null | sed 's/  */|/g; s/|$//')
lista "montagens" < <(
  findmnt -rno TARGET,SOURCE,FSTYPE 2>/dev/null |
    grep -vE '^/(proc|sys|dev|run|snap)' | tr ' ' '|'
)
fimsec

# ------------------------------------------------------------------ 3. peso
sec peso
if [ $RAPIDO -eq 0 ]; then
  lista "diretorios_opt" < <(
    du -sm /opt/* 2>/dev/null | sort -rn | head -30 | awk '{ printf "%s|%s\n", $2, $1 }'
  )
  lista "diretorios_sistema" < <(
    du -sm /var/lib/docker /var/lib/postgresql /var/lib/mysql /var/log /root /home /srv 2>/dev/null |
      awk '{ printf "%s|%s\n", $2, $1 }'
  )
else
  lista "diretorios_opt"      < /dev/null
  lista "diretorios_sistema"  < /dev/null
fi
fimsec

# ---------------------------------------------------------------- 4. docker
sec docker
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  campo "disponivel" "sim"
  campo "versao"     "$(docker version --format '{{.Server.Version}}' 2>/dev/null)"
  lista "resumo_uso"   < <(docker system df --format '{{.Type}}|{{.TotalCount}}|{{.Size}}|{{.Reclaimable}}' 2>/dev/null)
  lista "imagens"      < <(docker images --format '{{.Repository}}:{{.Tag}}|{{.Size}}|{{.CreatedSince}}' 2>/dev/null | sort -t'|' -k2 -rh)
  lista "containers"   < <(docker ps -a --format '{{.Names}}|{{.Image}}|{{.Status}}' 2>/dev/null)
  lista "volumes"      < <(docker volume ls --format '{{.Name}}|{{.Driver}}' 2>/dev/null)
  lista "tag_latest"   < <(docker ps --format '{{.Image}}' 2>/dev/null | grep ':latest$' | sort -u)
  lista "sem_healthcheck" < <(
    for C in $(docker ps --format '{{.Names}}' 2>/dev/null); do
      docker inspect -f "{{if .Config.Healthcheck}}{{else}}${C}{{end}}" "$C" 2>/dev/null
    done | grep -v '^$'
  )
  lista "reiniciando" < <(docker ps -a --filter 'status=restarting' --format '{{.Names}}|{{.Status}}' 2>/dev/null)
  lista "parados"     < <(docker ps -a --filter 'status=exited'    --format '{{.Names}}|{{.Status}}' 2>/dev/null)
else
  campo "disponivel" "nao"
  lista "resumo_uso"      < /dev/null
  lista "imagens"         < /dev/null
  lista "containers"      < /dev/null
  lista "volumes"         < /dev/null
  lista "tag_latest"      < /dev/null
  lista "sem_healthcheck" < /dev/null
  lista "reiniciando"     < /dev/null
  lista "parados"         < /dev/null
fi
fimsec

# ------------------------------------------------------------------- 5. env
sec env
find $RAIZES -maxdepth 6 -name '.env*' -type f 2>/dev/null | grep -Ev "$EXCLUI" > "$TMP/env" 2>/dev/null
num   "total" "$(wc -l < "$TMP/env")"
lista "por_nome" < <(
  sed 's|.*/||' "$TMP/env" | sort | uniq -c | sort -rn | awk '{ printf "%s|%s\n", $2, $1 }'
)
lista "por_projeto" < <(
  grep '^/opt/' "$TMP/env" | awk -F/ '{ print $3 }' | sort | uniq -c | sort -rn |
    awk '{ printf "%s|%s\n", $2, $1 }'
)
# permissao insegura: qualquer bit concedido a grupo ou a outros
lista "permissao_insegura" < <(
  xargs -r -d '\n' stat -c '%a|%U:%G|%n' < "$TMP/env" 2>/dev/null |
    awk -F'|' '$1 !~ /^[0-7]00$/'
)
lista "sobras_de_backup" < <(
  grep -E '\.(bak|old|save|orig|anterior|quebrado)|\.bak-|\.backup-|\.before-|~$|\.[0-9]{9,}$' "$TMP/env"
)
fimsec

# -------------------------------------------------------------- 6. segredos
sec segredos
# O padrao do Resend e re_<id de 8+>_<segredo de 10+>. Exigir os dois trechos
# separa a chave de verdade de nomes de metrica como re_cpu_waiting_seconds.
RESEND='re_[A-Za-z0-9]{8,}_[A-Za-z0-9]{10,}'
PADROES='sk_live_[A-Za-z0-9]{16,}|rk_live_[A-Za-z0-9]{16,}|sk_test_[A-Za-z0-9]{16,}'
PADROES="$PADROES|$RESEND"'|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}'
PADROES="$PADROES"'|github_pat_[A-Za-z0-9_]{30,}|xox[baprs]-[A-Za-z0-9-]{10,}'
PADROES="$PADROES"'|AIza[0-9A-Za-z_-]{35}|SG\.[A-Za-z0-9_-]{20,}|BEGIN [A-Z ]*PRIVATE KEY'

lista "ocorrencias" < <(
  grep -rInE "$PADROES" $RAIZES \
    --include='.env*' --include='*.sh' --include='*.yml' --include='*.yaml' \
    --include='*.json' --include='*.conf' 2>/dev/null |
  grep -Ev "$EXCLUI" |
  awk -F: -v resend="$RESEND" '{
    tipo = "desconhecido"
    if      ($0 ~ /sk_live_/)         tipo = "stripe-secreta-PRODUCAO"
    else if ($0 ~ /rk_live_/)         tipo = "stripe-restrita-PRODUCAO"
    else if ($0 ~ /sk_test_/)         tipo = "stripe-teste"
    else if ($0 ~ resend)             tipo = "resend"
    else if ($0 ~ /AKIA/)             tipo = "aws"
    else if ($0 ~ /ghp_|github_pat_/) tipo = "github"
    else if ($0 ~ /xox/)              tipo = "slack"
    else if ($0 ~ /AIza/)             tipo = "google-api"
    else if ($0 ~ /SG\./)             tipo = "sendgrid"
    else if ($0 ~ /PRIVATE KEY/)      tipo = "chave-privada"
    printf "%s|linha %s|%s\n", $1, $2, tipo
  }' | sort -u
)

# host e banco ficam visiveis; a senha e substituida antes de sair daqui
lista "conexoes_com_senha" < <(
  grep -rhoE '(postgres(ql)?|mysql|mongodb(\+srv)?|redis|amqp)://[^:@[:space:]"]+:[^@[:space:]"]+@[^"[:space:]/]+/?[^"[:space:];]*' \
    $RAIZES --include='.env*' 2>/dev/null |
    sed -E 's|(//[^:]+:)[^@]+@|\1SENHA-OMITIDA@|' | sort -u
)
fimsec

# -------------------------------------------------------------- 7. dominios
sec dominios
CADDYFILE=/etc/caddy/Caddyfile
if [ -f "$CADDYFILE" ]; then
  grep -oE '^[^#[:space:]][^{]*\{' "$CADDYFILE" 2>/dev/null |
    sed 's/{//' | tr ',' '\n' | tr -d ' \t' |
    grep -E '^[a-z0-9*.-]+\.[a-z]{2,}$' | sort -u > "$TMP/dom"
  num   "total_hostnames" "$(wc -l < "$TMP/dom")"
  lista "hostnames" < "$TMP/dom"
  lista "dominios_raiz" < <(
    awk -F. '{ if (NF >= 3 && $NF == "br") print $(NF-2)"."$(NF-1)"."$NF; else print $(NF-1)"."$NF }' "$TMP/dom" | sort -u
  )
  # hostname declarado no Caddy que nao resolve = bloco morto, cert nunca renova
  lista "sem_dns" < <(
    while read -r D; do
      [ -z "$D" ] && continue
      case "$D" in \**) continue ;; esac
      getent hosts "$D" >/dev/null 2>&1 || echo "$D"
    done < "$TMP/dom"
  )
else
  num   "total_hostnames" 0
  lista "hostnames"     < /dev/null
  lista "dominios_raiz" < /dev/null
  lista "sem_dns"       < /dev/null
fi

# validade lida do disco: rapido e sem depender de rede
lista "certificados" < <(
  find /var/lib/caddy /etc/letsencrypt/live -name '*.crt' -o -name 'fullchain.pem' 2>/dev/null |
  while read -r C; do
    FIM=$(openssl x509 -enddate -noout -in "$C" 2>/dev/null | cut -d= -f2)
    [ -z "$FIM" ] && continue
    DIAS=$(( ( $(date -d "$FIM" +%s 2>/dev/null || echo 0) - $(date +%s) ) / 86400 ))
    printf '%s|%s dias\n' "$(basename "$C" .crt)" "$DIAS"
  done | sort -t'|' -k2 -n
)
fimsec

# ---------------------------------------------------------------- 8. bancos
sec bancos
if command -v psql >/dev/null 2>&1 && id postgres >/dev/null 2>&1; then
  campo "postgres_host_versao" "$(sudo -u postgres psql -tAc 'show server_version;' 2>/dev/null)"
  lista "postgres_host_bancos" < <(
    sudo -u postgres psql -tAc "select datname || '|' || pg_size_pretty(pg_database_size(datname)) || '|' || pg_get_userbyid(datdba) from pg_database where datistemplate = false order by pg_database_size(datname) desc;" 2>/dev/null
  )
  lista "postgres_host_roles" < <(
    sudo -u postgres psql -tAc "select rolname || '|' || case when rolsuper then 'SUPERUSUARIO' else 'comum' end || '|' || case when rolcreatedb then 'createdb' else '-' end from pg_roles where rolname not like 'pg\\_%' order by rolsuper desc, rolname;" 2>/dev/null
  )
  campo "postgres_host_escuta" "$(ss -lntH 2>/dev/null | awk '$4 ~ /:5432$/ { printf "%s ", $4 }')"
else
  campo "postgres_host_versao" ""
  lista "postgres_host_bancos" < /dev/null
  lista "postgres_host_roles"  < /dev/null
  campo "postgres_host_escuta" ""
fi

lista "postgres_containers" < <(
  for C in $(docker ps --format '{{.Names}}' 2>/dev/null); do
    IMG=$(docker inspect -f '{{.Config.Image}}' "$C" 2>/dev/null)
    case "$IMG" in *postgres*) ;; *) continue ;; esac
    U=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$C" 2>/dev/null |
          awk -F= '/^POSTGRES_USER=/ { print $2 }')
    docker exec "$C" psql -U "${U:-postgres}" -tAc \
      "select datname || '|' || pg_size_pretty(pg_database_size(datname)) from pg_database where datistemplate = false;" 2>/dev/null |
      sed "s|^|${C}\||"
  done
)
lista "outros_bancos" < <(
  docker ps --format '{{.Names}}|{{.Image}}' 2>/dev/null | grep -Ei 'mysql|maria|mongo|redis|clickhouse'
)
fimsec

# ------------------------------------------------------------------- 9. git
sec git
lista "repositorios" < <(
  find $RAIZES -maxdepth 5 -type d -name .git 2>/dev/null | sed 's|/\.git$||' |
  while read -r R; do
    B=$(git -C "$R" rev-parse --abbrev-ref HEAD 2>/dev/null)
    S=$(git -C "$R" status --porcelain 2>/dev/null | wc -l)
    A=$(git -C "$R" rev-list --count '@{u}..HEAD' 2>/dev/null || echo "sem-upstream")
    O=$(git -C "$R" remote get-url origin 2>/dev/null | sed 's|.*[:/]\([^/]*/[^/]*\)$|\1|; s|\.git$||')
    printf '%s|%s|sujo=%s|ahead=%s|%s\n' "$R" "${B:-?}" "$S" "$A" "${O:-SEM-REMOTO}"
  done
)
fimsec

# ------------------------------------------------------------------ 10. rede
sec rede
campo "ips" "$(hostname -I 2>/dev/null)"
lista "portas_escutando" < <(
  ss -lntpH 2>/dev/null |
    awk '{ p = $6; sub(/.*users:\(\("/, "", p); sub(/".*/, "", p); printf "%s|%s\n", $4, p }' | sort -u
)
lista "portas_publicas" < <(
  ss -lntpH 2>/dev/null |
    awk '$4 ~ /^(0\.0\.0\.0|\[::\]|\*):/ { p = $6; sub(/.*users:\(\("/, "", p); sub(/".*/, "", p); printf "%s|%s\n", $4, p }' | sort -u
)
lista "interfaces" < <(ip -br addr 2>/dev/null | tr -s ' ' '|')
fimsec

# ------------------------------------------------------------- 11. seguranca
sec seguranca
campo "fail2ban_jails" "$(fail2ban-client status 2>/dev/null | awk -F: '/Jail list/ { gsub(/^[ \t]+/, "", $2); print $2 }')"
num   "fail2ban_qtd"   "$(fail2ban-client status 2>/dev/null | awk -F: '/Number of jail/ { gsub(/[^0-9]/, "", $2); print $2 + 0 }' || echo 0)"
campo "firewall"       "$(ufw status 2>/dev/null | head -1)"
lista "firewall_regras" < <(ufw status numbered 2>/dev/null | grep -E '^\[' | tr -s ' ')
campo "ssh_senha_permitida" "$(sshd -T 2>/dev/null | awk '/^passwordauthentication/ { print $2 }')"
campo "ssh_root_permitido"  "$(sshd -T 2>/dev/null | awk '/^permitrootlogin/ { print $2 }')"
num   "chaves_root"         "$(grep -cvE '^\s*(#|$)' /root/.ssh/authorized_keys 2>/dev/null || echo 0)"
lista "chaves_autorizadas" < <(
  awk '!/^\s*(#|$)/ { print $NF " (" substr($1, 1, 20) ")" }' /root/.ssh/authorized_keys 2>/dev/null
)
num   "atualizacoes_pendentes" "$(apt-get -s upgrade 2>/dev/null | grep -c '^Inst' || echo 0)"
campo "reinicio_necessario"    "$([ -f /var/run/reboot-required ] && echo sim || echo nao)"
fimsec

# --------------------------------------------------------------- 12. backups
sec backups
lista "cron_root"  < <(crontab -l 2>/dev/null | grep -vE '^\s*(#|$)')
lista "cron_d"     < <(grep -rhvE '^\s*(#|$)' /etc/cron.d/ 2>/dev/null)
lista "timers"     < <(systemctl list-timers --no-pager --no-legend 2>/dev/null | tr -s ' ' | cut -d' ' -f1-4)
if [ -d /opt/backups ]; then
  campo "tamanho" "$(du -sh /opt/backups 2>/dev/null | cut -f1)"
  lista "mais_recentes" < <(
    find /opt/backups -maxdepth 3 -type f \( -name '*.gz' -o -name '*.dump' -o -name '*.tar*' \) \
      -printf '%TY-%Tm-%Td %TH:%TM|%s|%p\n' 2>/dev/null | sort -r | head -12
  )
  lista "ultimo_log" < <(
    find /opt/backups /var/log -maxdepth 2 -name '*backup*.log' -mtime -7 2>/dev/null |
      head -3 | xargs -r tail -n 5 2>/dev/null
  )
else
  campo "tamanho" ""
  lista "mais_recentes" < /dev/null
  lista "ultimo_log"    < /dev/null
fi
fimsec

# ------------------------------------------------------------- 13. servicos
sec servicos
lista "systemd_ativos" < <(
  systemctl list-units --type=service --state=running --no-pager --no-legend 2>/dev/null |
    awk '{ print $1 }' | grep -vE '^(systemd-|dbus|cron|rsyslog|polkit|udisks|ssh|unattended|getty)'
)
lista "systemd_falhos" < <(systemctl list-units --state=failed --no-pager --no-legend 2>/dev/null | tr -s ' ')
lista "top_ram" < <(
  ps -eo rss,comm --sort=-rss 2>/dev/null | awk 'NR > 1 && NR <= 13 { printf "%s|%d MB\n", $2, $1/1024 }'
)
lista "top_swap" < <(
  for P in /proc/[0-9]*; do
    S=$(awk '/^VmSwap/ { print $2 }' "$P/status" 2>/dev/null)
    [ -n "$S" ] && [ "$S" -gt 10240 ] && printf '%s|%d MB\n' "$(cat "$P/comm" 2>/dev/null)" $((S / 1024))
  done | sort -t'|' -k2 -rn | head -10
)
fimsec

printf ' "fim": true\n}\n'
