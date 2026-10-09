#!/usr/bin/env python3
"""Roda a suíte inteira do commit publicado, uma vez por dia (sem LLM).

O publicador só roda os testes no commit novo. Teste que estraga com o relógio (data
fixa que vence, por exemplo) deixava a `main` vermelha sem ninguém ver até o push
seguinte, que aí não era publicado: foi assim de 08/10/2026 05:30 UTC até a tarefa 202.

Quem chama é o `publica_rotinas.sh`, no fim de cada rodada (a cada 10 min), sem job
próprio. Na maioria das vezes sai sem fazer nada: só roda quando o commit publicado
mudou, a última conferência tem mais de SUITE_INTERVALO_H ou ela falhou há mais de
SUITE_REPETE_H (falha do momento não fica um dia inteiro em alerta). Extrai o commit inteiro
do repositório do publicador (`repo.git`) numa pasta temporária, roda
`python3 -m unittest discover -s tests` e guarda o resultado num arquivo de estado.
Não abre tarefa: quem lê o estado e abre `vigia:suite-main` é o `vigia_saude.py`.

Saída: 0 se passou ou não era hora, 1 se a suíte falhou, 2 se não deu para conferir.

Uso: suite_da_main.py [--forca] [--simula]
"""
import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

PUBLICADO = Path(os.environ.get("ROTINAS_PUBLICADO", Path.home() / ".local/share/rotinas-openclaw"))
ESTADO = Path(os.environ.get("ROTINAS_SUITE_ESTADO",
                             Path.home() / ".local/state/rotinas-openclaw/suite-main"))
INTERVALO_H = float(os.environ.get("ROTINAS_SUITE_INTERVALO_H", 24))
# Depois de uma conferência com falha, tenta de novo neste intervalo.
REPETE_H = float(os.environ.get("ROTINAS_SUITE_REPETE_H", 1))
# O job do publicador tem 120 s; a suíte leva uns 25 s.
LIMITE_S = int(os.environ.get("ROTINAS_SUITE_LIMITE_S", 75))
# Extrair o commit (1,5 MB) leva menos de 1 s.
LIMITE_EXTRAI_S = 10
PREFIXO = "suite-main-"
SOBRA_MAX_H = 1


def le_estado(arquivo):
    """Resultado da última conferência; None se não há ou está ilegível."""
    try:
        estado = json.loads(arquivo.read_text())
        quando = datetime.fromisoformat(estado["quando"])
        if quando.tzinfo is None or not isinstance(estado["saida"], int):
            return None
        return {**estado, "quando": quando}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def e_hora(estado, commit, agora):
    if estado is None or estado.get("commit") != commit:
        return True
    intervalo = REPETE_H if estado["saida"] else INTERVALO_H
    return (agora - estado["quando"]).total_seconds() >= intervalo * 3600


def apaga(pasta):
    subprocess.run(["chmod", "-R", "u+w", str(pasta)], capture_output=True)
    shutil.rmtree(pasta, ignore_errors=True)


def apaga_sobras():
    """Pastas de conferência interrompida sem chance de limpar (SIGKILL, falta de energia)."""
    corte = time.time() - SOBRA_MAX_H * 3600
    for sobra in Path(tempfile.gettempdir()).glob(PREFIXO + "*"):
        try:
            velha = sobra.is_dir() and not sobra.is_symlink() and sobra.lstat().st_mtime < corte
        except OSError:
            continue
        if velha:
            apaga(sobra)


