#!/usr/bin/env python3
"""Vigia de saúde do servidor creators (sem LLM).

Mede memória, swap, disco, o gateway do OpenClaw (ativo, memória, reinícios,
mortes por sinal/OOM), a validade do certificado do Caddy, a última rodada do
backup dos bancos do `applications` (lida por SSH) e se as rotinas publicadas
acompanham a `main` do GitHub. Toda medida vai para
`saude_medidas`; o quadro (`equipe_tarefas`, raia ops) só recebe tarefa quando um
limite é ultrapassado, e a tarefa se encerra sozinha quando o problema passa.
Depois de gravar a medida, chama a limpeza de disco (`limpeza_disco.py`), que só age
com o `/` acima de 75%.

Atenção: sem `--sem-banco` é a rodada de verdade, igual à do job, mesmo à mão: grava a
medida, abre e encerra tarefa no quadro e a limpeza APAGA (cache do npm, builds do CMake
em /tmp, apt). Para só olhar, use sempre `--sem-banco`.

Uso: vigia_saude.py [--sem-banco]
"""
import argparse
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comum  # noqa: E402

# Limites (ajuste por variável de ambiente no job, sem mexer no código)
FOLGA_MIN_MB = int(os.environ.get("VIGIA_FOLGA_MIN_MB", 1500))  # RAM disponível + swap livre
DISCO_MAX_PCT = int(os.environ.get("VIGIA_DISCO_MAX_PCT", 85))
GATEWAY_MEM_MAX_MB = int(os.environ.get("VIGIA_GATEWAY_MEM_MAX_MB", 2800))  # RSS + swap do processo
CERT_MIN_DIAS = int(os.environ.get("VIGIA_CERT_MIN_DIAS", 14))
JANELA_MORTES = os.environ.get("VIGIA_JANELA_MORTES", "-70min")  # só na primeira medida

SERVICO = "openclaw-gateway"
CERT_HOST = os.environ.get("VIGIA_CERT_HOST", "agentes.avilaops.com")
CERT_IP = os.environ.get("VIGIA_CERT_IP", "62.238.119.61")
BACKUP_HOST = os.environ.get("VIGIA_BACKUP_HOST", "applications")
BACKUP_LOG = os.environ.get("VIGIA_BACKUP_LOG", "/var/log/backup-bancos.log")
BACKUP_MAX_HORAS = int(os.environ.get("VIGIA_BACKUP_MAX_HORAS", 26))  # a rotina é diária (03:30 UTC)
# Hora da última vez em que o log do backup foi lido (fora da pasta publicada, que é sem escrita)
BACKUP_ESTADO = Path(os.environ.get("VIGIA_BACKUP_ESTADO",
                                    Path.home() / ".local/state/rotinas-openclaw/backup-ultima-leitura"))
# Publicação das rotinas (publica_rotinas.sh, job "Publica rotinas" a cada 10 min)
ROTINAS_JOB = os.environ.get("VIGIA_ROTINAS_JOB", "cb9e0c56-d2fa-4812-9f91-84504a29f581")
ROTINAS_ERRO_MAX_MIN = int(os.environ.get("VIGIA_ROTINAS_ERRO_MAX_MIN", 60))
ROTINAS_TOLERANCIA_MIN = int(os.environ.get("VIGIA_ROTINAS_TOLERANCIA_MIN", 20))  # 2 batidas do job
ROTINAS_PUBLICADO = Path(os.environ.get("ROTINAS_PUBLICADO",
                                        Path.home() / ".local/share/rotinas-openclaw"))
ROTINAS_ORIGEM = os.environ.get("ROTINAS_ORIGEM", "git@github.com:avilaops/infra.git")
ROTINAS_RAMO = os.environ.get("ROTINAS_RAMO", "main")
# Limpeza de disco (limpeza_disco.py): "1" apaga, "simula" só lista, "0" desliga
LIMPEZA = os.environ.get("LIMPEZA_DISCO", "1")

# Resultado de uma rodada no log: a linha `saida=N` que o script grava ao sair ou,
# em rodada anterior a ela, o resumo `bancos copiados: N  falhas: N`.
RESULTADO_BACKUP = re.compile(r"^(\S+) (?:saida=(\d+)|bancos copiados: \d+\s+falhas: (\d+))")


def roda(args, timeout=20, env=None):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                              env={**os.environ, **env} if env else None).stdout
    except (subprocess.SubprocessError, OSError):
        return ""


