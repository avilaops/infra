#!/usr/bin/env python3
"""Varredura dos repositórios de ~/projetos (sem LLM).

Por repositório coleta: branch atual, arquivos sem commit, branches de agente
(claude/…, openclaw/…, codex/…), PRs abertos e estado do CI. Grava o retrato no
banco `agentes` (repo_estado, repo_branches, repo_prs, repo_varreduras) e gera a
página Markdown com o que precisa de atenção.

Uso: varredura_repos.py [--sem-fetch] [--sem-banco] [--saida ARQUIVO]
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comum  # noqa: E402

PROJETOS = Path(os.environ.get("ROTINAS_PROJETOS", Path.home() / "projetos"))
SAIDA = Path(os.environ.get("ROTINAS_SAIDA", Path.home() / ".agents/shared/rotinas")) / "repos.md"
DIAS_PARADO = 3
PARALELO = 4
AGENTES = ("claude", "openclaw", "codex")

CONSULTA = """
query($o:String!,$n:String!){repository(owner:$o,name:$n){
  defaultBranchRef{name target{... on Commit{statusCheckRollup{state}}}}
  pullRequests(states:OPEN,first:100,orderBy:{field:UPDATED_AT,direction:DESC}){nodes{
    number title isDraft headRefName mergeable reviewDecision updatedAt createdAt url
    author{login} files(first:50){nodes{path}}
    commits(last:1){nodes{commit{statusCheckRollup{state}}}}}}}}
