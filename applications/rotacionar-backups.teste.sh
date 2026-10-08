#!/usr/bin/env bash
# Teste do rotacionar-backups.sh em diretorio temporario, com arquivos falsos.
# Uso: teste-rot.sh <script>
set -u
S=$(readlink -f "$1"); T=$(mktemp -d /tmp/t172-rot.XXXXXX); trap 'rm -rf "$T"' EXIT
case "$T" in /tmp/t172-rot.*) ;; *) echo "diretorio inesperado"; exit 2;; esac
falhas=0
confere() { # descricao, esperado(existe|some), arquivo
  if { [ "$2" = existe ] && [ -e "$T/d/$3" ]; } || { [ "$2" = some ] && [ ! -e "$T/d/$3" ]; }; then
    echo "  ok   $1"; else echo "  ERRO $1 (esperado: $3 $2)"; falhas=$((falhas+1)); fi
}
bom() { head -c 3000 /dev/urandom | base64 | gzip > "$1"; }
novo_dir() { rm -rf "$T/d"; mkdir "$T/d"; cd "$T/d"; }

echo "== 1. diretorio vazio"
novo_dir
saida=$(ROTACAO_DIR="$T/d" bash "$S" 2>&1); rc=$?
echo "  saida: $saida (codigo $rc)"
[ $rc -eq 0 ] && ! grep -q unbound <<<"$saida" && echo "  ok   nao quebra" || { echo "  ERRO diretorio vazio"; falhas=$((falhas+1)); }

