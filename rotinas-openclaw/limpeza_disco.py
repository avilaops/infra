#!/usr/bin/env python3
"""Limpeza de disco do servidor creators (sem LLM), chamada pelo vigia de saúde.

Só age com o `/` acima de LIMPEZA_DISCO_PCT (75%). Apaga apenas o que se refaz sozinho:
  1. cache do npm (`npm cache clean --force`), se não houver npm rodando;
  2. diretórios de build do CMake em /tmp (os que têm CMakeCache.txt e CMakeFiles/, com
     a fonte fora deles, nenhum CMakeLists.txt dentro e nenhuma anotação, remendo ou
     código solto na raiz), parados há mais de 6 h e sem processo com pasta de trabalho,
     executável ou arquivo aberto neles;
  3. pacotes baixados pelo apt (`apt-get clean`), se houver sudo sem senha para isso.
Nunca toca em backup, dump, banco, volume do docker, repositório, ~/.openclaw, ~/.agents
nem node_modules: fora o cache do npm e o do apt, só mexe dentro de /tmp, sem seguir
link simbólico e sem passar para outro disco.

Rodado à mão, só simula (lista o que faria); apagar exige --executa.

Uso: limpeza_disco.py [--executa] [--limiar PCT]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Limites (ajuste por variável de ambiente no job, sem mexer no código)
LIMIAR_PCT = float(os.environ.get("LIMPEZA_DISCO_PCT", 75))
TMP_HORAS = float(os.environ.get("LIMPEZA_TMP_HORAS", 6))  # build parado há mais que isso
NPM_INTERVALO_H = float(os.environ.get("LIMPEZA_NPM_INTERVALO_H", 6))  # entre limpezas do cache
ORCAMENTO_S = float(os.environ.get("LIMPEZA_ORCAMENTO_S", 60))  # o job do vigia tem 90 s
# Log e hora da última limpeza do cache do npm (fora da pasta publicada, que é sem escrita)
ESTADO = Path(os.environ.get("LIMPEZA_ESTADO", Path.home() / ".local/state/rotinas-openclaw"))
LOG_LINHAS = 500

TMP = Path("/tmp")  # de propósito sem variável de ambiente: a limpeza não sai de /tmp
RAIZES = ("/tmp",)  # só aqui dentro se apaga pasta; nada de TMPDIR, que pode apontar para a home
PROC = Path("/proc")
NPM_CACHE = Path.home() / ".npm/_cacache"
APT_CACHE = Path("/var/cache/apt/archives")
MARCA = "CMakeCache.txt"
FUNDO = 6  # níveis abaixo de /tmp em que procura a marca
PODA = {"node_modules", ".git"}  # nunca entra
FONTE = ("CMakeLists.txt", ".git", "package.json")  # build feito dentro do código: não apaga
PASTA_DO_CMAKE = "CMakeFiles"  # todo build de verdade tem, ao lado da marca
CHAVE_FONTE = "CMAKE_HOME_DIRECTORY:INTERNAL="  # no CMakeCache.txt: de onde veio o código
BAIXADOS = "_deps"  # código que o próprio CMake baixa (FetchContent), na raiz do build
# Arquivo solto na raiz do build com cara de coisa de gente (quem rodou `cmake <fonte>` de
# dentro da pasta de trabalho): anotação, remendo, código. O CMake também gera arquivo
# assim em alguns projetos; nesse caso a pasta fica para sempre e aparece em `mantidos`.
DE_GENTE = (".md", ".txt", ".patch", ".diff", ".c", ".cc", ".cpp", ".cxx", ".py", ".sh")
DO_CMAKE = ("install_manifest",)  # começo de nome de .txt que o CMake grava na raiz (fora a marca)
GERENCIADORES = {"npm", "npx", "pnpm", "pnpx", "yarn"}

MB = 1024 * 1024


def espaco():
    """(total, usado, livre) do `/`, em bytes."""
    return shutil.disk_usage("/")


def uso_pct(usado, livre):
    """Uso como o `df` mostra: sobre o espaço que o usuário pode ocupar, sem a reserva do root.

    O vigia usa a mesma conta no `vigia:disco`."""
    return 100 * usado / (usado + livre)


def comando(args, timeout):
    """(código de saída, saída) de um comando; código None se não rodou ou estourou o tempo."""
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (subprocess.SubprocessError, OSError) as e:
        return None, str(e)


def processos(proc=None):
    """pid -> (pid do pai, argumentos) de cada processo legível."""
    achados = {}
    for pasta in (proc or PROC).iterdir():
        if not pasta.name.isdigit():
            continue
        try:
            # O node regrava o título do processo: os argumentos podem vir separados por espaço.
            args = (pasta / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").split()
            pai = int((pasta / "stat").read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        achados[int(pasta.name)] = (pai, args)
    return achados


def tipo_de_npm(args):
    """"exec" para `npm exec`/`npx`, "outro" para os demais usos do npm, None se não é npm."""
    if not args:
        return None
    nome, resto = os.path.basename(args[0]), args[1:]
    if nome in ("node", "nodejs"):
        while resto and resto[0].startswith("-"):
            resto = resto[1:]
        if not resto:
            return None
        nome, resto = os.path.basename(resto[0]), resto[1:]
        for sufixo in (".js", ".cjs", ".mjs", "-cli"):
            nome = nome.removesuffix(sufixo)
    if nome not in GERENCIADORES:
        return None
    sub = next((a for a in resto if not a.startswith("-")), "")
    return "exec" if nome in ("npx", "pnpx") or sub in ("exec", "x", "dlx") else "outro"


def npm_em_uso(proc=None):
    """Processos npm que podem estar lendo ou gravando o cache.

    `npm exec`/`npx` que já subiu o programa (tem processo filho) não conta: ele fica vivo
    enquanto o programa roda (servidor MCP de uma sessão, por dias) e não usa mais o cache,
    que é `_cacache`; o pacote dele fica em `_npx`, que esta limpeza não toca."""
    todos = processos(proc)
    com_filho = {pai for pai, _ in todos.values()}
    ocupados = []
    for pid, (_, args) in sorted(todos.items()):
        tipo = tipo_de_npm(args)
        if tipo == "outro" or (tipo == "exec" and pid not in com_filho):
            ocupados.append(f"{pid} {' '.join(args)[:80]}")
    return ocupados


def caminhos_em_uso(base, proc=None, prazo=lambda: 1):
    """Caminhos sob `base` em uso por algum processo: pasta de trabalho, executável,
    arquivo aberto ou mapeado. Processo de outro usuário não é legível e fica de fora;
    por isso só se apaga pasta do próprio usuário.

    Estoura TimeoutError se o tempo da rodada acabar no meio: lista pela metade não
    prova que ninguém usa a pasta."""
    prefixo = str(base).rstrip("/") + "/"
    usados = set()

    def anota(caminho):
        caminho = caminho.removesuffix(" (deleted)")
        if caminho.startswith(prefixo):
            usados.add(caminho)

    for pasta in (proc or PROC).iterdir():
        if not pasta.name.isdigit():
            continue
        if prazo() <= 0:
            raise TimeoutError
        for nome in ("cwd", "exe"):
            try:
                anota(os.readlink(pasta / nome))
            except OSError:
                pass
        try:
            for fd in os.scandir(pasta / "fd"):
                try:
                    anota(os.readlink(fd.path))
                except OSError:
                    pass
        except OSError:
            pass
        try:
            for linha in (pasta / "maps").read_text(errors="replace").splitlines():
                if prefixo in linha:
                    anota(linha.split(None, 5)[-1])
        except OSError:
            pass
    return usados


def dentro(caminho, pasta):
    return caminho == pasta or caminho.startswith(pasta.rstrip("/") + "/")


def base_permitida(base):
    """Só /tmp (os testes trocam RAIZES pela pasta temporária deles)."""
    real = os.path.realpath(base)
    return any(dentro(real, os.path.realpath(raiz)) for raiz in RAIZES)


def procura_builds(base, prazo=lambda: 1):
    """Diretórios sob `base` com CMakeCache.txt, sem seguir link nem passar para outro disco.

    A própria `base` nunca entra, e não se procura dentro de um build já achado. Se o
    tempo da rodada acabar, para de descer: o que faltou fica para a rodada seguinte."""
    disco_da_base = os.lstat(base).st_dev
    achados = []

    def desce(pasta, nivel):
        if prazo() <= 0:
            return
        try:
            entradas = list(os.scandir(pasta))
        except OSError:
            return
        if nivel and any(e.name == MARCA and e.is_file(follow_symlinks=False) for e in entradas):
            achados.append(pasta)
            return
        if nivel >= FUNDO:
            return
        for e in entradas:
            try:
                if e.name in PODA or e.is_symlink() or not e.is_dir(follow_symlinks=False) \
                        or e.stat(follow_symlinks=False).st_dev != disco_da_base:
                    continue
            except OSError:
                continue
            desce(e.path, nivel + 1)

    desce(os.path.realpath(base), 0)
    return sorted(achados)


def fonte_do_build(pasta):
    """Pasta do código que gerou o build, como o CMake anotou no CMakeCache.txt, ou None."""
    try:
        with open(os.path.join(pasta, MARCA), errors="replace") as arquivo:
            for linha in arquivo:
                if linha.startswith(CHAVE_FONTE):
                    return linha[len(CHAVE_FONTE):].strip() or None
    except OSError:
        pass
    return None


def codigo_dentro(pasta, prazo):
    """Primeiro CMakeLists.txt na árvore do build, fora do que o CMake gera ou baixa
    (CMakeFiles/ em qualquer nível, _deps/ na raiz); None se não há.

    Não segue link simbólico. Estoura TimeoutError se o tempo da rodada acabar no meio."""
    for raiz, pastas, arquivos in os.walk(pasta, followlinks=False):
        if prazo() <= 0:
            raise TimeoutError
        if FONTE[0] in arquivos or FONTE[0] in pastas:
            return os.path.join(raiz, FONTE[0])
        pastas[:] = [p for p in pastas if p != PASTA_DO_CMAKE and not (p == BAIXADOS and raiz == pasta)]
    return None


def avalia_build(pasta, agora, usados, prazo=lambda: 1):
    """(tamanho em bytes, motivo para não apagar ou None).

    Na dúvida sobre ser só build, mantém: o que sai daqui não volta."""
    topo = os.lstat(pasta)
    if topo.st_uid != os.getuid():
        return 0, "de outro usuário"
    for nome in FONTE:
        if os.path.lexists(os.path.join(pasta, nome)):
            return 0, f"tem {nome}: build dentro do código"
    do_cmake = os.path.join(pasta, PASTA_DO_CMAKE)
    if os.path.islink(do_cmake) or not os.path.isdir(do_cmake):
        return 0, f"sem {PASTA_DO_CMAKE}/ ao lado do {MARCA}: não parece build"
    fonte = fonte_do_build(pasta)
    if fonte is None:
        return 0, f"{MARCA} sem {CHAVE_FONTE.rstrip('=')}: não dá para saber onde está o código"
    if not os.path.isabs(fonte):  # o CMake sempre grava caminho absoluto: marca escrita à mão
        return 0, f"{MARCA} com fonte relativa ({fonte}): não dá para saber onde está o código"
    if dentro(os.path.realpath(fonte), os.path.realpath(pasta)):
        return 0, f"o código do build ({fonte}) fica dentro da pasta"
    try:
        soltos = sorted(e.name for e in os.scandir(pasta)
                        if e.name != MARCA and e.name.endswith(DE_GENTE) and not e.name.startswith(DO_CMAKE))
    except OSError as e:
        return 0, f"raiz ilegível ({e.strerror})"
    if soltos:
        return 0, f"tem {soltos[0]} solto na raiz: pode ser anotação ou código de alguém"
    try:
        achado = codigo_dentro(pasta, prazo)
    except TimeoutError:
        return 0, "sem tempo nesta rodada"
    if achado:
        return 0, f"tem {os.path.relpath(achado, pasta)}: código dentro do build"
    em_uso = sorted(c for c in usados if dentro(c, pasta))
    if em_uso:
        return 0, f"em uso por processo ({em_uso[0]})"
    tamanho, mais_novo = 0, max(topo.st_mtime, topo.st_ctime)
    for raiz, pastas, arquivos in os.walk(pasta, followlinks=False):
        if prazo() <= 0:
            return 0, "sem tempo nesta rodada"
        for nome in pastas + arquivos:
            try:
                st = os.lstat(os.path.join(raiz, nome))
            except OSError:
                continue
            if st.st_dev != topo.st_dev:
                return 0, f"tem outro disco montado dentro ({os.path.relpath(os.path.join(raiz, nome), pasta)})"
            tamanho += st.st_blocks * 512
            mais_novo = max(mais_novo, st.st_mtime, st.st_ctime)
    horas = (agora - mais_novo) / 3600
    if horas <= TMP_HORAS:
        return tamanho, f"mexido há {max(horas, 0):.1f} h (mín. {TMP_HORAS:g})"
    return tamanho, None


def etapa_npm(simula, agora, prazo):
    if shutil.which("npm") is None:
        return {"feito": False, "detalhe": "pulado: npm não está no PATH"}
    ultima = ESTADO / "limpeza-npm-ultima"
    try:
        horas = (agora - float(ultima.read_text().strip())) / 3600
        if 0 <= horas < NPM_INTERVALO_H:
            return {"feito": False, "detalhe": f"pulado: cache limpo há {horas:.1f} h (intervalo {NPM_INTERVALO_H:g})"}
    except (OSError, ValueError):
        pass
    ocupados = npm_em_uso()
    if ocupados:
        return {"feito": False, "detalhe": f"pulado: npm rodando ({'; '.join(ocupados[:3])})"}
    if simula:
        codigo, saida = comando(["du", "-sxm", str(NPM_CACHE)], min(20, prazo()))
        tamanho = int(saida.split()[0]) if codigo == 0 and saida.split()[0].isdigit() else None
        return {"feito": False, "estimado_mb": tamanho, "detalhe": f"rodaria npm cache clean --force ({NPM_CACHE})"}
    codigo, saida = comando(["npm", "cache", "clean", "--force"], min(40, prazo()))
    if codigo != 0:
        return {"feito": False, "erro": f"npm cache clean saiu com {codigo}: {saida[-200:]}"}
    try:
        ESTADO.mkdir(parents=True, exist_ok=True)
        ultima.write_text(f"{agora}\n")
    except OSError:
        pass
    return {"feito": True, "detalhe": "npm cache clean --force"}


def etapa_tmp(simula, agora, prazo, base=None):
    base = str(base or TMP)
    if not base_permitida(base):
        return {"feito": False, "erro": f"{base} não é /tmp: nada apagado"}
    try:
        usados = caminhos_em_uso(os.path.realpath(base), prazo=prazo)
    except TimeoutError:
        return {"feito": False, "detalhe": "pulado: sem tempo nesta rodada"}
    apagar, mantidos, erros = [], [], []
    avaliada = {}  # pasta -> (disco, inode) de antes da avaliação: é essa, e não outra, que sai
    for pasta in procura_builds(base, prazo):
        try:
            antes = os.lstat(pasta)
            avaliada[pasta] = (antes.st_dev, antes.st_ino)
            tamanho, motivo = avalia_build(pasta, agora, usados, prazo)
        except OSError as e:  # sumiu ou ficou ilegível no meio: fica
            tamanho, motivo = 0, f"não deu para avaliar ({e.strerror})"
        (mantidos if motivo else apagar).append(
            {"pasta": pasta, "mb": round(tamanho / MB), **({"motivo": motivo} if motivo else {})})
    feitos = []
    for item in apagar:
        if simula:
            continue
        pasta = item["pasta"]
        if prazo() <= 0:
            mantidos.append({**item, "motivo": "sem tempo nesta rodada"})
            continue
        try:
            # Confere de novo na hora de apagar: o caminho segue sem link e ninguém entrou nele.
            if os.path.islink(pasta) or os.path.realpath(pasta) != pasta or not base_permitida(pasta):
                raise OSError("caminho mudou desde a procura")
            na_hora = os.lstat(pasta)
            if (na_hora.st_dev, na_hora.st_ino) != avaliada[pasta]:
                raise OSError("a pasta foi trocada por outra desde a avaliação")
            try:
                agora_usados = [c for c in caminhos_em_uso(os.path.realpath(base), prazo=prazo) if dentro(c, pasta)]
            except TimeoutError:
                mantidos.append({**item, "motivo": "sem tempo nesta rodada"})
                continue
            if agora_usados:
                mantidos.append({**item, "motivo": f"em uso por processo ({agora_usados[0]})"})
                continue
            shutil.rmtree(pasta)
            feitos.append(item)
        except OSError as e:
            erros.append(f"{pasta}: {e}")
    resultado = {
        "feito": bool(feitos),
        "detalhe": (f"apagaria {len(apagar)}" if simula else f"apagou {len(feitos)}")
        + f" build(s) do CMake em {base}; mantidos {len(mantidos)}",
        "estimado_mb": sum(i["mb"] for i in (apagar if simula else feitos)),
        "itens": apagar if simula else feitos,
        "mantidos": mantidos,
    }
    if erros:
        resultado["erro"] = "; ".join(erros)[:500]
    return resultado


def etapa_apt(simula, agora, prazo):
    try:
        pacotes = [e for e in os.scandir(APT_CACHE) if e.name.endswith(".deb")]
        tamanho = round(sum(e.stat(follow_symlinks=False).st_size for e in pacotes) / MB)
    except OSError:
        return {"feito": False, "detalhe": f"pulado: {APT_CACHE} ilegível"}
    if not pacotes:
        return {"feito": False, "detalhe": "pulado: sem pacote baixado em cache"}
    # `sudo -n -l` só pergunta se o comando é permitido sem senha; não executa nada.
    codigo, _ = comando(["sudo", "-n", "-l", "apt-get", "clean"], min(10, prazo()))
    if codigo != 0:
        return {"feito": False, "detalhe": "pulado: sem sudo sem senha para apt-get clean"}
    if simula:
        return {"feito": False, "estimado_mb": tamanho,
                "detalhe": f"rodaria sudo -n apt-get clean ({len(pacotes)} pacote(s))"}
    codigo, saida = comando(["sudo", "-n", "apt-get", "clean"], min(20, prazo()))
    if codigo != 0:
        return {"feito": False, "erro": f"apt-get clean saiu com {codigo}: {saida[-200:]}"}
    return {"feito": True, "detalhe": f"sudo -n apt-get clean ({len(pacotes)} pacote(s))"}


ETAPAS = (("npm_cache", etapa_npm), ("tmp_cmake", etapa_tmp), ("apt", etapa_apt))


def registra(resultado):
    """Uma linha por rodada que apagou algo ou falhou, em ESTADO/limpeza-disco.log."""
    try:
        ESTADO.mkdir(parents=True, exist_ok=True)
        arquivo = ESTADO / "limpeza-disco.log"
        linhas = arquivo.read_text().splitlines()[-(LOG_LINHAS - 1):] if arquivo.exists() else []
        linhas.append(datetime.now().astimezone().isoformat(timespec="seconds") + " "
                      + json.dumps(resultado, ensure_ascii=False))
        arquivo.write_text("\n".join(linhas) + "\n")
    except OSError:
        pass


def limpa(simula=True, limiar=None):
    """Roda as etapas se o `/` estiver acima do limiar e devolve o que fez.

    Nenhuma falha sai daqui: cada etapa que estoura vira `erro` no resultado."""
    limiar = LIMIAR_PCT if limiar is None else limiar
    total, usado, livre = espaco()
    pct = uso_pct(usado, livre)
    resultado = {"simula": simula, "limiar_pct": limiar, "disco_antes_pct": round(pct, 1)}
    if pct <= limiar:
        return {**resultado, "rodou": False, "motivo": f"disco em {pct:.1f}%, não passa de {limiar:g}%"}
    fim = time.monotonic() + ORCAMENTO_S
    agora = time.time()
    etapas = {}
    for nome, etapa in ETAPAS:
        antes = espaco()[2]
        try:
            if fim - time.monotonic() <= 0:
                etapas[nome] = {"feito": False, "detalhe": "pulado: sem tempo nesta rodada"}
                continue
            etapas[nome] = etapa(simula, agora, lambda: max(fim - time.monotonic(), 0))
        except Exception as e:  # a limpeza nunca derruba quem a chama
            etapas[nome] = {"feito": False, "erro": f"{type(e).__name__}: {e}"[:300]}
        try:
            etapas[nome]["liberado_mb"] = round((espaco()[2] - antes) / MB) if etapas[nome].get("feito") else 0
        except OSError:
            pass
    total, usado, livre_depois = espaco()
    resultado.update(rodou=True, livre_antes_mb=round(livre / MB), livre_depois_mb=round(livre_depois / MB),
                     liberado_mb=round((livre_depois - livre) / MB),
                     disco_depois_pct=round(uso_pct(usado, livre_depois), 1), etapas=etapas)
    if not simula and any(e.get("feito") or e.get("erro") for e in etapas.values()):
        registra(resultado)
    return resultado


def resumo(resultado):
    """O resultado sem as listas de pastas, para caber na linha JSON do vigia."""
    if "etapas" not in resultado:
        return resultado
    return {**resultado, "etapas": {nome: {k: v for k, v in etapa.items() if k not in ("itens", "mantidos")}
                                    for nome, etapa in resultado["etapas"].items()}}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--executa", action="store_true", help="apaga de verdade (sem isto, só simula)")
    ap.add_argument("--limiar", type=float, help=f"uso do / acima do qual age (padrão {LIMIAR_PCT:g})")
    a = ap.parse_args()
    print(json.dumps(limpa(simula=not a.executa, limiar=a.limiar), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
