#!/usr/bin/env python3
"""Limpeza de disco do servidor creators (sem LLM), chamada pelo vigia de saúde.

Só age com o `/` acima de LIMPEZA_DISCO_PCT (75%). Apaga apenas o que se refaz sozinho:
  1. cache do npm (`npm cache clean --force`), se não houver npm rodando;
  2. diretórios de build do CMake em /tmp (os que têm CMakeCache.txt e CMakeFiles/, com
     a fonte fora deles, nenhum CMakeLists.txt dentro e nenhuma anotação, remendo ou
     código solto na raiz), parados há mais de 6 h e sem processo com pasta de trabalho,
     executável ou arquivo aberto neles;
  3. pacotes baixados pelo apt (`apt-get clean`), se houver sudo sem senha para isso;
  4. imagens Docker com tag e sem contêiner (`docker rmi <nome:tag>`, sem `-f`), criadas há
     mais de 2 h e com nenhum cliente `docker` rodando; `postgres:18-alpine` fica;
  5. logs do claude-cli (`~/.cache/claude-cli-nodejs/*/mcp-logs*`) parados há mais de 1 dia;
  6. arquivos de /tmp/node-compile-cache não lidos nem gravados há mais de 1 dia;
  7. entradas de `~/.npm/_npx` sem processo e não lidas há mais de 12 h;
  8. arquivos do store do pnpm com um só link (nenhum node_modules aponta para eles);
  9. `.next` de repositório de ~/projetos com árvore git limpa e sem processo nele;
 10. só com o `/` acima de LIMPEZA_NODE_MODULES_PCT (80%): `node_modules` de repositório com
     árvore limpa, sem processo, sem commit nem mexida há mais de 24 h, com lockfile ao lado
     e sem tarefa `em_andamento` no quadro que cite o repositório.
Nunca toca em backup, dump, banco, volume do docker, ~/.openclaw, ~/.agents nem em arquivo
`*.antes-t*`: toda pasta ou arquivo das etapas 5 a 10 passa por `recusa()`, que barra esses
caminhos mesmo se alguém os passar por engano, não segue link simbólico, não passa para
outro disco e só aceita o que é do próprio usuário. O docker só recebe os comandos de
`DOCKER_PERMITIDO`. Em repositório só saem `.next` e `node_modules` ignorados pelo git.

Rodado à mão, só simula (lista o que faria); apagar exige --executa.

Uso: limpeza_disco.py [--executa] [--limiar PCT]
"""
import argparse
import fnmatch
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
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

# Etapas fora de /tmp (tarefa 278). Cada uma só apaga dentro da própria pasta.
DOCKER_HORAS = float(os.environ.get("LIMPEZA_DOCKER_HORAS", 2))  # imagem mais nova que isso fica
LOGS_HORAS = float(os.environ.get("LIMPEZA_LOGS_HORAS", 24))  # logs do claude-cli e node-compile-cache
NPX_HORAS = float(os.environ.get("LIMPEZA_NPX_HORAS", 12))
PNPM_HORAS = float(os.environ.get("LIMPEZA_PNPM_HORAS", 1))  # arquivo do store sozinho há menos que isso fica
NODE_MODULES_PCT = float(os.environ.get("LIMPEZA_NODE_MODULES_PCT", 80))
NODE_MODULES_HORAS = float(os.environ.get("LIMPEZA_NODE_MODULES_HORAS", 24))
PROJETOS = Path.home() / "projetos"
NPX = Path.home() / ".npm/_npx"
CLAUDE_LOGS = Path.home() / ".cache/claude-cli-nodejs"
PASTA_DE_LOG = "mcp-logs"  # começo do nome das pastas de log do claude-cli; o resto do cache fica
COMPILE_CACHE = TMP / "node-compile-cache"
PNPM_STORE = Path.home() / ".local/share/pnpm/store"
# Nunca se apaga nada aqui dentro, nem pasta que contenha uma destas (ver `recusa`).
PROIBIDOS = ("/opt/backups", "/var/lib/docker", "/var/lib/postgresql",
             str(Path.home() / ".openclaw"), str(Path.home() / ".agents"))