echo "== 2. familias"
novo_dir
# a: dump bom antigo + .gz corrompido de mais de 20 bytes, mais novo (caso do parecer)
bom a-20260901.sql.gz;                       touch -d '30 days ago' a-20260901.sql.gz
head -c 40 /dev/urandom > a-20260905.sql.gz; touch -d '20 days ago' a-20260905.sql.gz
# b: dump bom antigo + gzip de verdade truncado no meio, mais novo
bom b-20260901.sql.gz;                       touch -d '30 days ago' b-20260901.sql.gz
bom "$T/inteiro.gz"; head -c 1500 "$T/inteiro.gz" > b-20260910.sql.gz; touch -d '15 days ago' b-20260910.sql.gz
# c: so gzip vazio (20 bytes): fica o mais recente
for d in 01 02; do printf '' | gzip -n > c-202609$d.sql.gz; touch -d "$((40 - 10#$d)) days ago" c-202609$d.sql.gz; done
# d: so corrompidos de mais de 20 bytes: fica o mais recente
for d in 01 02; do head -c 50 /dev/urandom > d-202609$d.sql.gz; touch -d "$((40 - 10#$d)) days ago" d-202609$d.sql.gz; done
# e: nome com espaco e nome comecando por hifen
bom "com espaco-20260901.sql.gz"; touch -d '30 days ago' "com espaco-20260901.sql.gz"
bom "com espaco-20260902.sql.gz"; touch -d '29 days ago' "com espaco-20260902.sql.gz"
bom ./-hifen-20260901.sql.gz; touch -d '30 days ago' ./-hifen-20260901.sql.gz
bom ./-hifen-20260902.sql.gz; touch -d '29 days ago' ./-hifen-20260902.sql.gz
# f: .dump (nao e gzip) de 21 bytes conta pelo tamanho, como antes
head -c 21 /dev/urandom > f-20260901.dump; touch -d '30 days ago' f-20260901.dump
head -c 10 /dev/urandom > f-20260902.dump; touch -d '29 days ago' f-20260902.dump
# g: bom recente e bom de ontem: nada com menos de 7 dias some
bom g-20261006.sql.gz; touch -d '1 day ago' g-20261006.sql.gz; bom g-20261007.sql.gz
[ "$(stat -c%s a-20260905.sql.gz)" -gt 20 ] && [ "$(stat -c%s b-20260910.sql.gz)" -gt 20 ] || { echo "preparo errado"; exit 2; }
gzip -t a-20260905.sql.gz 2>/dev/null && { echo "preparo errado: a passou no gzip -t"; exit 2; }
gzip -t b-20260910.sql.gz 2>/dev/null && { echo "preparo errado: b passou no gzip -t"; exit 2; }
echo "  preparo: a-20260905 $(stat -c%s a-20260905.sql.gz) bytes e b-20260910 $(stat -c%s b-20260910.sql.gz) bytes, os dois reprovados no gzip -t"
saida=$(ROTACAO_DIR="$T/d" DRY=1 bash "$S" 2>&1); rc=$?
echo "  simulacao: $(tail -1 <<<"$saida") (codigo $rc); arquivos depois: $(ls -A | wc -l) de 16"
[ "$(ls -A | wc -l)" -eq 16 ] || { echo "  ERRO DRY=1 apagou"; falhas=$((falhas+1)); }
saida=$(ROTACAO_DIR="$T/d" bash "$S" 2>&1); rc=$?
echo "$saida" | sed 's/^/  saida: /'; echo "  codigo $rc"
[ $rc -eq 0 ] || { echo "  ERRO codigo $rc"; falhas=$((falhas+1)); }
confere "a: dump bom fica, corrompido de 40 bytes nao conta como conteudo" existe a-20260901.sql.gz
confere "a: corrompido antigo sai"                 some   a-20260905.sql.gz
confere "b: dump bom fica, gzip truncado nao conta" existe b-20260901.sql.gz
confere "b: truncado antigo sai"                   some   b-20260910.sql.gz
confere "c: so vazios, fica o mais recente"        existe c-20260902.sql.gz
confere "c: vazio mais antigo sai"                 some   c-20260901.sql.gz
confere "d: so corrompidos, fica o mais recente"   existe d-20260902.sql.gz
confere "d: corrompido mais antigo sai"            some   d-20260901.sql.gz
confere "e: nome com espaco, fica o mais recente"  existe "com espaco-20260902.sql.gz"
confere "e: nome com espaco, antigo sai"           some   "com espaco-20260901.sql.gz"
confere "e: nome com hifen, fica o mais recente"   existe "-hifen-20260902.sql.gz"
confere "e: nome com hifen, antigo sai"            some   "-hifen-20260901.sql.gz"
confere "f: .dump de 21 bytes conta pelo tamanho"  existe f-20260901.dump
confere "f: .dump menor e antigo sai"              some   f-20260902.dump
confere "g: recente fica"                          existe g-20261007.sql.gz
confere "g: de ontem (menos de 7 dias) fica"       existe g-20261006.sql.gz

echo "== 3. nome que e so a data (familia sem nome)"
novo_dir
head -c 100 /dev/urandom > 20260901; touch -d '30 days ago' 20260901
head -c 100 /dev/urandom > 20260902; touch -d '29 days ago' 20260902
bom h-20260901.sql.gz; touch -d '30 days ago' h-20260901.sql.gz
bom h-20260902.sql.gz; touch -d '29 days ago' h-20260902.sql.gz
saida=$(ROTACAO_DIR="$T/d" bash "$S" 2>&1); rc=$?
echo "$saida" | sed 's/^/  saida: /'; echo "  codigo $rc"
[ $rc -eq 0 ] && ! grep -q 'bad array subscript' <<<"$saida" && echo "  ok   nao aborta" || { echo "  ERRO nome so com a data aborta a rotacao"; falhas=$((falhas+1)); }
grep -q 'familias preservadas: 2$' <<<"$saida" && echo "  ok   duas familias" || { echo "  ERRO esperado: familias preservadas: 2"; falhas=$((falhas+1)); }
confere "so a data: fica o mais recente"           existe 20260902
confere "so a data: antigo sai"                    some   20260901
confere "h: familia vizinha, fica o mais recente"  existe h-20260902.sql.gz
confere "h: familia vizinha, antigo sai"           some   h-20260901.sql.gz
cd /; echo "== $falhas erro(s)"; [ $falhas -eq 0 ]
