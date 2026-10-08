"""Apoio comum das rotinas: banco `agentes` via psql (sem dependência externa)."""
import json
import secrets
import subprocess

BANCO = "agentes"


def sql(texto, timeout=60):
    """Roda SQL no banco agentes e devolve a saída crua (sem cabeçalho)."""
    r = subprocess.run(
        ["psql", "-d", BANCO, "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1", "-f", "-"],
        input=texto, capture_output=True, text=True, timeout=timeout,
    )
    if r.returncode != 0:
        raise RuntimeError(f"psql falhou: {r.stderr.strip()[:500]}")
    return r.stdout.strip()


def consulta(select):
    """Devolve o resultado de um SELECT como lista de dicionários."""
    saida = sql(f"select coalesce(json_agg(t), '[]'::json) from ({select}) t;")
    return json.loads(saida or "[]")


def literal(valor):
    """Literal SQL seguro (dollar-quoting com etiqueta aleatória) para texto ou JSON."""
    if valor is None:
        return "null"
    if not isinstance(valor, str):
        valor = json.dumps(valor, ensure_ascii=False)
    etiqueta = "q" + secrets.token_hex(6)
    return f"${etiqueta}${valor}${etiqueta}$"


def registra_tarefa(chave, raia, pedido, nota, estado="aberta", repo=None, decisao=None):
    """Abre tarefa no quadro, ou atualiza a que já está aberta com a mesma chave.

    Devolve True quando criou tarefa nova."""
    saida = sql(f"""
        insert into equipe_tarefas (chave, raia, pedido, nota, estado, repo, decisao)
        values ({literal(chave)}, {literal(raia)}, {literal(pedido)}, {literal(nota)},
                {literal(estado)}, {literal(repo)}, {literal(decisao)})
        on conflict (chave) where chave is not null and estado not in ('concluida', 'cancelada')
        do update set nota = excluded.nota, atualizada_em = now()
        returning (xmax = 0);
    """)
    return saida.splitlines()[0] == "t" if saida else False


def encerra_tarefa(chave, nota):
    """Conclui a tarefa aberta com esta chave (o problema passou sozinho)."""
    sql(f"""
        update equipe_tarefas
           set estado = 'concluida', atualizada_em = now(),
               nota = coalesce(nota, '') || ' | ' || {literal(nota)}
         where chave = {literal(chave)} and estado in ('aberta', 'bloqueada');
    """)