def memoria():
    info = {}
    for linha in Path("/proc/meminfo").read_text().splitlines():
        chave, _, resto = linha.partition(":")
        info[chave] = int(resto.split()[0])  # kB
    swap_total = info.get("SwapTotal", 0)
    swap_pct = round(100 * (swap_total - info.get("SwapFree", 0)) / swap_total) if swap_total else 0
    folga = (info["MemAvailable"] + info.get("SwapFree", 0)) // 1024
    return info["MemAvailable"] // 1024, swap_pct, folga


def disco():
    """Uso do `/` como o `df` mostra: sobre o que o usuário pode ocupar, sem a reserva do root.

    É a mesma conta da limpeza de disco (`limpeza_disco.uso_pct`). Sobre o disco inteiro o
    número saía uns 4 pontos abaixo, e os 85% do alerta só chegavam perto de 90% no `df`."""
    uso = shutil.disk_usage("/")
    return round(100 * uso.used / (uso.used + uso.free))


def desde_a_ultima_medida():
    """Início da janela de mortes: a medida anterior, para cada morte gerar um só alerta."""
    try:
        ultima = comum.sql("select to_char(max(medida_em), 'YYYY-MM-DD HH24:MI:SS') from saude_medidas;")
    except RuntimeError:
        ultima = ""
    return ultima or JANELA_MORTES


def memoria_do_processo(pid, proc=Path("/proc")):
    """VmRSS + VmSwap do processo, em MB; None se não deu para ler.

    É a memória do processo em si. O `MemoryCurrent` da unidade soma o cgroup inteiro
    com cache de arquivo e slab e deixa o swap de fora: sobe com qualquer build de agente.
    """
    if not str(pid).isdigit() or int(pid) <= 0:
        return None
    try:
        texto = (proc / str(int(pid)) / "status").read_text()
    except (OSError, UnicodeError):
        return None
    campos = dict(re.findall(r"^(VmRSS|VmSwap):\s+(\d+) kB", texto, flags=re.M))
    if "VmRSS" not in campos:  # thread de kernel ou processo já encerrado
        return None
    return (int(campos["VmRSS"]) + int(campos.get("VmSwap", 0))) // 1024


def gateway(desde):
    props = dict(l.split("=", 1) for l in roda(
        ["systemctl", "--user", "show", SERVICO, "-p", "ActiveState", "-p", "NRestarts",
         "-p", "MainPID"], timeout=5).splitlines() if "=" in l)
    diario = roda(["journalctl", "--user", "-u", SERVICO, "--since", desde,
                   "--no-pager", "-o", "cat"], timeout=15)
    mortes = len(re.findall(rf"^{SERVICO}\.service: Failed with result '(?:signal|oom-kill)'",
                            diario, flags=re.M))
    return {
        "ativo": props.get("ActiveState") == "active",
        "mem_mb": memoria_do_processo(props.get("MainPID", "")),
        "reinicios": int(props.get("NRestarts", 0) or 0),
        "mortes": mortes,
    }


def dias_do_certificado():
    """Dias até o certificado público vencer; None se não deu para conferir."""
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((CERT_IP, 443), timeout=8) as s:
            with ctx.wrap_socket(s, server_hostname=CERT_HOST) as tls:
                fim = ssl.cert_time_to_seconds(tls.getpeercert()["notAfter"])
        return int((fim - datetime.now(timezone.utc).timestamp()) // 86400)
    except (OSError, ssl.SSLError, KeyError, ValueError):
        return None


def log_do_backup():
    """Fim do log do backup no `applications`; vazio se o SSH não respondeu."""
    return roda(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", BACKUP_HOST,
                 f"tail -n 80 {BACKUP_LOG}"], timeout=25)


