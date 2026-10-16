#!/usr/bin/env python3
"""Inventário de capacidades dos agentes de IA de uma máquina da casa.

Levanta o que cada agente desta máquina tem instalado (plugins, skills, hooks e
servidores MCP do Claude, Codex, Cursor, Grok e da pasta compartilhada
~/.agents) e as skills que os projetos trazem consigo, e manda tudo para o fluxo
"[Ops] Inventário de capacidades — Ávila Ops" do n8n. O fluxo compara com a
tabela "Biblioteca de capacidades - inventario", grava só o que mudou e avisa o
Nicolas no WhatsApp.

Só saem nomes, versões, caminhos e um hash do conteúdo. Nada de variável de
ambiente, argumento de comando, cabeçalho ou parâmetro de URL de servidor MCP:
é ali que costuma morar token.

Configuração por variável de ambiente ou arquivo KEY=VALOR (padrão
/etc/inventario-capacidades.env no Linux e
%APPDATA%\\inventario-capacidades\\config.env no Windows):

  INVENTARIO_WEBHOOK   endereço do webhook do fluxo no n8n (obrigatório para enviar)
  INVENTARIO_MAQUINA   nome da máquina na tabela (pc-nicolas, apps-noclient, ...)
  INVENTARIO_PROJETOS  pastas de projetos, separadas por ";" no Windows e ":" no Linux

Uso:
  inventario_capacidades.py              levanta e envia
  inventario_capacidades.py --simula     levanta e mostra o resumo, sem enviar
  inventario_capacidades.py --saida X    grava também o JSON enviado em X
  inventario_capacidades.py --log X      acrescenta as mensagens também em X
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11 nos servidores: leitor mínimo abaixo
    tomllib = None

HOME = Path.home()
PASTAS_DE_SKILLS_DO_PROJETO = {
    ".agents/skills": "agents",
    ".claude/skills": "claude",
    ".codex/skills": "codex",
    ".cursor/skills": "cursor",
    ".grok/skills": "grok",
    "skills": "repositorio",
}
IGNORAR_NA_BUSCA = {"node_modules", ".git", ".venv", "venv", "dist", "build", ".next", "__pycache__"}


ARQUIVO_DE_LOG: Path | None = None


def diz(mensagem: str) -> None:
    agora = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    linha = f"{agora} {mensagem}"
    if sys.stdout is not None:
        print(linha, flush=True)
    if ARQUIVO_DE_LOG is not None:
        with ARQUIVO_DE_LOG.open("a", encoding="utf-8") as log:
            log.write(linha + "\n")


def ler_json(caminho: Path) -> dict | list | None:
    try:
        return json.loads(caminho.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def ler_toml(caminho: Path) -> dict:
    try:
        bruto = caminho.read_bytes()
    except OSError:
        return {}
    if tomllib is not None:
        try:
            return tomllib.loads(bruto.decode("utf-8"))
        except ValueError:
            return {}
    return ler_toml_minimo(bruto.decode("utf-8", errors="replace"))


def ler_toml_minimo(texto: str) -> dict:
    """Só o que este script usa do config.toml do Codex: plugins e mcp_servers."""
    resultado: dict = {"plugins": {}, "mcp_servers": {}}
    atual: dict | None = None
    for linha in texto.splitlines():
        linha = linha.strip()
        secao = re.match(r'^\[(plugins|mcp_servers)\.(?:"([^"]+)"|\'([^\']+)\'|([^\]."\']+))\]$', linha)
        if secao:
            nome = secao.group(2) or secao.group(3) or secao.group(4)
            atual = resultado[secao.group(1)].setdefault(nome, {})
            continue
        if linha.startswith("["):
            atual = None
            continue
        campo = re.match(r'^(enabled|command|url)\s*=\s*(.+)$', linha)
        if atual is not None and campo:
            valor = campo.group(2).strip()
            if valor in ("true", "false"):
                atual[campo.group(1)] = valor == "true"
            else:
                atual[campo.group(1)] = valor.strip("\"'")
    return resultado


def e_pasta(caminho: Path) -> bool:
    """is_dir() que trata pasta sem permissão como inexistente (no 3.12 ela levanta erro)."""
    try:
        return caminho.is_dir()
    except OSError:
        return False


def e_arquivo(caminho: Path) -> bool:
    try:
        return caminho.is_file()
    except OSError:
        return False


def subpastas(pasta: Path) -> list[Path]:
    """Subpastas em ordem alfabética; pasta ilegível conta como vazia."""
    try:
        return sorted((p for p in pasta.iterdir() if e_pasta(p)), key=lambda p: p.name.lower())
    except OSError:
        return []


def resumo_hash(conteudo: bytes | str) -> str:
    if isinstance(conteudo, str):
        conteudo = conteudo.encode("utf-8")
    return hashlib.sha256(conteudo).hexdigest()[:16]


def hash_de_arquivo(caminho: Path) -> str | None:
    try:
        return resumo_hash(caminho.read_bytes())
    except OSError:
        return None


def caminho_curto(caminho: Path | str) -> str:
    texto = str(caminho).replace("\\", "/")
    casa = str(HOME).replace("\\", "/")
    if texto.lower().startswith(casa.lower()):
        texto = "~" + texto[len(casa):]
    return texto


def descricao_mcp(definicao: dict) -> str:
    """Programa ou endereço do servidor MCP, sem argumento, variável nem parâmetro."""
    url = definicao.get("url") or definicao.get("serverUrl")
    if url:
        return re.sub(r"[?#].*$", "", str(url))
    comando = str(definicao.get("command") or "")
    return Path(comando.replace("\\", "/")).name if comando else "(sem comando)"


class Inventario:
    def __init__(self) -> None:
        self.itens: dict[tuple, dict] = {}

    def add(self, agente: str, tipo: str, nome: str, *, escopo: str = "global", versao=None,
            hash_=None, origem=None, situacao: str = "ativo") -> None:
        chave = (agente, escopo, tipo, nome)
        if chave in self.itens:
            return
        self.itens[chave] = {
            "agente": agente,
            "tipo": tipo,
            "escopo": escopo,
            "nome": nome,
            "versao": None if versao is None else str(versao),
            "hash": hash_,
            "origem": None if origem is None else caminho_curto(origem),
            "situacao": situacao,
        }

    def skills_da_pasta(self, agente: str, pasta: Path, *, escopo: str = "global", prefixo: str = "",
                        situacao: str = "ativo") -> None:
        """Cada subpasta com SKILL.md é uma skill (pastas ocultas são internas do agente)."""
        for sub in subpastas(pasta):
            arquivo = sub / "SKILL.md"
            if sub.name.startswith(".") or not e_arquivo(arquivo):
                continue
            self.add(agente, "skill", prefixo + sub.name, escopo=escopo, hash_=hash_de_arquivo(arquivo),
                     origem=sub, situacao=situacao)

    def hooks_do_arquivo(self, agente: str, arquivo: Path, fonte: str, *, situacao: str = "ativo") -> None:
        dados = ler_json(arquivo)
        if isinstance(dados, dict):
            self.hooks(agente, dados.get("hooks"), fonte, arquivo, situacao=situacao)

    def hooks(self, agente: str, eventos, fonte: str, arquivo: Path, *, situacao: str = "ativo") -> None:
        if not isinstance(eventos, dict):
            return
        for evento, grupos in eventos.items():
            numero = 0
            for grupo in grupos if isinstance(grupos, list) else []:
                for hook in grupo.get("hooks", []) if isinstance(grupo, dict) else []:
                    numero += 1
                    comando = str(hook.get("command") or hook.get("type") or "")
                    programa = comando.strip().strip('"').split('"')[0].split(" ")[0]
                    self.add(agente, "hook", f"{fonte}:{evento}:{numero}",
                             hash_=resumo_hash(comando + "|" + str(grupo.get("matcher", ""))),
                             origem=programa or arquivo, situacao=situacao)

    def mcps(self, agente: str, servidores, fonte: str | None, *, escopo: str = "global",
             situacao: str = "ativo") -> None:
        if not isinstance(servidores, dict):
            return
        for nome, definicao in servidores.items():
            if not isinstance(definicao, dict):
                continue
            descricao = descricao_mcp(definicao)
            self.add(agente, "mcp", f"{fonte}:{nome}" if fonte else str(nome), escopo=escopo,
                     hash_=resumo_hash(descricao), origem=descricao, situacao=situacao)

    def plugin(self, agente: str, nome: str, pasta: Path | None, *, versao=None, ativo: bool = True) -> None:
        """O plugin e o que vem dentro dele: skills, hooks e servidores MCP."""
        situacao = "ativo" if ativo else "desligado"
        if pasta is None or not e_pasta(pasta):
            self.add(agente, "plugin", nome, versao=versao, origem="(pasta ausente)", situacao=situacao)
            return
        manifesto = next((m for m in (pasta / ".claude-plugin" / "plugin.json", pasta / ".codex-plugin" / "plugin.json",
                                      pasta / ".cursor-plugin" / "plugin.json", pasta / "plugin.json") if e_arquivo(m)), None)
        dados = ler_json(manifesto) if manifesto else None
        if versao is None and isinstance(dados, dict):
            versao = dados.get("version")
        self.add(agente, "plugin", nome, versao=versao, hash_=hash_de_arquivo(manifesto) if manifesto else None,
                 origem=pasta, situacao=situacao)
        for arquivo in sorted(buscar(pasta, "SKILL.md")):
            sub = arquivo.parent
            self.add(agente, "skill", f"{nome}:{sub.name}", hash_=hash_de_arquivo(arquivo), origem=sub, situacao=situacao)
        self.hooks_do_arquivo(agente, pasta / "hooks" / "hooks.json", nome, situacao=situacao)
        mcp = ler_json(pasta / ".mcp.json")
        if isinstance(mcp, dict):
            self.mcps(agente, mcp.get("mcpServers", mcp), nome, situacao=situacao)


def buscar(raiz: Path, nome_arquivo: str, profundidade: int = 6):
    """os.walk sem entrar em node_modules, .git e afins, e sem passar de `profundidade`."""
    base = len(raiz.parts)
    for atual, pastas, arquivos in os.walk(raiz):
        pastas[:] = [p for p in pastas if p not in IGNORAR_NA_BUSCA]
        if len(Path(atual).parts) - base >= profundidade:
            pastas[:] = []
        if nome_arquivo in arquivos:
            yield Path(atual) / nome_arquivo


def versao_mais_recente(pasta: Path) -> Path | None:
    candidatas = [p for p in subpastas(pasta) if not p.name.startswith(".")]
    return max(candidatas, key=lambda p: p.stat().st_mtime, default=None)


def levantar_claude(inv: Inventario) -> None:
    raiz = Path(os.environ.get("CLAUDE_CONFIG_DIR") or HOME / ".claude")
    if not e_pasta(raiz):
        return
    config = ler_json(raiz / "settings.json") or {}
    ligados = config.get("enabledPlugins") or {}

    instalados = (ler_json(raiz / "plugins" / "installed_plugins.json") or {}).get("plugins") or {}
    for chave, entradas in instalados.items():
        entrada = entradas[0] if isinstance(entradas, list) and entradas else entradas
        if not isinstance(entrada, dict):
            continue
        pasta = Path(entrada["installPath"]) if entrada.get("installPath") else None
        inv.plugin("claude", chave.split("@")[0], pasta, versao=entrada.get("version"), ativo=bool(ligados.get(chave, False)))

    # Plugins sincronizados da conta claude.ai (cowork). A pasta tem cópias "~gN"
    # de versões anteriores: vale a sem sufixo.
    for conta in subpastas(raiz / "plugins" / "synced"):
        if conta.name.startswith("."):
            continue
        for pasta in sorted(subpastas(conta), key=lambda p: ("~" in p.name, p.name)):
            dados = ler_json(pasta / ".claude-plugin" / "plugin.json")
            if pasta.name.startswith(".") or not isinstance(dados, dict) or not dados.get("name"):
                continue
            nome = dados["name"]
            inv.plugin("claude", nome, pasta, ativo=bool(ligados.get(f"{nome}@synced", True)))

    inv.skills_da_pasta("claude", raiz / "skills")
    inv.hooks("claude", config.get("hooks"), "usuario", raiz / "settings.json")
    inv.mcps("claude", (ler_json(HOME / ".claude.json") or {}).get("mcpServers"), None)


def levantar_codex(inv: Inventario) -> None:
    raiz = Path(os.environ.get("CODEX_HOME") or HOME / ".codex")
    if not e_pasta(raiz):
        return
    config = ler_toml(raiz / "config.toml")
    cache = raiz / "plugins" / "cache"
    for chave, opcoes in (config.get("plugins") or {}).items():
        nome, _, mercado = chave.partition("@")
        pasta = versao_mais_recente(cache / mercado / nome)
        inv.plugin("codex", nome, pasta, ativo=bool((opcoes or {}).get("enabled", True)))
    # Plugins instalados pela conta ChatGPT (mercados "*-remote") não passam pelo
    # config.toml: valem as pastas que o Codex baixou.
    for mercado in subpastas(cache):
        if not mercado.name.endswith("-remote"):
            continue
        for plugin in subpastas(mercado):
            pasta = versao_mais_recente(plugin)
            if pasta is not None and not plugin.name.startswith("."):
                inv.plugin("codex", plugin.name, pasta)
    inv.skills_da_pasta("codex", raiz / "skills")
    inv.hooks_do_arquivo("codex", raiz / "hooks.json", "usuario")
    inv.mcps("codex", config.get("mcp_servers"), None)


def levantar_cursor(inv: Inventario) -> None:
    raiz = HOME / ".cursor"
    if not e_pasta(raiz):
        return
    inv.skills_da_pasta("cursor", raiz / "skills")
    inv.skills_da_pasta("cursor", raiz / "skills-cursor")
    inv.mcps("cursor", (ler_json(raiz / "mcp.json") or {}).get("mcpServers"), None)
    for mercado in subpastas(raiz / "plugins" / "cache"):
        for plugin in subpastas(mercado):
            pasta = versao_mais_recente(plugin)
            if pasta is not None:
                inv.plugin("cursor", plugin.name, pasta)


def levantar_grok_e_compartilhada(inv: Inventario) -> None:
    inv.skills_da_pasta("grok", Path(os.environ.get("GROK_HOME") or HOME / ".grok") / "skills")
    inv.skills_da_pasta("agents", HOME / ".agents" / "skills")


def levantar_projetos(inv: Inventario, raizes: list[Path]) -> None:
    for raiz in raizes:
        if not e_pasta(raiz):
            diz(f"pasta de projetos não encontrada: {raiz}")
            continue
        for projeto in subpastas(raiz):
            if projeto.name.startswith("."):
                continue
            escopo = "projeto:" + re.sub(r"[^A-Za-z0-9._-]", "-", projeto.name)[:80]
            for relativo, agente in PASTAS_DE_SKILLS_DO_PROJETO.items():
                inv.skills_da_pasta(agente, projeto / relativo, escopo=escopo)
            mcp = ler_json(projeto / ".mcp.json")
            if isinstance(mcp, dict):
                inv.mcps("claude", mcp.get("mcpServers"), None, escopo=escopo)


def carregar_configuracao(arquivo: str | None) -> dict:
    if arquivo:
        caminho = Path(arquivo)
    elif os.name == "nt":
        caminho = Path(os.environ.get("APPDATA", HOME)) / "inventario-capacidades" / "config.env"
    else:
        caminho = Path("/etc/inventario-capacidades.env")
    valores: dict = {}
    if caminho.is_file():
        for linha in caminho.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if linha and not linha.startswith("#") and "=" in linha:
                chave, _, valor = linha.partition("=")
                valores[chave.strip()] = valor.strip().strip("\"'")
    for chave in ("INVENTARIO_WEBHOOK", "INVENTARIO_MAQUINA", "INVENTARIO_PROJETOS"):
        if os.environ.get(chave):
            valores[chave] = os.environ[chave]
    return valores


def enviar(webhook: str, corpo: dict) -> dict:
    dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
    pedido = urllib.request.Request(webhook, data=dados, method="POST",
                                    headers={"Content-Type": "application/json", "User-Agent": "inventario-capacidades/1"})
    with urllib.request.urlopen(pedido, timeout=300) as resposta:
        return json.loads(resposta.read().decode("utf-8") or "{}")


def main() -> int:
    global ARQUIVO_DE_LOG
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Inventário de capacidades dos agentes desta máquina.")
    parser.add_argument("--simula", action="store_true", help="levanta e mostra o resumo, sem enviar")
    parser.add_argument("--saida", help="grava o JSON do inventário neste arquivo")
    parser.add_argument("--config", help="arquivo KEY=VALOR com a configuração")
    parser.add_argument("--log", help="acrescenta as mensagens também neste arquivo (útil sem console, no pythonw)")
    args = parser.parse_args()
    if args.log:
        ARQUIVO_DE_LOG = Path(args.log)

    config = carregar_configuracao(args.config)
    maquina = config.get("INVENTARIO_MAQUINA", "").strip()
    if not maquina:
        diz("INVENTARIO_MAQUINA não definido")
        return 1
    raizes = [Path(p) for p in config.get("INVENTARIO_PROJETOS", "").split(os.pathsep) if p.strip()]

    inv = Inventario()
    levantar_claude(inv)
    levantar_codex(inv)
    levantar_cursor(inv)
    levantar_grok_e_compartilhada(inv)
    levantar_projetos(inv, raizes)

    itens = list(inv.itens.values())
    contagem: dict[str, int] = {}
    for item in itens:
        rotulo = f"{item['agente']}/{item['tipo']}"
        contagem[rotulo] = contagem.get(rotulo, 0) + 1
    diz(f"{maquina}: {len(itens)} itens — " + ", ".join(f"{k}={v}" for k, v in sorted(contagem.items())))

    corpo = {"maquina": maquina, "gerado_em": dt.datetime.now(dt.timezone.utc).isoformat(), "itens": itens}
    if args.saida:
        Path(args.saida).write_text(json.dumps(corpo, ensure_ascii=False, indent=1), encoding="utf-8")
        diz(f"inventário gravado em {args.saida}")
    if args.simula:
        return 0

    webhook = config.get("INVENTARIO_WEBHOOK", "").strip()
    if not webhook:
        diz("INVENTARIO_WEBHOOK não definido")
        return 1
    try:
        resposta = enviar(webhook, corpo)
    except (urllib.error.URLError, TimeoutError, ValueError) as erro:
        diz(f"falha ao enviar: {erro}")
        return 1
    diz("n8n: " + json.dumps(resposta, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