GUARDADO = "*.antes-t*"  # cópia que alguém guardou antes de mexer: fica, e segura a pasta em que está
DOCKER_MANTER = ("postgres:18-alpine",)  # imagem mantida de propósito, mesmo sem contêiner
DOCKER_CLIENTES = {"docker", "docker-compose", "docker-buildx"}  # build, save, load ou run em curso
# Únicos comandos que esta rotina manda ao docker: três leituras e o `rmi` de um nome:tag.
DOCKER_PERMITIDO = (("images",), ("ps",), ("inspect",), ("image", "inspect"), ("rmi",))
FUNDO_REPO = 3  # níveis abaixo da raiz do repositório em que procura .next e node_modules
LOCKS = ("package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb", "bun.lock")
SEPARA = re.compile(r"""[;&|<>'"()]""")  # fim de um caminho dentro de uma linha de comando

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


def prefixos_de(base):
    """Uma base ou várias (tupla ou lista), cada uma terminada em barra."""
    bases = base if isinstance(base, (tuple, list)) else (base,)
    return tuple(str(b).rstrip("/") + "/" for b in bases)


def caminhos_em_uso(base, proc=None, prazo=lambda: 1):
    """Caminhos sob `base` (uma ou várias), ou a própria, em uso por algum processo: pasta
    de trabalho, executável, arquivo aberto ou mapeado. Processo de outro usuário não é
    legível e fica de fora; por isso só se apaga pasta do próprio usuário.

    Estoura TimeoutError se o tempo da rodada acabar no meio: lista pela metade não
    prova que ninguém usa a pasta."""
    prefixos = prefixos_de(base)
    usados = set()

    def anota(caminho):
        caminho = caminho.removesuffix(" (deleted)")
        if (caminho + "/").startswith(prefixos):
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
                if any(prefixo in linha for prefixo in prefixos):
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


def recusa(caminho, raiz):
    """Motivo para nunca apagar `caminho`, ou None se pode.

    Última barreira das etapas fora de /tmp, conferida de novo na hora de apagar: o caminho
    tem de ficar dentro de `raiz` (a pasta da etapa) sem ser ela, sem link simbólico, ser do
    próprio usuário, não ser, não conter e não ficar dentro de nada de PROIBIDOS, e não ter
    `*.antes-t*` no nome nem no caminho."""
    caminho, raiz = str(caminho), str(raiz)
    real = os.path.realpath(caminho)
    for proibido in PROIBIDOS:
        alvo = os.path.realpath(proibido)
        if any(dentro(a, b) for a, b in ((real, alvo), (caminho, proibido), (alvo, real))):
            return f"caminho proibido ({proibido})"
    if any(fnmatch.fnmatch(parte, GUARDADO) for parte in real.split("/")):
        return f"guardado ({GUARDADO})"
    if os.path.islink(caminho) or real != os.path.abspath(caminho):
        return "tem link simbólico no caminho"
    raiz_real = os.path.realpath(raiz)
    if real == raiz_real or not dentro(real, raiz_real):
        return f"fora de {raiz}"
    try:
        if os.lstat(caminho).st_uid != os.getuid():
            return "de outro usuário"
    except OSError as e:
        return f"ilegível ({e.strerror})"
    return None


def em_uso(bases, prazo, proc=None):
    """Caminhos sob `bases` em uso: os de `caminhos_em_uso` mais os que aparecem na linha de
    comando de algum processo. O node abre o programa, lê e fecha: só a linha de comando
    mostra quem roda de dentro de `~/.npm/_npx` ou de um repositório com a pasta de trabalho
    em outro lugar. A linha de comando é legível também nos processos de outro usuário."""
    usados = caminhos_em_uso(bases, proc, prazo)
    prefixos = prefixos_de(bases)
    for _, args in processos(proc).values():
        for arg in args:
            for prefixo in prefixos:
                for inicio in (prefixo, prefixo.rstrip("/")):
                    onde = arg.find(inicio)
                    if onde >= 0:
                        achado = SEPARA.split(arg[onde:])[0].rstrip("/")
                        if (achado + "/").startswith(prefixo):
                            usados.add(achado)
                        break
    return usados