def backup(log, agora):
    """(em alerta?, detalhe) da última rodada do backup. Alerta `None`: não deu para ler.

    A falha do backup só existia no log do outro servidor: o `medusa_store` falhou
    todo dia de 16/09 a 06/10/2026 sem ninguém ver."""
    linhas = [linha for linha in log.splitlines() if linha.strip()]
    if not linhas:
        return None, f"não foi possível ler {BACKUP_LOG} em {BACKUP_HOST} por SSH"
    ultima = None
    for i, linha in enumerate(linhas):
        m = RESULTADO_BACKUP.match(linha)
        if m:
            ultima = (i, m)
    if ultima is None:
        return True, f"{BACKUP_LOG} sem resultado de rodada nas últimas {len(linhas)} linhas"
    i, m = ultima
    try:
        quando = datetime.fromisoformat(m.group(1))
        if quando.tzinfo is None:  # sem fuso não dá para comparar com a hora atual
            raise ValueError("data sem fuso")
    except ValueError:
        return True, f"linha de resultado com data ilegível: {linhas[i][:120]}"
    horas = (agora - quando).total_seconds() / 3600
    rotulo = f"rodada de {quando:%d/%m %H:%M} UTC"
    codigo = int(m.group(2) if m.group(2) is not None else m.group(3))
    if codigo:
        # Só as falhas desta rodada: o que vem antes do resultado anterior é de outra.
        fim = i
        if m.group(2) is not None and i and (RESULTADO_BACKUP.match(linhas[i - 1]) or [None] * 4)[3]:
            fim = i - 1  # o resumo `bancos copiados` logo acima é desta mesma rodada
        inicio = max((j for j in range(fim) if RESULTADO_BACKUP.match(linhas[j])), default=-1)
        falhas = [linha.split(" ", 1)[1][:160] for linha in linhas[inicio + 1:fim] if " FALHA" in linha]
        o_que = f"saída {codigo}" if m.group(2) is not None else f"{codigo} falha(s)"
        return True, f"{rotulo}: {o_que}" + ("; " + "; ".join(falhas) if falhas else "")
    if horas > BACKUP_MAX_HORAS:
        return True, f"última {rotulo}, há {horas:.0f} h (máx. {BACKUP_MAX_HORAS}): a rotina não rodou"
    return False, f"{rotulo}: sem falha"


def guarda_a_hora(arquivo, agora):
    """Grava a hora em `arquivo` de uma vez (temporário na mesma pasta + troca).

    Gravar direto trunca antes de escrever: com o disco cheio o arquivo ficava vazio e a
    contagem das horas sem leitura recomeçava a cada rodada, em silêncio. Assim, quando
    não dá para gravar, a hora anterior continua lá. Devolve se conseguiu."""
    novo = arquivo.with_name(arquivo.name + ".novo")
    try:
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        novo.write_text(agora.isoformat() + "\n")
        os.replace(novo, arquivo)
        return True
    except OSError:
        try:
            novo.unlink()
        except OSError:
            pass
        return False


def horas_sem_ler_o_backup(leu, agora, arquivo=None, grava=True):
    """(horas desde a última leitura boa do log do backup, guardou a hora?); 0 h se leu agora.

    SSH mudo por uma rodada é oscilação, mas chave revogada, host renomeado ou log
    truncado deixavam o vigia cego sem prazo: a regra das 26 h depende da leitura.
    Sem hora guardada (primeira rodada, arquivo ilegível), a contagem começa agora.
    `guardou` só é falso quando era para gravar a hora e não deu (disco cheio, pasta sem
    escrita): aí a contagem não é confiável, e quem chama diz isso na medida."""
    arquivo = arquivo or BACKUP_ESTADO
    if not leu:
        try:
            ultima = datetime.fromisoformat(arquivo.read_text().strip())
            if ultima.tzinfo is not None:
                return max((agora - ultima).total_seconds() / 3600, 0), True
        except (OSError, ValueError):
            pass
    return 0, guarda_a_hora(arquivo, agora) if grava else True


def execucoes_do_publicador():
    """Últimas execuções do job que publica as rotinas, da mais nova para a mais antiga.

    Lista de (início em UTC, status, commit que o job viu na `main`); None se o gateway
    não respondeu. O commit vem do `summary` (`{"publicado":false,"commit":"…"}`, a saída
    do publica_rotinas.sh) e é None quando a execução não o informou (erro, por exemplo)."""
    try:
        entradas = json.loads(roda(["openclaw", "cron", "runs", ROTINAS_JOB, "--limit", "12",
                                    "--sort", "desc"], timeout=10))["entries"]
        return [(datetime.fromtimestamp(e["runAtMs"] / 1000, timezone.utc), e["status"],
                 commit_do_resumo(e.get("summary")))
                for e in entradas if e.get("status") in ("ok", "error")]
    except (ValueError, KeyError, TypeError, OverflowError, OSError):
        return None


def commit_do_resumo(resumo):
    m = re.search(r'"commit"\s*:\s*"([0-9a-f]{40})"', resumo) if isinstance(resumo, str) else None
    return m.group(1) if m else None


def commit_publicado():
    """Commit para o qual o link `atual` aponta; "" sem link, None se ilegível."""
    try:
        return os.readlink(ROTINAS_PUBLICADO / "atual").rsplit("/", 1)[-1]
    except FileNotFoundError:
        return ""
    except OSError:
        return None


