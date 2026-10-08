#!/usr/bin/env python3
"""Ciclo de roadmap (sem LLM): mantém o quadro abastecido, um passo por vez.

Para cada repositório de `PROJETOS-ATIVOS.md`:
  - `ativo`: mantém um item especificado e um item em desenvolvimento;
  - `a confirmar`: mantém só o item especificado;
  - qualquer outra situação (ex.: `pausado`): não faz nada.

O script não especifica nem programa: ele abre tarefas no quadro
(`equipe_tarefas`) para as raias `roadmap` e `engineer`, acompanha o estado delas
e avança a fase em `roadmap_ciclo`. Quem executa é a equipe do OpenClaw.

Uso: ciclo_roadmap.py [--simula]
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comum  # noqa: E402

PROJETOS = Path(os.environ.get("ROTINAS_PROJETOS", Path.home() / "projetos"))
ATIVOS = Path(os.environ.get("ROADMAP_ATIVOS",
                             Path.home() / ".openclaw/workspace/roadmap/PROJETOS-ATIVOS.md"))
SAIDA = Path(os.environ.get("ROTINAS_SAIDA", Path.home() / ".agents/shared/rotinas")) / "roadmap.md"
MAX_ABERTAS = int(os.environ.get("ROADMAP_MAX_ABERTAS", 3))  # tarefas de roadmap abertas no quadro
CANDIDATOS = 8  # itens oferecidos ao roadmap para escolher o próximo executável
ENCERRADA = ("concluida", "cancelada")


def git(repo, *args):
    r = subprocess.run(["git", "-C", str(PROJETOS / repo), *args],
                       capture_output=True, text=True, timeout=60)
    return r.stdout if r.returncode == 0 else ""


def situacoes():
    """{repo: situação} lido da tabela Markdown de PROJETOS-ATIVOS.md."""
    achados = {}
    for linha in ATIVOS.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\|\s*`([^`]+)`\s*\|\s*([^|]+?)\s*\|", linha)
        if m:
            achados[m.group(1)] = m.group(2).strip().lower()
    return achados


def itens_abertos(repo):
    """(arquivo, [(item_id, texto)]) dos itens `- [ ]` do roadmap na branch principal."""
    padrao = git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").strip() or "origin/main"
    arquivos = [a for a in git(repo, "ls-tree", "-r", "--name-only", padrao).splitlines()
                if re.search(r"(^|/)roadmap[^/]*\.md$", a, re.I) and a.count("/") <= 1]
    if not arquivos:
        return None, []
    arquivo = min(arquivos, key=lambda a: (a.count("/"), len(a)))
    itens = []
    for linha in git(repo, "show", f"{padrao}:{arquivo}").splitlines():
        m = re.match(r"\s*[-*]\s+\[ \]\s+(.+)", linha)
        if m:
            texto = re.sub(r"\s+", " ", m.group(1)).strip()
            itens.append((hashlib.sha1(texto.encode()).hexdigest()[:6], texto))
    return arquivo, itens


def abre(chave, raia, repo, pedido, simula):
    """Abre a tarefa (ou devolve a que já está aberta com a mesma chave). Devolve o id."""
    if simula:
        print(f"[simula] abriria {chave} para {raia}")
        return None
    return int(comum.sql(f"""
        insert into equipe_tarefas (chave, raia, repo, pedido, estado)
        values ({comum.literal(chave)}, {comum.literal(raia)}, {comum.literal(repo)},
                {comum.literal(pedido)}, 'aberta')
        on conflict (chave) where chave is not null and estado not in ('concluida', 'cancelada')
        do update set atualizada_em = equipe_tarefas.atualizada_em
        returning id;
    """).splitlines()[0])


def fase(linha_id, nova, simula, **campos):
    if simula:
        print(f"[simula] ciclo #{linha_id} -> {nova} {campos or ''}")
        return
    extra = "".join(f", {k} = {comum.literal(v)}" for k, v in campos.items())
    comum.sql(f"update roadmap_ciclo set fase = {comum.literal(nova)}, atualizado_em = now(){extra} "
              f"where id = {int(linha_id)};")


def pedido_spec(repo, arquivo, candidatos):
    lista = "\n".join(f"[{i}] {t}" for i, t in candidatos)
    return (
        f"Roadmap de {repo}: especificar o próximo item executável de `{arquivo}`.\n"
        f"Itens abertos, na ordem do arquivo:\n{lista}\n"
        "Escolha o primeiro que a equipe consegue entregar em código neste servidor e escreva a "
        "especificação fechada para o Codador (objetivo, critérios de aceite, arquivos prováveis, "
        f"como testar, o que fica de fora) em /home/avops/.agents/openclaw/out/roadmap/AAAA-MM-DD-{repo}-<item>.md. "
        "Item que depende de pessoa, compra, decisão comercial ou ferramenta que este servidor não roda "
        "não vai para o Codador: pule e siga para o seguinte. "
        "Ao fechar a tarefa, grave o caminho em `artefato` e comece a `nota` exatamente assim: "
        "`item=<id>; pulados=<id>,<id>` (use os ids entre colchetes; `pulados=` vazio se não pulou nada). "
        "Se nenhum item for executável, feche como `cancelada` com `item=; pulados=<todos os ids>`."
    )


def pedido_dev(repo, arquivo, item_id, texto, artefato):
    return (
        f"Roadmap de {repo}: desenvolver o item [{item_id}] \"{texto}\".\n"
        f"Especificação: {artefato or 'ver tarefa de especificação no quadro'}.\n"
        "Fluxo (regra do Nicolas de 2026-10-05, ~/AGENTS.md seção 4): `git pull --rebase origin main`, "
        "implementar, rodar os testes e o build do repositório, pedir a conferência do `reviewer`, "
        f"marcar o item como `- [x]` em `{arquivo}` no mesmo commit e `git push origin main`. "
        "Sem `--force`, sem segredo no commit. Teste falhando ou reviewer reprovando: não envie; "
        "corrija e tente de novo. Se não fechar, feche como `cancelada` com o motivo exato na `nota` "
        "(`bloqueada` só quando falta algo que a equipe não obtém sozinha: credencial, pagamento, serviço fora). "
        "Ao terminar, grave o hash do commit em `artefato` e feche como `concluida`."
    )


def roda_repo(repo, situacao, vagas, simula, relato):
    arquivo, abertos = itens_abertos(repo)
    if not arquivo:
        relato.append((repo, situacao, "sem roadmap com caixas de marcar na branch principal"))
        return vagas
    textos = dict(abertos)
    linhas = comum.consulta(f"""
        select c.*, s.estado as spec_estado, s.nota as spec_nota, s.artefato as spec_artefato,
               d.estado as dev_estado
          from roadmap_ciclo c
          left join equipe_tarefas s on s.id = c.tarefa_spec
          left join equipe_tarefas d on d.id = c.tarefa_dev
         where c.repo = {comum.literal(repo)} order by c.id""")

    # 1) acompanha o que já está em curso
    for l in linhas:
        if l["fase"] == "especificando" and l["spec_estado"] in ENCERRADA:
            nota = l["spec_nota"] or ""
            escolhido = re.search(r"item=([0-9a-f]{6})", nota)
            pulados = re.search(r"pulados=([0-9a-f, ]+)", nota)
            for pid in re.findall(r"[0-9a-f]{6}", pulados.group(1)) if pulados else []:
                if pid in textos and not simula:
                    comum.sql(f"""insert into roadmap_ciclo (repo, item_id, item_texto, fase, tarefa_spec)
                                  values ({comum.literal(repo)}, {comum.literal(pid)},
                                          {comum.literal(textos[pid])}, 'pulado', {l['tarefa_spec']});""")
            if l["spec_estado"] == "concluida" and escolhido and escolhido.group(1) in textos:
                iid = escolhido.group(1)
                fase(l["id"], "especificado", simula, item_id=iid, item_texto=textos[iid],
                     artefato=l["spec_artefato"])
                l.update(fase="especificado", item_id=iid, item_texto=textos[iid], artefato=l["spec_artefato"])
            else:
                fase(l["id"], "cancelado", simula)
                l["fase"] = "cancelado"
        elif l["fase"] == "desenvolvendo":
            if l["item_id"] not in textos or l["dev_estado"] == "concluida":
                fase(l["id"], "concluido", simula)
                l["fase"] = "concluido"
                if l["dev_estado"] not in ENCERRADA and not simula:
                    comum.encerra_tarefa(f"roadmap:{repo}:dev", "item marcado como feito no roadmap da main")
            elif l["dev_estado"] == "cancelada":
                fase(l["id"], "cancelado", simula)
                l["fase"] = "cancelado"
        elif l["fase"] == "especificado" and l["item_id"] not in textos:
            fase(l["id"], "concluido", simula)  # alguém entregou por fora do ciclo
            l["fase"] = "concluido"

    em = lambda f: [l for l in linhas if l["fase"] == f]  # noqa: E731
    usados = {l["item_id"] for l in linhas if l["item_id"]}
    candidatos = [(i, t) for i, t in abertos if i not in usados][:CANDIDATOS]

    # 2) vaga de desenvolvimento (só repositório ativo)
    if situacao == "ativo" and not em("desenvolvendo") and em("especificado") and vagas > 0:
        l = em("especificado")[0]
        tid = abre(f"roadmap:{repo}:dev", "engineer", repo,
                   pedido_dev(repo, arquivo, l["item_id"], l["item_texto"], l["artefato"]), simula)
        fase(l["id"], "desenvolvendo", simula, **({"tarefa_dev": tid} if tid else {}))
        l["fase"] = "desenvolvendo"
        vagas -= 1

    # 3) vaga de especificação (sempre um item pronto na fila)
    if not em("especificando") and not em("especificado") and candidatos and vagas > 0:
        tid = abre(f"roadmap:{repo}:spec", "roadmap", repo, pedido_spec(repo, arquivo, candidatos), simula)
        if tid:
            comum.sql(f"""insert into roadmap_ciclo (repo, fase, tarefa_spec)
                          values ({comum.literal(repo)}, 'especificando', {tid});""")
        linhas.append({"fase": "especificando", "item_id": None, "item_texto": None})
        vagas -= 1

    def rotulo(f):
        return "; ".join((l["item_texto"] or "escolhendo o item")[:70] for l in em(f)) or "—"
    relato.append((repo, situacao,
                   f"{len(abertos)} abertos · desenvolvendo: {rotulo('desenvolvendo')} · "
                   f"especificado: {rotulo('especificado')} · especificando: {rotulo('especificando')} · "
                   f"concluídos pelo ciclo: {len(em('concluido'))} · pulados: {len(em('pulado'))}"))
    return vagas


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--simula", action="store_true", help="não grava nada; só mostra o que faria")
    a = ap.parse_args()

    abertas = int(comum.sql("select count(*) from equipe_tarefas where chave like 'roadmap:%' "
                            "and estado not in ('concluida', 'cancelada');") or 0)
    vagas = max(0, MAX_ABERTAS - abertas)
    relato = []
    todos = situacoes()
    # quem está há mais tempo sem movimento no ciclo é atendido primeiro
    ordem = {r["repo"]: r["ultimo"] for r in comum.consulta(
        "select repo, max(atualizado_em)::text as ultimo from roadmap_ciclo group by repo")}
    for repo in sorted(todos, key=lambda r: ordem.get(r, "")):
        situacao = todos[repo]
        if situacao not in ("ativo", "a confirmar"):
            relato.append((repo, situacao, "fora do ciclo"))
            continue
        if not (PROJETOS / repo / ".git").exists():
            relato.append((repo, situacao, "repositório não encontrado em ~/projetos"))
            continue
        vagas = roda_repo(repo, situacao, vagas, a.simula, relato)

    agora = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    texto = [f"# Ciclo de roadmap — {agora}", "",
             "Gerado por `infra/rotinas-openclaw/ciclo_roadmap.py` (sem LLM). A lista de projetos é "
             f"`{ATIVOS}`; no máximo {MAX_ABERTAS} tarefas de roadmap abertas no quadro.", "",
             "| Repositório | Situação | Estado do ciclo |", "|---|---|---|"]
    texto += [f"| `{r}` | {s} | {d} |" for r, s, d in sorted(relato)]
    if not a.simula:
        SAIDA.parent.mkdir(parents=True, exist_ok=True)
        SAIDA.write_text("\n".join(texto) + "\n", encoding="utf-8")
    print(json.dumps({"repos": len(relato), "tarefas_roadmap_abertas_antes": abertas,
                      "vagas_restantes": vagas, "pagina": str(SAIDA)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