def mede(pasta, prazo, lido=False):
    """(bytes, hora da última mexida, motivo para não apagar ou None) de uma árvore.

    Não segue link. Arquivo com mais de um nome conta uma vez, como no `du` (se o outro nome
    fica fora da árvore, caso do store do pnpm, o espaço só volta quando ele também sai).
    Com `lido`, a última leitura de cada arquivo também conta como mexida."""
    topo = os.lstat(pasta)
    tamanho, mais_novo, vistos = topo.st_blocks * 512, max(topo.st_mtime, topo.st_ctime), set()
    for raiz, pastas, arquivos in os.walk(pasta, followlinks=False):
        if prazo() <= 0:
            return 0, 0, "sem tempo nesta rodada"
        for nome in pastas + arquivos:
            relativo = os.path.relpath(os.path.join(raiz, nome), pasta)
            if fnmatch.fnmatch(nome, GUARDADO):
                return 0, 0, f"tem {relativo} guardado ({GUARDADO})"
            try:
                st = os.lstat(os.path.join(raiz, nome))
            except OSError as e:  # sumiu no meio: alguém está mexendo
                return 0, 0, f"{relativo} ilegível ({e.strerror})"
            if st.st_dev != topo.st_dev:
                return 0, 0, f"tem outro disco montado dentro ({relativo})"
            if st.st_uid != topo.st_uid:
                return 0, 0, f"tem {relativo} de outro usuário"
            mais_novo = max(mais_novo, st.st_mtime, st.st_ctime)
            if stat.S_ISREG(st.st_mode):
                if lido:
                    mais_novo = max(mais_novo, st.st_atime)
                if st.st_nlink > 1:
                    if st.st_ino in vistos:
                        continue
                    vistos.add(st.st_ino)
            tamanho += st.st_blocks * 512
    return tamanho, mais_novo, None


def avalia_pasta(pasta, raiz, agora, prazo, travas, horas=0, lido=False):
    """(tamanho em bytes, motivo para não apagar ou None) de uma pasta que se refaz.

    As travas baratas vêm antes da medida; pasta segurada por elas sai com tamanho 0."""
    motivo = recusa(pasta, raiz) or travas()
    if motivo:
        return 0, motivo
    tamanho, mais_novo, motivo = mede(pasta, prazo, lido)
    if motivo:
        return 0, motivo
    idade = (agora - mais_novo) / 3600
    if horas and idade <= horas:
        return tamanho, f"{'lido ou ' if lido else ''}mexido há {max(idade, 0):.1f} h (mín. {horas:g})"
    return tamanho, None


def limpa_pastas(candidatas, raiz, simula, agora, prazo, o_que, horas=0, lido=False):
    """Avalia e apaga pastas inteiras de uma etapa. `candidatas`: pares (pasta, travas), em
    que `travas()` devolve o motivo para segurar a pasta ou None.

    Na hora de apagar confere tudo de novo: `recusa`, se a pasta ainda é a que foi avaliada
    (mesmo inode) e as travas de uso."""
    apagar, mantidos, feitos, erros = [], [], [], []
    for pasta, travas in candidatas:
        item = {"pasta": pasta, "mb": 0}
        try:
            antes = os.lstat(pasta)
            tamanho, motivo = avalia_pasta(pasta, raiz, agora, prazo, travas, horas, lido)
            item["mb"] = round(tamanho / MB)
        except TimeoutError:
            motivo = "sem tempo nesta rodada"
        except OSError as e:  # sumiu ou ficou ilegível no meio: fica
            motivo = f"não deu para avaliar ({e.strerror})"
        if motivo:
            mantidos.append({**item, "motivo": motivo})
            continue
        apagar.append(item)
        if simula:
            continue
        try:
            if prazo() <= 0:
                raise TimeoutError
            na_hora = os.lstat(pasta)
            motivo = recusa(pasta, raiz) or travas()
            if not motivo and (na_hora.st_dev, na_hora.st_ino) != (antes.st_dev, antes.st_ino):
                motivo = "a pasta foi trocada por outra desde a avaliação"
            if motivo:
                mantidos.append({**item, "motivo": motivo})
                continue
            shutil.rmtree(pasta)
            feitos.append(item)
        except TimeoutError:
            mantidos.append({**item, "motivo": "sem tempo nesta rodada"})
        except OSError as e:
            erros.append(f"{pasta}: {e}")
    saem = apagar if simula else feitos
    resultado = {"feito": bool(feitos),
                 "detalhe": f"{'apagaria' if simula else 'apagou'} {len(saem)} {o_que}; mantidos {len(mantidos)}",
                 "estimado_mb": sum(i["mb"] for i in saem), "itens": saem, "mantidos": mantidos}
    if erros:
        resultado["erro"] = "; ".join(erros)[:500]
    return resultado


