#!/usr/bin/env bash
# Rotação dos dumps em /opt/backups/db.
#
# Regra decidida em 26/08/2026 (poucos clientes ativos, o volume estava com
# 9,9 GB por causa de três cópias do filestore do Odoo, 3,2 GB cada):
#
#   - o arquivo mais recente COM CONTEÚDO de cada família NUNCA é apagado;
#   - arquivo grande (> 500 MB): fica só o mais recente da família;
#   - os demais: 7 dias.
#
# Família = nome sem a data (ex.: "fs-odoo-avilaops_avilaops-data").
#
# "Com conteúdo" = maior que um gzip vazio (20 bytes). Até 07/10/2026 valia o
# mais recente pela data, sem olhar o tamanho: quando um contêiner sumia, o
# dump de 20 bytes do dia virava "o mais recente" e os dumps bons da família
# saíam aos 7 dias. Desde a tarefa 172 (07/10/2026) um ".gz" também precisa
# passar no "gzip -t": só o tamanho deixava um gzip truncado (disco cheio) de 21
# bytes tomar o lugar do dump bom. Família em que nenhum arquivo tem conteúdo
# segue a regra antiga (fica o mais recente), para a rotação nunca zerar uma
# família.
set -euo pipefail
DIR=${ROTACAO_DIR:-/opt/backups/db}
DIAS=${DIAS:-7}
GRANDE=${GRANDE:-524288000}
VAZIO=20   # bytes de um gzip sem conteúdo
cd "$DIR"

# "=()" e os "--" abaixo: diretorio vazio nao pode dar "unbound variable" e nome
# comecando por hifen nao pode virar opcao de comando.
declare -A recente=() cheio=() corrompido=()

escolhe() {
  recente=(); cheio=()
  local f fam tem atual
  for f in *; do
    [ -f "$f" ] || continue
    fam=$(printf '%s\n' "$f" | sed -E "s/[-_]?[0-9]{8}([-_][0-9]{6})?//g")
    # Nome que e so a data (ex.: "20260901") da familia vazia, e o bash recusa
    # chave vazia ("bad array subscript"): o prefixo deixa a chave sempre com texto.
    fam="_$fam"
    tem=false
    [ "$(stat -c%s -- "$f")" -gt "$VAZIO" ] && [ -z "${corrompido[$f]:-}" ] && tem=true
    atual="${recente[$fam]:-}"
    # Troca o preservado se ainda nao ha nenhum, se este tem conteudo e o atual
    # nao, ou se os dois estao na mesma condicao e este e mais novo.
    if [ -z "$atual" ] \
       || { $tem && ! ${cheio[$fam]}; } \
       || { [ "$tem" = "${cheio[$fam]}" ] && [ "$f" -nt "$atual" ]; }; then
      recente[$fam]="$f"; cheio[$fam]=$tem
    fi
  done
}

# So o escolhido de cada familia passa pelo "gzip -t" (nao o diretorio inteiro).
# Escolhido que nao passa conta como sem conteudo e a escolha e refeita.
while :; do
  escolhe
  refazer=false
  for fam in "${!recente[@]}"; do
    f="${recente[$fam]}"
    case "$f" in *.gz) ;; *) continue ;; esac
    ${cheio[$fam]} || continue
    if ! gzip -t -- "$f" 2>/dev/null; then
      corrompido[$f]=1; refazer=true
      echo "rotacao: $f nao passa no gzip -t, nao conta como conteudo" >&2
    fi
  done
  $refazer || break
done

apagados=0; bytes=0
for f in *; do
  [ -f "$f" ] || continue
  eh_recente=false
  for m in "${recente[@]}"; do [ "$f" = "$m" ] && eh_recente=true && break; done
  $eh_recente && continue
  tam=$(stat -c%s -- "$f")
  velho=$(find "./$f" -maxdepth 0 -mtime +"$DIAS")
  if [ "$tam" -gt "$GRANDE" ] || [ -n "$velho" ]; then
    bytes=$((bytes + tam)); apagados=$((apagados + 1))
    [ "${DRY:-0}" = "1" ] || rm -f -- "$f"
  fi
done
echo "rotacao: $apagados arquivo(s), $((bytes/1024/1024)) MB $([ "${DRY:-0}" = "1" ] && echo "(simulacao)" || echo "apagados"); familias preservadas: ${#recente[@]}"