def commit_da_main():
    """Ponta da `main` no GitHub; None se não respondeu."""
    saida = roda(["git", "ls-remote", ROTINAS_ORIGEM, f"refs/heads/{ROTINAS_RAMO}"], timeout=10,
                 env={"GIT_TERMINAL_PROMPT": "0", "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=8"})
    m = re.match(r"^([0-9a-f]{40})\s", saida)
    return m.group(1) if m else None


def rotinas(execucoes, publicado, remoto, agora):
    """(em alerta?, detalhe) da publicação das rotinas. Alerta `None`: não deu para ler.

    Publicador parado não avisava ninguém: os jobs seguiam na versão antiga em silêncio.
    Alerta com o job em erro há mais de ROTINAS_ERRO_MAX_MIN, ou com o link `atual` fora
    da `main` do GitHub. A diferença logo depois de um push é esperada (o job roda a cada
    10 min): só não conta enquanto a última execução deu certo há menos de
    ROTINAS_TOLERANCIA_MIN e viu na `main` um commit diferente do de agora (o push veio
    depois dela). Se ela já viu o commit de agora e o link não foi para ele, o publicador
    saiu com 0 sem publicar: esperar a próxima batida não resolve, alerta na hora. O mesmo
    vale para link cujo alvo não é um commit (40 hexadecimais)."""
    if publicado and not re.fullmatch(r"[0-9a-f]{40}", publicado):
        return True, f"o link atual aponta para {publicado[:60]!r}, que não é um commit"
    if execucoes:
        inicio_do_erro = None
        for quando, status, _ in execucoes:
            if status != "error":
                break
            inicio_do_erro = quando
        if inicio_do_erro is not None:
            minutos = (agora - inicio_do_erro).total_seconds() / 60
            if minutos > ROTINAS_ERRO_MAX_MIN:
                return True, (f"job Publica rotinas com erro desde {inicio_do_erro:%d/%m %H:%M} UTC "
                              f"(há {minutos:.0f} min, máx. {ROTINAS_ERRO_MAX_MIN}); "
                              f"no ar: {(publicado or 'nenhuma versão')[:12]}")
    if publicado is None or remoto is None:
        return None, "não foi possível ler o link atual ou a main do GitHub"
    if publicado == remoto:
        return False, f"no ar: {publicado[:12]}, igual à main"
    detalhe = f"no ar: {(publicado or 'nenhuma versão')[:12]}; main do GitHub: {remoto[:12]}"
    if execucoes is None:
        return None, detalhe + " (sem leitura do job Publica rotinas)"
    if execucoes and execucoes[0][1] == "ok" \
            and (agora - execucoes[0][0]).total_seconds() < ROTINAS_TOLERANCIA_MIN * 60:
        visto = execucoes[0][2]
        if visto is not None and visto != remoto:
            return False, detalhe + " (dentro da espera pela próxima publicação)"
        return True, detalhe + ("; a última execução do job viu esse commit e saiu com sucesso sem publicar"
                                if visto else "; a última execução do job saiu com sucesso sem dizer o commit")
    return True, detalhe