def limpa_arquivos(raizes, simula, agora, prazo, horas, o_que, serve=lambda relativo, st: True, lido=False):
    """Apaga arquivo a arquivo sob cada pasta de `raizes`, deixando as pastas no lugar.

    Sai só arquivo comum, do próprio usuário, no mesmo disco, que `serve(caminho relativo,
    stat)` aceite, parado há mais de `horas` (com `lido`, a leitura também conta) e que
    nenhum processo tenha aberto ou mapeado. Não segue link, não entra em pasta `*.antes-t*`
    e cada raiz passa antes por `recusa`."""
    grupos, abertos, erros, recusadas = {}, 0, [], []
    for raiz in raizes:
        raiz = str(raiz)
        if not os.path.isdir(raiz):
            continue
        motivo = recusa(raiz, os.path.dirname(os.path.abspath(raiz)))
        if motivo:
            recusadas.append(f"{raiz}: {motivo}")
            continue
        try:
            usados = caminhos_em_uso(raiz, prazo=prazo)
        except TimeoutError:
            return {"feito": False, "detalhe": "pulado: sem tempo nesta rodada"}
        disco, dono = os.lstat(raiz).st_dev, os.getuid()
        for pasta, pastas, arquivos in os.walk(raiz, followlinks=False):
            if prazo() <= 0:
                break
            pastas[:] = [p for p in pastas if not fnmatch.fnmatch(p, GUARDADO)]
            for nome in arquivos:
                caminho = os.path.join(pasta, nome)
                relativo = os.path.relpath(caminho, raiz)
                try:
                    st = os.lstat(caminho)
                except OSError:  # sumiu no meio
                    continue
                if not stat.S_ISREG(st.st_mode) or st.st_uid != dono or st.st_dev != disco \
                        or fnmatch.fnmatch(nome, GUARDADO) or not serve(relativo, st):
                    continue
                if agora - max(st.st_mtime, st.st_ctime, st.st_atime if lido else 0) <= horas * 3600:
                    continue
                if caminho in usados:
                    abertos += 1
                    continue
                try:
                    if not simula:
                        os.unlink(caminho)
                except OSError as e:
                    erros.append(f"{caminho}: {e.strerror}")
                    continue
                grupo = grupos.setdefault(os.path.join(raiz, relativo.split(os.sep)[0]) if os.sep in relativo
                                          else raiz, [0, 0])
                grupo[0] += 1
                grupo[1] += st.st_blocks * 512
    itens = [{"pasta": pasta, "arquivos": n, "mb": round(tamanho / MB, 1)} for pasta, (n, tamanho) in sorted(grupos.items())]
    total = sum(n for n, _ in grupos.values())
    resultado = {"feito": bool(total) and not simula,
                 "detalhe": f"{'apagaria' if simula else 'apagou'} {total} {o_que}; {abertos} aberto(s) por processo ficaram",
                 "estimado_mb": round(sum(tamanho for _, tamanho in grupos.values()) / MB), "itens": itens}
    if erros or recusadas:
        resultado["erro"] = "; ".join(recusadas + erros)[:500]
    return resultado


def docker(args, prazo, limite=20):
    """Roda `docker` só com o que está em DOCKER_PERMITIDO; `rmi` só de um nome:tag, sem opção.

    Volume, contêiner, `prune` e `-f` não passam daqui, nem por engano de quem chamar."""
    args = list(args)
    if not any(tuple(args[:len(p)]) == p for p in DOCKER_PERMITIDO):
        return None, f"recusado: docker {' '.join(args[:2])} não é permitido"
    if args[0] == "rmi" and (len(args) != 2 or args[1].startswith(("-", "sha256:")) or ":" not in args[1]
                             or args[1] in DOCKER_MANTER):
        return None, f"recusado: docker {' '.join(args)}"
    return comando(["docker", *args], min(limite, prazo()))


def imagens_com_conteiner(prazo):
    """Ids das imagens de todos os contêineres (parados também), ou None se não deu para saber."""
    codigo, saida = docker(["ps", "-aq", "--no-trunc"], prazo)
    if codigo != 0:
        return None
    conteineres = saida.split()
    if not conteineres:
        return set()
    codigo, saida = docker(["inspect", "--format", "{{.Image}}", *conteineres], prazo)
    usadas = saida.split()
    if codigo != 0 or len(usadas) != len(conteineres) or not all(u.startswith("sha256:") for u in usadas):
        return None
    return set(usadas)