def mata(suite):
    """Mata o grupo inteiro da suíte (ela e o que os testes tiverem deixado rodando)."""
    try:
        os.killpg(suite.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    suite.wait()


def roda_a_suite(repo, commit):
    """(código de saída, saída do unittest) da suíte daquele commit, numa pasta temporária."""
    apaga_sobras()
    pasta = Path(tempfile.mkdtemp(prefix=PREFIXO))
    suite = None
    try:
        # Os temporários dos próprios testes ficam dentro da pasta: somem junto com ela.
        codigo, temporarios = pasta / "codigo", pasta / "tmp"
        codigo.mkdir()
        temporarios.mkdir()
        arquivo = subprocess.run(["git", "-C", str(repo), "archive", commit],
                                 capture_output=True, timeout=LIMITE_EXTRAI_S, check=True)
        subprocess.run(["tar", "-x", "-C", str(codigo)], input=arquivo.stdout,
                       timeout=LIMITE_EXTRAI_S, check=True)
        # Sessão própria: no limite (ou no sinal) morre a suíte inteira, com os filhos dos testes.
        suite = subprocess.Popen([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                                 cwd=codigo, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 start_new_session=True,
                                 env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": str(temporarios)})
        try:
            saida = suite.communicate(timeout=LIMITE_S)[0]
            return suite.returncode, saida
        except subprocess.TimeoutExpired:
            mata(suite)
            try:
                return 124, suite.communicate(timeout=5)[0]
            except subprocess.TimeoutExpired:  # filho de teste em outra sessão segurando a saída
                return 124, ""
    finally:
        if suite is not None:
            mata(suite)
        apaga(pasta)


def resultado(commit, codigo, saida, agora, duracao):
    """O que vai para o arquivo de estado: o resumo do unittest e os testes que falharam."""
    m = re.search(r"^Ran (\d+) tests?", saida, flags=re.M)
    falhas = re.findall(r"^((?:FAIL|ERROR): .{1,160})", saida, flags=re.M)
    return {"quando": agora.isoformat(timespec="seconds"), "commit": commit, "saida": codigo,
            "testes": int(m.group(1)) if m else None, "falhas": falhas[:8],
            "estourou": codigo == 124, "duracao_s": round(duracao, 1)}


def guarda(arquivo, estado):
    """Grava de uma vez (temporário na mesma pasta + troca): leitor nunca vê pela metade."""
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    # Nome próprio de cada gravação: conferência à mão junto com a do publicador não embaralha.
    descritor, novo = tempfile.mkstemp(dir=arquivo.parent, prefix=arquivo.name + ".", suffix=".novo")
    try:
        with os.fdopen(descritor, "w", encoding="utf-8") as f:
            f.write(json.dumps(estado, ensure_ascii=False) + "\n")
        os.chmod(novo, 0o644)
        os.replace(novo, arquivo)
    except BaseException:
        Path(novo).unlink(missing_ok=True)
        raise


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--forca", action="store_true", help="roda mesmo que não seja hora")
    ap.add_argument("--simula", action="store_true",
                    help="roda e imprime, sem gravar o estado (o vigia não fica sabendo)")
    a = ap.parse_args()
    # Job morto no limite de tempo: sai pelo `finally`, que apaga a pasta extraída.
    for sinal in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sinal, lambda numero, _: sys.exit(128 + numero))

    try:
        commit = os.readlink(PUBLICADO / "atual").rsplit("/", 1)[-1]
    except OSError:
        commit = ""
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        print(json.dumps({"conferiu": False, "erro": "sem versão publicada (link atual)"}, ensure_ascii=False))
        return 2
    agora = datetime.now(timezone.utc)
    if not a.forca and not e_hora(le_estado(ESTADO), commit, agora):
        print(json.dumps({"conferiu": False, "commit": commit}))
        return 0

    inicio = time.monotonic()
    try:
        codigo, saida = roda_a_suite(PUBLICADO / "repo.git", commit)
    except (subprocess.SubprocessError, OSError) as e:
        # Não deu nem para rodar (repo.git sem o commit, /tmp cheio): o estado antigo fica,
        # e o vigia alerta quando ele passar da idade.
        print(json.dumps({"conferiu": False, "commit": commit, "erro": f"{type(e).__name__}: {e}"[:300]},
                         ensure_ascii=False))
        return 2
    estado = resultado(commit, codigo, saida, agora, time.monotonic() - inicio)
    if not a.simula:
        guarda(ESTADO, estado)
    if codigo:
        print(saida[-3000:], file=sys.stderr)
    print(json.dumps({"conferiu": True, **estado, **({"simulacao": True} if a.simula else {})},
                     ensure_ascii=False))
    return 1 if codigo else 0


if __name__ == "__main__":
    sys.exit(main())