"""

CI = {"SUCCESS": "verde", "FAILURE": "vermelho", "ERROR": "vermelho",
      "PENDING": "pendente", "EXPECTED": "pendente"}


def roda(args, cwd=None, timeout=60):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def git(repo, *args, timeout=60):
    r = roda(["git", "-C", str(repo), *args], timeout=timeout)
    return r.stdout.strip() if r.returncode == 0 else ""


def ci_de(rollup):
    return CI.get((rollup or {}).get("state"), "sem_ci")


def so_docs(caminhos):
    return bool(caminhos) and all(c.lower().endswith((".md", ".txt")) for c in caminhos)


def varre(pasta, com_fetch):
    """Devolve (estado, branches, prs) de um repositório."""
    nome = pasta.name
    estado = {"repo": nome, "remoto": None, "branch_atual": None, "arquivos_sujos": 0,
              "ultimo_commit_em": None, "ci_main": None, "erro": None}
    if not (pasta / ".git").exists():
        estado["erro"] = "sem git"
        return estado, [], []
    erros, prs, vistas = [], [], {}
    try:
        remoto = git(pasta, "remote", "get-url", "origin")
        estado["remoto"] = remoto or None
        if com_fetch and remoto:
            f = roda(["git", "-C", str(pasta), "fetch", "--prune", "--quiet", "origin"], timeout=90)
            if f.returncode != 0:
                erros.append("fetch falhou")
        estado["branch_atual"] = git(pasta, "branch", "--show-current") or "(HEAD solto)"
        estado["arquivos_sujos"] = len(git(pasta, "status", "--porcelain").splitlines())
        estado["ultimo_commit_em"] = git(pasta, "log", "-1", "--format=%cI") or None

        padrao = "main"
        m = re.search(r"github\.com[:/]([^/]+)/(.+?)(?:\.git)?$", remoto or "")
        if m:
            g = roda(["gh", "api", "graphql", "-f", f"query={CONSULTA}",
                      "-f", f"o={m.group(1)}", "-f", f"n={m.group(2)}"], timeout=60)
            if g.returncode != 0:
                erros.append("gh: " + (g.stderr.strip().splitlines() or ["erro"])[-1][:120])
            else:
                r = json.loads(g.stdout)["data"]["repository"]
                ref = r.get("defaultBranchRef") or {}
                padrao = ref.get("name") or "main"
                estado["ci_main"] = ci_de((ref.get("target") or {}).get("statusCheckRollup"))
                for p in r["pullRequests"]["nodes"]:
                    ultimo = (p["commits"]["nodes"] or [{}])[-1].get("commit", {})
                    prs.append({
                        "repo": nome, "numero": p["number"], "titulo": p["title"].strip(),
                        "autor": (p.get("author") or {}).get("login"),
                        "rascunho": p["isDraft"], "branch": p["headRefName"],
                        "mesclavel": p["mergeable"], "revisao": p.get("reviewDecision"),
                        "ci": ci_de(ultimo.get("statusCheckRollup")),
                        "so_docs": so_docs([f["path"] for f in p["files"]["nodes"]]),
                        "atualizado_em": p["updatedAt"], "criado_em": p["createdAt"],
                        "url": p["url"],
                    })
        elif remoto:
            erros.append("remoto fora do GitHub")

        com_pr = {p["branch"] for p in prs}
        linhas = git(pasta, "for-each-ref", "--format=%(refname)\t%(committerdate:iso-strict)",
                     "refs/heads", "refs/remotes/origin").splitlines()
        for linha in linhas:
            ref, _, data = linha.partition("\t")
            curto = re.sub(r"^refs/(heads|remotes/origin)/", "", ref)
            agente = curto.split("/", 1)[0]
            if agente not in AGENTES or "/" not in curto:
                continue
            mesclada = roda(["git", "-C", str(pasta), "merge-base", "--is-ancestor", ref,
                             f"origin/{padrao}"]).returncode == 0
            # a mesma branch aparece local e remota: fica a de commit mais recente
            if curto not in vistas or (data or "") > (vistas[curto]["ultimo_commit_em"] or ""):
                vistas[curto] = {"repo": nome, "branch": curto, "agente": agente,
                                 "ultimo_commit_em": data or None, "mesclada": mesclada,
                                 "tem_pr_aberto": curto in com_pr}
    except Exception as e:  # um repo com problema não derruba a varredura
        erros.append(f"{type(e).__name__}: {e}"[:160])
    estado["erro"] = "; ".join(erros) or None
    return estado, list(vistas.values()), prs


def idade(iso, agora):
    if not iso:
        return None
    return agora - datetime.fromisoformat(iso.replace("Z", "+00:00"))


def dias(delta):
    return "?" if delta is None else f"{delta.days}d"


def grava(estados, branches, prs, resumo):
    agora = datetime.now(timezone.utc).isoformat()
    for linhas in (estados, branches, prs):
        for l in linhas:
            l["varrido_em"] = agora
    comum.sql(f"""
        begin;
        delete from repo_prs; delete from repo_branches; delete from repo_estado;
        insert into repo_estado select * from json_populate_recordset(null::repo_estado, {comum.literal(estados)}::json);
        insert into repo_branches select * from json_populate_recordset(null::repo_branches, {comum.literal(branches)}::json);
        insert into repo_prs select * from json_populate_recordset(null::repo_prs, {comum.literal(prs)}::json);
        insert into repo_varreduras (duracao_s, repos, prs_abertos, ci_vermelho, conflitos, parados, erros)
        values ({resumo['duracao_s']}, {resumo['repos']}, {resumo['prs_abertos']}, {resumo['ci_vermelho']},
                {resumo['conflitos']}, {resumo['parados']}, {resumo['erros']});
        delete from repo_varreduras where iniciada_em < now() - interval '30 days';
        commit;
    """)


def pagina(estados, branches, prs, fila, bloqueadas, agora):
    limite = timedelta(days=DIAS_PARADO)
    local = agora.astimezone().strftime("%Y-%m-%d %H:%M %Z")
    L = [f"# Estado dos repositórios — {local}", "",
         "Gerado por `infra/rotinas-openclaw/varredura_repos.py` (sem LLM, de hora em hora). "
         "Não edite: a próxima varredura sobrescreve.", ""]

    def pr_linha(p, extra=""):
        marca = " (rascunho)" if p["rascunho"] else ""
        return f"- `{p['repo']}` [#{p['numero']}]({p['url']}){marca} — {p['titulo']}{extra}"

    vermelhos = [p for p in prs if p["ci"] == "vermelho"]
    conflitos = [p for p in prs if p["mesclavel"] == "CONFLICTING"]
    main_vermelha = [e for e in estados if e["ci_main"] == "vermelho"]
    prs_parados = [p for p in prs if (idade(p["atualizado_em"], agora) or timedelta()) > limite]
    br_paradas = [b for b in branches if not b["mesclada"] and not b["tem_pr_aberto"]
                  and (idade(b["ultimo_commit_em"], agora) or timedelta()) > limite]
    prontos = [p for p in prs if not p["rascunho"] and p["mesclavel"] == "MERGEABLE"
               and (p["ci"] == "verde" or (p["ci"] == "sem_ci" and p["so_docs"]))]
    sujos = [e for e in estados if e["arquivos_sujos"]]
    com_erro = [e for e in estados if e["erro"] and e["erro"] != "sem git"]

    L += [f"**Resumo:** {len(estados)} repositórios · {len(prs)} PRs abertos "
          f"({sum(p['so_docs'] for p in prs)} só de documentação) · {len(vermelhos)} com CI vermelho · "
          f"{len(conflitos)} em conflito · {len(prs_parados) + len(br_paradas)} parados há mais de "
          f"{DIAS_PARADO} dias · {len(fila)} tarefas na fila da equipe.", ""]

    L += ["## 1. Fila da equipe (abertas e em andamento)", ""]
    if fila:
        for t in fila:
            repo = f" `{t['repo']}`" if t.get("repo") else ""
            L.append(f"- **#{t['id']}**{repo} ({t['raia']}, {t['estado']}) — {t['pedido'].splitlines()[0]}")
            if t.get("decisao"):
                L.append(f"  - Decisão tomada: {t['decisao']}")
    else:
        L.append("Nada na fila.")
    if bloqueadas:
        L += ["", "Bloqueadas (falta algo que a equipe não obtém sozinha):"]
        L += [f"- **#{t['id']}** ({t['raia']}) — {t['pedido']}: {t.get('nota') or 'sem motivo anotado'}"
              for t in bloqueadas]

    L += ["", "## 2. CI vermelho ou conflito", ""]
    if not (vermelhos or conflitos or main_vermelha):
        L.append("Nada.")
    if main_vermelha:
        L.append("Branch principal com CI vermelho: " + ", ".join(f"`{e['repo']}`" for e in main_vermelha))
        L.append("")
    L += [pr_linha(p, " — **CI vermelho**" + (" e **conflito**" if p in conflitos else "")) for p in vermelhos]
    L += [pr_linha(p, " — **conflito com a main**") for p in conflitos if p not in vermelhos]

    L += ["", f"## 3. Parado há mais de {DIAS_PARADO} dias", ""]
    if not (prs_parados or br_paradas):
        L.append("Nada.")
    L += [pr_linha(p, f" — sem movimento há {dias(idade(p['atualizado_em'], agora))}") for p in prs_parados]
    L += [f"- `{b['repo']}` branch `{b['branch']}` sem PR e fora da main — último commit há "
          f"{dias(idade(b['ultimo_commit_em'], agora))}" for b in br_paradas]

    L += ["", "## 4. Prontos para a main (mescláveis, sem rascunho, CI verde ou só documentação)", ""]
    L += [pr_linha(p, " — só documentação" if p["so_docs"] else "") for p in prontos] or ["Nada."]

    if sujos or com_erro:
        L += ["", "## 5. Cópia local com pendência", ""]
        L += [f"- `{e['repo']}` na branch `{e['branch_atual']}` com {e['arquivos_sujos']} arquivo(s) sem commit"
              for e in sujos]
        L += [f"- `{e['repo']}`: {e['erro']}" for e in com_erro]

    L += ["", "## Todos os repositórios", "",
          "| Repositório | Branch local | CI da main | PRs | Branches de agente fora da main |",
          "|---|---|---|---|---|"]
    for e in estados:
        n_prs = sum(1 for p in prs if p["repo"] == e["repo"])
        n_br = sum(1 for b in branches if b["repo"] == e["repo"] and not b["mesclada"])
        L.append(f"| `{e['repo']}` | {e['branch_atual'] or e['erro'] or '-'} | {e['ci_main'] or '-'} "
                 f"| {n_prs} | {n_br} |")
    return "\n".join(L) + "\n", {
        "ci_vermelho": len(vermelhos) + len(main_vermelha), "conflitos": len(conflitos),
        "parados": len(prs_parados) + len(br_paradas), "erros": len(com_erro)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sem-fetch", action="store_true", help="não roda git fetch")
    ap.add_argument("--sem-banco", action="store_true", help="não grava no banco (teste)")
    ap.add_argument("--saida", type=Path, default=SAIDA)
    a = ap.parse_args()

    inicio = time.time()
    pastas = sorted((p for p in PROJETOS.iterdir() if p.is_dir() and not p.name.startswith(".")),
                    key=lambda p: p.name.lower())
    with ThreadPoolExecutor(PARALELO) as ex:
        resultados = list(ex.map(lambda p: varre(p, not a.sem_fetch), pastas))
    estados = [r[0] for r in resultados]
    branches = [b for r in resultados for b in r[1]]
    prs = [p for r in resultados for p in r[2]]

    fila = bloqueadas = []
    if not a.sem_banco:
        fila = comum.consulta("select id, raia, repo, estado, left(pedido, 200) as pedido, decisao "
                              "from equipe_tarefas where estado in ('aberta', 'em_andamento') order by id")
        bloqueadas = comum.consulta("select id, raia, pedido, left(nota, 300) as nota from equipe_tarefas "
                                    "where estado = 'bloqueada' order by id")
    agora = datetime.now(timezone.utc)
    texto, contas = pagina(estados, branches, prs, fila, bloqueadas, agora)
    resumo = {"duracao_s": round(time.time() - inicio, 1), "repos": len(estados),
              "prs_abertos": len(prs), **contas}

    a.saida.parent.mkdir(parents=True, exist_ok=True)
    tmp = a.saida.with_suffix(".tmp")
    tmp.write_text(texto, encoding="utf-8")
    tmp.replace(a.saida)
    if not a.sem_banco:
        grava(estados, branches, prs, resumo)
    print(json.dumps({**resumo, "pagina": str(a.saida)}, ensure_ascii=False))
    # falha só quando nada pôde ser consultado (ex.: gh sem autenticação)
    com_git = sum(1 for e in estados if e["erro"] != "sem git")
    return 1 if resumo["erros"] and resumo["erros"] == com_git else 0


if __name__ == "__main__":
    sys.exit(main())