def etapa_docker(simula, agora, prazo):
    if shutil.which("docker") is None:
        return {"feito": False, "detalhe": "pulado: docker não está no PATH"}
    clientes = [f"{pid} {' '.join(args)[:80]}" for pid, (_, args) in sorted(processos().items())
                if args and os.path.basename(args[0]) in DOCKER_CLIENTES]
    if clientes:
        return {"feito": False, "detalhe": f"pulado: docker em uso ({'; '.join(clientes[:3])})"}
    codigo, saida = docker(["images", "--no-trunc", "--format", "{{.ID}}\t{{.Repository}}\t{{.Tag}}"], prazo)
    if codigo != 0:
        return {"feito": False, "detalhe": f"pulado: docker images não respondeu ({saida[-120:]})"}
    imagens = [linha.split("\t") for linha in saida.splitlines() if linha.strip()]
    if any(len(i) != 3 or not i[0].startswith("sha256:") for i in imagens):
        return {"feito": False, "detalhe": "pulado: saída do docker images fora do esperado"}
    imagens = [(ident, f"{repo}:{tag}") for ident, repo, tag in imagens if "<none>" not in (repo, tag)]
    if not imagens:
        return {"feito": False, "detalhe": "pulado: nenhuma imagem com tag"}
    usadas = imagens_com_conteiner(prazo)
    codigo, saida = docker(["image", "inspect", "--format", "{{.Id}}\t{{.Created}}\t{{.Size}}",
                            *sorted({ident for ident, _ in imagens})], prazo)
    if usadas is None or codigo != 0:
        return {"feito": False, "detalhe": "pulado: não deu para saber quais imagens têm contêiner ou a idade delas"}
    dados = {}
    for linha in saida.splitlines():
        try:
            ident, criada, tamanho = linha.split("\t")
            criada = datetime.strptime(criada[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
            dados[ident] = (criada, int(tamanho))
        except ValueError:
            continue
    guardadas = {ident for ident, nome in imagens if nome in DOCKER_MANTER}
    apagar, mantidos, feitos, erros = [], [], [], []
    for ident, nome in sorted(imagens, key=lambda i: i[1]):
        item = {"imagem": nome, "mb": round(dados.get(ident, (0, 0))[1] / MB)}
        if ident in guardadas:
            motivo = "mantida de propósito"
        elif ident in usadas:
            motivo = "tem contêiner"
        elif ident not in dados:
            motivo = "não deu para ler a data de criação"
        elif (agora - dados[ident][0]) / 3600 <= DOCKER_HORAS:
            motivo = f"criada há {max((agora - dados[ident][0]) / 3600, 0):.1f} h (mín. {DOCKER_HORAS:g})"
        else:
            apagar.append(item)
            continue
        mantidos.append({**item, "motivo": motivo})
    for item in ([] if simula else apagar):
        if prazo() <= 0:
            mantidos.append({**item, "motivo": "sem tempo nesta rodada"})
            continue
        na_hora = imagens_com_conteiner(prazo)  # contêiner criado depois da lista segura a imagem
        if na_hora is None or na_hora - usadas:
            mantidos.append({**item, "motivo": "contêiner novo desde a avaliação"})
            continue
        codigo, saida = docker(["rmi", item["imagem"]], prazo, limite=30)
        if codigo == 0:
            feitos.append(item)
        else:
            erros.append(f"docker rmi {item['imagem']} saiu com {codigo}: {saida[-150:]}")
    saem = apagar if simula else feitos
    resultado = {"feito": bool(feitos),
                 "detalhe": f"{'apagaria' if simula else 'apagou'} {len(saem)} imagem(ns) sem contêiner; "
                            f"mantidas {len(mantidos)}",
                 "estimado_mb": sum(i["mb"] for i in saem), "itens": saem, "mantidos": mantidos}
    if erros:
        resultado["erro"] = "; ".join(erros)[:500]
    return resultado


def etapa_claude_logs(simula, agora, prazo):
    return limpa_arquivos(
        (CLAUDE_LOGS,), simula, agora, prazo, LOGS_HORAS, "log(s) do claude-cli",
        serve=lambda relativo, st: any(p.startswith(PASTA_DE_LOG) for p in relativo.split(os.sep)[:-1]))


def etapa_compile_cache(simula, agora, prazo):
    if not base_permitida(COMPILE_CACHE):
        return {"feito": False, "erro": f"{COMPILE_CACHE} não é /tmp: nada apagado"}
    return limpa_arquivos((COMPILE_CACHE,), simula, agora, prazo, LOGS_HORAS,
                          "arquivo(s) do node-compile-cache", lido=True)


def etapa_npx(simula, agora, prazo):
    raiz = os.path.realpath(NPX)
    if not os.path.isdir(raiz):
        return {"feito": False, "detalhe": f"pulado: sem {NPX}"}
    ocupados = npm_em_uso()
    if ocupados:  # um `npx` ainda baixando está gravando aqui
        return {"feito": False, "detalhe": f"pulado: npm rodando ({'; '.join(ocupados[:3])})"}

    def travas(entrada):
        usados = sorted(c for c in em_uso((entrada,), prazo) if dentro(c, entrada))
        if usados:
            return f"em uso por processo ({usados[0]})"
        return "npm rodando" if npm_em_uso() else None

    entradas = sorted(e.path for e in os.scandir(raiz) if e.is_dir(follow_symlinks=False))
    return limpa_pastas([(e, lambda e=e: travas(e)) for e in entradas], raiz, simula, agora, prazo,
                        f"entrada(s) de {NPX}", horas=NPX_HORAS, lido=True)


def etapa_pnpm(simula, agora, prazo):
    try:
        raizes = sorted(os.path.join(e.path, "files") for e in os.scandir(PNPM_STORE)
                        if e.is_dir(follow_symlinks=False))
    except OSError:
        return {"feito": False, "detalhe": f"pulado: sem {PNPM_STORE}"}
    ocupados = npm_em_uso()
    if ocupados:  # instalação em curso: o arquivo entra no store antes de ganhar o segundo nome
        return {"feito": False, "detalhe": f"pulado: gerenciador de pacotes rodando ({'; '.join(ocupados[:3])})"}
    return limpa_arquivos(raizes, simula, agora, prazo, PNPM_HORAS, "arquivo(s) do store do pnpm com um só link",
                          serve=lambda relativo, st: st.st_nlink == 1)


def repositorios(base):
    """Pastas logo abaixo de `base` que são repositório git (têm `.git`), sem link."""
    try:
        return sorted(e.path for e in os.scandir(base) if e.is_dir(follow_symlinks=False)
                      and os.path.lexists(os.path.join(e.path, ".git")))
    except OSError:
        return []


def procura_no_repo(repo, nome, prazo):
    """Pastas chamadas `nome` até FUNDO_REPO níveis abaixo da raiz do repositório.

    Não segue link, não passa para outro disco e não entra em pasta oculta, em node_modules
    nem em outro repositório aninhado (pasta com `.git` próprio)."""
    disco, achadas = os.lstat(repo).st_dev, []

    def desce(pasta, nivel):
        if prazo() <= 0:
            raise TimeoutError
        try:
            entradas = sorted(os.scandir(pasta), key=lambda e: e.name)
        except OSError:
            return
        for e in entradas:
            try:
                if e.is_symlink() or not e.is_dir(follow_symlinks=False) \
                        or e.stat(follow_symlinks=False).st_dev != disco:
                    continue
            except OSError:
                continue
            if e.name == nome:
                achadas.append(e.path)
            elif nivel < FUNDO_REPO and not e.name.startswith(".") and e.name not in PODA \
                    and not os.path.lexists(os.path.join(e.path, ".git")):
                desce(e.path, nivel + 1)

    desce(repo, 0)
    return achadas


def git(repo, *args, prazo):
    """(código, saída) de um `git` de leitura no repositório, sem gravar nem o índice."""
    return comando(["git", "--no-optional-locks", "-C", repo, *args], min(15, prazo()))


def trava_do_repo(repo, prazo):
    """Motivo para não mexer no repositório agora: processo nele ou árvore git suja."""
    usados = sorted(c for c in em_uso((repo,), prazo) if dentro(c, repo))
    if usados:
        return f"processo no repositório ({usados[0]})"
    codigo, saida = git(repo, "status", "--porcelain", "--untracked-files=normal", prazo=prazo)
    if codigo != 0:
        return f"git status não respondeu ({saida[-80:]})"
    if saida:
        return f"árvore git suja ({len(saida.splitlines())} arquivo(s))"
    return None


def trava_do_gerado(repo, pasta, prazo):
    """Motivo para não tratar `pasta` como gerada: tem `package.json` ao lado, o git a ignora
    e nada dentro dela é versionado; senão apagar deixaria a árvore suja ou levaria trabalho."""
    if not os.path.isfile(os.path.join(os.path.dirname(pasta), "package.json")):
        return "sem package.json ao lado"
    relativo = os.path.relpath(pasta, repo)
    codigo, saida = git(repo, "ls-files", "--", relativo, prazo=prazo)
    if codigo != 0 or saida:
        return "tem arquivo versionado dentro (ou o git não respondeu)"
    codigo, _ = git(repo, "check-ignore", "-q", "--", relativo, prazo=prazo)
    if codigo != 0:
        return "não é ignorada pelo git"
    return None


def limpa_dos_repos(nome, simula, agora, prazo, travas_do_repo=lambda repo: None,
                    travas_da_pasta=lambda repo, pasta: None, horas=0):
    """Apaga a pasta gerada `nome` (.next, node_modules) dos repositórios de PROJETOS."""
    base = os.path.realpath(PROJETOS)
    candidatas, sem_tempo = [], False
    for repo in repositorios(base):
        try:
            pastas = procura_no_repo(repo, nome, prazo)
        except TimeoutError:
            sem_tempo = True
            break
        for pasta in pastas:
            candidatas.append((pasta, lambda repo=repo, pasta=pasta: (
                trava_do_repo(repo, prazo) or travas_do_repo(repo) or trava_do_gerado(repo, pasta, prazo)
                or travas_da_pasta(repo, pasta))))
    resultado = limpa_pastas(candidatas, base, simula, agora, prazo, f"{nome} de repositório", horas=horas)
    if sem_tempo:
        resultado["detalhe"] += "; procura interrompida: sem tempo nesta rodada"
    return resultado


def etapa_next(simula, agora, prazo):
    return limpa_dos_repos(".next", simula, agora, prazo)


def tarefas_em_andamento(prazo):
    """Repositório e pedido das tarefas `em_andamento` do quadro, num texto só em minúsculas;
    None se o banco não respondeu (sem saber, nenhum node_modules sai)."""
    codigo, saida = comando(["psql", "-d", "agentes", "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1", "-c",
                             "select coalesce(repo, '') || ' ' || coalesce(pedido, '') from equipe_tarefas "
                             "where estado = 'em_andamento'"], min(10, prazo()))
    return saida.lower() if codigo == 0 else None


def etapa_node_modules(simula, agora, prazo):
    total, usado, livre = espaco()
    pct = uso_pct(usado, livre)
    if pct <= NODE_MODULES_PCT:
        return {"feito": False, "detalhe": f"pulado: disco em {pct:.1f}%, não passa de {NODE_MODULES_PCT:g}%"}
    tarefas = tarefas_em_andamento(prazo)
    if tarefas is None:
        return {"feito": False, "detalhe": "pulado: o quadro não respondeu, não dá para saber se há tarefa em andamento"}

    def travas_do_repo(repo):
        nome = re.escape(os.path.basename(repo).lower())
        if re.search(rf"(?<![\w.-]){nome}(?![\w-])", tarefas):
            return "tarefa em_andamento no quadro para o repositório"
        codigo, saida = git(repo, "log", "-1", "--all", "--format=%ct", prazo=prazo)
        if codigo != 0 or not saida.strip().isdigit():
            return "não deu para ler a data do último commit"
        horas = (agora - int(saida)) / 3600
        if horas <= NODE_MODULES_HORAS:
            return f"commit há {max(horas, 0):.1f} h (mín. {NODE_MODULES_HORAS:g})"
        return None

    def travas_da_pasta(repo, pasta):
        if not any(os.path.isfile(os.path.join(onde, lock)) for onde in (os.path.dirname(pasta), repo) for lock in LOCKS):
            return "sem lockfile: a reinstalação poderia trazer outras versões"
        return None

    return limpa_dos_repos("node_modules", simula, agora, prazo, travas_do_repo, travas_da_pasta,
                           horas=NODE_MODULES_HORAS)


ETAPAS = (("npm_cache", etapa_npm), ("tmp_cmake", etapa_tmp), ("apt", etapa_apt),
          ("docker_imagens", etapa_docker), ("claude_logs", etapa_claude_logs),
          ("node_compile_cache", etapa_compile_cache), ("npx", etapa_npx), ("pnpm_store", etapa_pnpm),
          ("next", etapa_next), ("node_modules", etapa_node_modules))


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
    if simula:  # o que a rodada de verdade liberaria, somadas as etapas
        resultado["estimado_mb"] = sum(e.get("estimado_mb") or 0 for e in etapas.values())
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