def limpeza(simula):
    """Resultado da limpeza de disco. Falha dela (até de importação) vira `erro`:
    nunca derruba o vigia, que já gravou a medida e os alertas."""
    if LIMPEZA == "0":
        return {"rodou": False, "motivo": "desligada (LIMPEZA_DISCO=0)"}
    try:
        import limpeza_disco
        return limpeza_disco.resumo(limpeza_disco.limpa(simula=simula or LIMPEZA != "1"))
    except Exception as e:
        return {"rodou": False, "erro": f"{type(e).__name__}: {e}"[:300]}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sem-banco", action="store_true",
                    help="só mede e imprime; a limpeza de disco só simula (teste). "
                         "Sem esta opção grava no banco e a limpeza apaga de verdade")
    a = ap.parse_args()

    mem_disp, swap_pct, folga = memoria()
    disco_pct = disco()
    desde = JANELA_MORTES if a.sem_banco else desde_a_ultima_medida()
    gw = gateway(desde)
    cert = dias_do_certificado()
    agora_utc = datetime.now(timezone.utc)
    backup_ruim, backup_detalhe = backup(log_do_backup(), agora_utc)
    horas_cego, guardou = horas_sem_ler_o_backup(backup_ruim is not None, agora_utc, grava=not a.sem_banco)
    if horas_cego > BACKUP_MAX_HORAS:
        backup_ruim = True
        backup_detalhe += f"; sem leitura há {horas_cego:.0f} h (máx. {BACKUP_MAX_HORAS})"
    if not guardou:
        backup_detalhe += (f"; não foi possível guardar a hora da leitura em {BACKUP_ESTADO}"
                           f" (o alerta de {BACKUP_MAX_HORAS} h sem leitura não é confiável)")
    rotinas_ruim, rotinas_detalhe = rotinas(execucoes_do_publicador(), commit_publicado(),
                                            commit_da_main(), datetime.now(timezone.utc))

    # Mortes do gateway não se encerram sozinhas: alguém precisa olhar a causa.
    sem_auto_encerrar = {"vigia:gateway-morto"}
    # Sem leitura (SSH, GitHub ou gateway mudo) não abre nem encerra: uma oscilação de
    # rede não é falha, e também não prova que a falha passou. O backup sem leitura há
    # mais de BACKUP_MAX_HORAS já virou alerta acima.
    sem_leitura = {chave for chave, ruim in (("vigia:backup-applications", backup_ruim),
                                             ("vigia:rotinas-desatualizadas", rotinas_ruim))
                   if ruim is None}
    # chave -> (está em alerta?, pedido da tarefa, detalhe atual)
    checagens = {
        "vigia:memoria": (folga < FOLGA_MIN_MB, "Servidor sem folga de memória (RAM + swap)",
                          f"folga {folga} MB (mín. {FOLGA_MIN_MB}): RAM disponível {mem_disp} MB, swap em {swap_pct}%"),
        "vigia:disco": (disco_pct > DISCO_MAX_PCT, "Disco / acima do limite",
                        f"uso {disco_pct}% (máx. {DISCO_MAX_PCT}%)"),
        "vigia:gateway-parado": (not gw["ativo"], "Gateway do OpenClaw fora do ar",
                                 "systemd não reporta o serviço como active"),
        "vigia:gateway-morto": (gw["mortes"] > 0, "Gateway do OpenClaw foi morto (sinal/OOM)",
                                f"{gw['mortes']} morte(s) desde {desde}; reinícios acumulados {gw['reinicios']}"),
        "vigia:gateway-memoria": ((gw["mem_mb"] or 0) > GATEWAY_MEM_MAX_MB,
                                  "Gateway do OpenClaw perto do limite de memória",
                                  f"RSS + swap do processo: {gw['mem_mb']} MB (máx. {GATEWAY_MEM_MAX_MB})"
                                  if gw["mem_mb"] is not None
                                  else "não foi possível ler a memória do processo (sem MainPID ou /proc)"),
        "vigia:certificado": (cert is None or cert < CERT_MIN_DIAS,
                              f"Certificado de {CERT_HOST} vencendo ou ilegível",
                              f"faltam {cert} dia(s) (mín. {CERT_MIN_DIAS})" if cert is not None
                              else "não foi possível ler o certificado em 443"),
        "vigia:backup-applications": (bool(backup_ruim),
                                      f"Backup dos bancos do {BACKUP_HOST} falhou ou não rodou",
                                      backup_detalhe),
        "vigia:rotinas-desatualizadas": (bool(rotinas_ruim),
                                         "Rotinas publicadas fora da main ou publicador com erro",
                                         rotinas_detalhe),
    }
    alertas = sorted(k for k, (ruim, _, _) in checagens.items() if ruim)
    agora = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")

    novas = 0
    if not a.sem_banco:
        comum.sql(f"""
            insert into saude_medidas (mem_disp_mb, swap_usado_pct, disco_pct, gateway_ativo,
                gateway_mem_mb, gateway_reinicios, gateway_mortes, cert_dias, alertas)
            values ({mem_disp}, {swap_pct}, {disco_pct}, {str(gw['ativo']).lower()},
                {gw['mem_mb'] if gw['mem_mb'] is not None else 'null'}, {gw['reinicios']}, {gw['mortes']},
                {cert if cert is not None else 'null'}, {comum.literal('{' + ','.join(alertas) + '}')}::text[]);
            delete from saude_medidas where medida_em < now() - interval '14 days';
        """)
        for chave, (ruim, pedido, detalhe) in checagens.items():
            if chave in sem_leitura:
                continue
            if ruim:
                novas += comum.registra_tarefa(chave, "ops", pedido, f"{agora}: {detalhe}")
            elif chave not in sem_auto_encerrar:
                comum.encerra_tarefa(chave, f"{agora}: voltou ao normal ({detalhe})")

    print(json.dumps({"mem_disp_mb": mem_disp, "swap_pct": swap_pct, "folga_mb": folga, "disco_pct": disco_pct,
                      "gateway": gw, "cert_dias": cert, "backup": backup_detalhe, "rotinas": rotinas_detalhe,
                      "alertas": alertas,
                      "tarefas_novas": novas, "limpeza": limpeza(a.sem_banco)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
