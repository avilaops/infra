import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'rotinas-openclaw'))
import ciclo_roadmap  # noqa: E402
import limpeza_disco  # noqa: E402
import suite_da_main  # noqa: E402
import varredura_repos  # noqa: E402
import vigia_saude  # noqa: E402

AGORA = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


def roda_o_vigia(**trocas):
    """Roda vigia_saude.main() sem rede nem banco; devolve os mocks de abrir e fechar tarefa."""
    estado = trocas.pop('estado_do_backup', None)  # sem ele, cada rodada usa um arquivo novo
    # A limpeza de disco nunca roda de verdade nos testes do vigia (o `/` real pode estar cheio):
    # quem passa `limpeza=None` troca o `limpeza_disco.limpa` por conta própria.
    # Hora relativa: o main() compara com a hora real, e data fixa vence em 26 h (BACKUP_MAX_HORAS).
    rodada_boa = f"{(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec='seconds')} saida=0\n"
    suite_boa = json.dumps({'quando': (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec='seconds'),
                            'commit': 'a' * 40, 'saida': 0, 'testes': 150, 'falhas': []})
    padrao = {'log_do_backup': rodada_boa, 'execucoes_do_publicador': [],
              'estado_da_suite': suite_boa, 'horas_da_publicacao': 5.0,
              'limpeza': {'rodou': False},
              'commit_publicado': 'a' * 40, 'commit_da_main': 'a' * 40, **trocas}
    if padrao['limpeza'] is None:
        del padrao['limpeza']
    with tempfile.TemporaryDirectory() as pasta, \
            mock.patch.multiple(vigia_saude, desde_a_ultima_medida=mock.Mock(return_value='-70min'),
                                dias_do_certificado=mock.Mock(return_value=60),
                                BACKUP_ESTADO=estado or Path(pasta) / 'backup-ultima-leitura',
                                **{nome: mock.Mock(return_value=valor) for nome, valor in padrao.items()}), \
            mock.patch.object(vigia_saude.comum, 'sql'), \
            mock.patch.object(vigia_saude.comum, 'registra_tarefa', return_value=True) as abre, \
            mock.patch.object(vigia_saude.comum, 'encerra_tarefa') as fecha, \
            mock.patch.object(sys, 'argv', ['vigia_saude.py']), mock.patch('builtins.print'):
        vigia_saude.main()
    return abre, fecha


def pr(numero, **campos):
    base = {'repo': 'loja', 'numero': numero, 'titulo': f'PR {numero}', 'rascunho': False,
            'branch': f'claude/b{numero}', 'mesclavel': 'MERGEABLE', 'revisao': None, 'ci': 'verde',
            'so_docs': False, 'atualizado_em': (AGORA - timedelta(hours=2)).isoformat(),
            'url': f'https://github.com/x/loja/pull/{numero}'}
    return {**base, **campos}


class Varredura(unittest.TestCase):
    def test_ci(self):
        self.assertEqual(varredura_repos.ci_de({'state': 'FAILURE'}), 'vermelho')
        self.assertEqual(varredura_repos.ci_de({'state': 'SUCCESS'}), 'verde')
        self.assertEqual(varredura_repos.ci_de({'state': 'PENDING'}), 'pendente')
        self.assertEqual(varredura_repos.ci_de(None), 'sem_ci')

    def test_so_docs(self):
        self.assertTrue(varredura_repos.so_docs(['AGENTS.md', 'docs/a.md']))
        self.assertFalse(varredura_repos.so_docs(['AGENTS.md', 'src/a.ts']))
        self.assertFalse(varredura_repos.so_docs([]))

    def test_pagina_separa_cada_caso(self):
        estados = [{'repo': 'loja', 'branch_atual': 'main', 'arquivos_sujos': 2, 'ci_main': 'vermelho', 'erro': None}]
        branches = [
            {'repo': 'loja', 'branch': 'claude/velha', 'mesclada': False, 'tem_pr_aberto': False,
             'ultimo_commit_em': (AGORA - timedelta(days=10)).isoformat()},
            {'repo': 'loja', 'branch': 'claude/mesclada', 'mesclada': True, 'tem_pr_aberto': False,
             'ultimo_commit_em': (AGORA - timedelta(days=10)).isoformat()},
        ]
        prs = [pr(1, ci='vermelho'), pr(2, mesclavel='CONFLICTING'), pr(3),
               pr(4, rascunho=True), pr(5, atualizado_em=(AGORA - timedelta(days=5)).isoformat())]
        fila = [{'id': 9, 'raia': 'ops', 'repo': None, 'estado': 'aberta', 'pedido': 'decidir X', 'decisao': 'feito Y porque Z'}]
        texto, contas = varredura_repos.pagina(estados, branches, prs, fila, [], AGORA)

        self.assertEqual(contas, {'ci_vermelho': 2, 'conflitos': 1, 'parados': 2, 'erros': 0})
        secoes = texto.split('\n## ')
        self.assertIn('decidir X', secoes[1])
        self.assertIn('#1]', secoes[2])
        self.assertIn('#2]', secoes[2])
        self.assertNotIn('#3]', secoes[2])
        self.assertIn('claude/velha', secoes[3])
        self.assertNotIn('claude/mesclada', secoes[3])
        self.assertIn('#5]', secoes[3])
        prontos = secoes[4]
        self.assertIn('#3]', prontos)
        for fora in ('#1]', '#2]', '#4]'):  # vermelho, conflito e rascunho não estão prontos
            self.assertNotIn(fora, prontos)
        self.assertIn('2 arquivo(s) sem commit', secoes[5])


class CicloRoadmap(unittest.TestCase):
    ROADMAP = '# R\n- [x] feito\n- [ ] Login,  usuários\n  - detalhe\n* [ ] **Backup**\n'

    def git_falso(self, repo, *args):
        if args[0] == 'symbolic-ref':
            return 'origin/main\n'
        if args[0] == 'ls-tree':
            return 'README.md\ndocs/roadmap.md\nsrc/deep/dir/ROADMAP.md\n'
        if args[0] == 'show':
            return self.ROADMAP
        return ''

    def test_itens_abertos(self):
        with mock.patch.object(ciclo_roadmap, 'git', self.git_falso):
            arquivo, itens = ciclo_roadmap.itens_abertos('x')
        self.assertEqual(arquivo, 'docs/roadmap.md')
        self.assertEqual([t for _, t in itens], ['Login, usuários', '**Backup**'])
        self.assertTrue(all(len(i) == 6 for i, _ in itens))

    def test_situacoes(self):
        with mock.patch.object(ciclo_roadmap.Path, 'read_text',
                               return_value='| Repo | Situação |\n|---|---|\n| `a` | ativo | x |\n| `b` | A confirmar | y |\n'):
            self.assertEqual(ciclo_roadmap.situacoes(), {'a': 'ativo', 'b': 'a confirmar'})

    def test_pedido_spec_traz_os_ids(self):
        texto = ciclo_roadmap.pedido_spec('x', 'ROADMAP.md', [('abc123', 'Login')])
        self.assertIn('[abc123] Login', texto)
        self.assertIn('item=<id>; pulados=', texto)


class VigiaGateway(unittest.TestCase):
    def proc(self, pid, status):
        raiz = Path(tempfile.mkdtemp())
        self.addCleanup(vigia_saude.shutil.rmtree, raiz)
        (raiz / str(pid)).mkdir()
        (raiz / str(pid) / 'status').write_text(status)
        return raiz

    def test_soma_rss_e_swap_do_processo(self):
        raiz = self.proc(42, 'Name:\tnode\nVmRSS:\t  819200 kB\nRssAnon:\t 700000 kB\nVmSwap:\t 1536000 kB\n')
        self.assertEqual(vigia_saude.memoria_do_processo('42', raiz), (819200 + 1536000) // 1024)

    def test_sem_vmswap_conta_so_o_rss(self):
        raiz = self.proc(42, 'Name:\tnode\nVmRSS:\t 2048 kB\n')
        self.assertEqual(vigia_saude.memoria_do_processo(42, raiz), 2)

    def test_sem_processo_nao_estoura(self):
        raiz = self.proc(42, 'Name:\tkthreadd\n')  # thread de kernel: sem VmRSS
        for pid in ('0', 0, '', None, 'abc', '-1', '7', '42'):
            self.assertIsNone(vigia_saude.memoria_do_processo(pid, raiz), pid)

    def test_proc_ilegivel_nao_estoura(self):
        with mock.patch.object(vigia_saude.Path, 'read_text', side_effect=PermissionError):
            self.assertIsNone(vigia_saude.memoria_do_processo('42'))

    def gateway(self, show):
        with mock.patch.object(vigia_saude, 'roda', side_effect=[show, '']):
            return vigia_saude.gateway('-70min')

    def test_gateway_parado_fica_sem_medida(self):
        gw = self.gateway('ActiveState=inactive\nNRestarts=3\nMainPID=0\n')
        self.assertEqual(gw, {'ativo': False, 'mem_mb': None, 'reinicios': 3, 'mortes': 0})

    def test_systemctl_mudo_nao_estoura(self):
        self.assertEqual(self.gateway(''), {'ativo': False, 'mem_mb': None, 'reinicios': 0, 'mortes': 0})

    def test_gateway_le_o_mainpid(self):
        with mock.patch.object(vigia_saude, 'memoria_do_processo', return_value=2300) as mem:
            gw = self.gateway('ActiveState=active\nNRestarts=0\nMainPID=27618\n')
        mem.assert_called_once_with('27618')
        self.assertEqual(gw['mem_mb'], 2300)
        self.assertTrue(gw['ativo'])


class VigiaBackup(unittest.TestCase):
    AGORA = datetime(2026, 10, 7, 4, 0, tzinfo=timezone.utc)
    BOA = ('2026-10-07T03:30:28+00:00 bancos copiados: 10  falhas: 0  ignorados: 1  total: 705M\n'
           '2026-10-07T03:30:28+00:00 saida=0\n')

    def test_rodada_boa_nao_alerta(self):
        self.assertEqual(vigia_saude.backup(self.BOA, self.AGORA), (False, 'rodada de 07/10 03:30 UTC: sem falha'))

    def test_saida_diferente_de_zero_alerta_e_diz_o_banco(self):
        log = (self.BOA.replace('2026-10-07', '2026-10-06')
               + '2026-10-07T03:30:02+00:00 FALHA ct-plataforma: container minas-espetinhos-db-1 nao existe\n'
               + '2026-10-07T03:30:28+00:00 bancos copiados: 9  falhas: 1  ignorados: 1  total: 705M\n'
               + '2026-10-07T03:30:28+00:00 saida=1\n')
        ruim, detalhe = vigia_saude.backup(log, self.AGORA)
        self.assertTrue(ruim)
        self.assertIn('saída 1', detalhe)
        self.assertIn('FALHA ct-plataforma: container minas-espetinhos-db-1 nao existe', detalhe)

    def test_falha_da_rodada_anterior_nao_entra_na_atual(self):
        log = ('2026-10-06T03:30:02+00:00 FALHA ct-velho: container x nao existe\n'
               '2026-10-06T03:30:28+00:00 bancos copiados: 9  falhas: 1  ignorados: 1  total: 705M\n'
               '2026-10-06T03:30:28+00:00 saida=1\n' + self.BOA)
        self.assertEqual(vigia_saude.backup(log, self.AGORA)[0], False)

    def test_script_que_morre_sem_resumo_alerta(self):
        ruim, detalhe = vigia_saude.backup(self.BOA.replace('2026-10-07', '2026-10-06')
                                           + '2026-10-07T03:30:01+00:00 saida=127\n', self.AGORA)
        self.assertEqual((ruim, detalhe), (True, 'rodada de 07/10 03:30 UTC: saída 127'))

    def test_log_antigo_sem_linha_de_saida_usa_o_resumo(self):
        log = '2026-10-06T03:30:28+00:00 bancos copiados: 7  falhas: 3  total: 740M\n'
        self.assertEqual(vigia_saude.backup(log, datetime(2026, 10, 6, 4, 0, tzinfo=timezone.utc)),
                         (True, 'rodada de 06/10 03:30 UTC: 3 falha(s)'))

    def test_rotina_que_nao_rodou_alerta(self):
        ruim, detalhe = vigia_saude.backup(self.BOA, self.AGORA + timedelta(hours=30))
        self.assertTrue(ruim)
        self.assertIn('a rotina não rodou', detalhe)

    def test_log_sem_resultado_alerta(self):
        self.assertTrue(vigia_saude.backup('linha qualquer\n', self.AGORA)[0])

    def test_ssh_mudo_nao_abre_nem_encerra_tarefa(self):
        self.assertIsNone(vigia_saude.backup('', self.AGORA)[0])
        abre, fecha = roda_o_vigia(log_do_backup='')
        chaves = [c.args[0] for c in abre.call_args_list + fecha.call_args_list]
        self.assertNotIn('vigia:backup-applications', chaves)

    def test_data_sem_fuso_e_ilegivel_e_nao_derruba_o_vigia(self):
        log = '2026-10-07T03:30:28 saida=0\n'
        ruim, detalhe = vigia_saude.backup(log, self.AGORA)
        self.assertTrue(ruim)
        self.assertIn('data ilegível', detalhe)
        abre, _ = roda_o_vigia(log_do_backup=log)
        self.assertIn('vigia:backup-applications', [c.args[0] for c in abre.call_args_list])

    def test_leitura_boa_guarda_a_hora_e_zera_a_contagem(self):
        with tempfile.TemporaryDirectory() as pasta:
            estado = Path(pasta) / 'sub' / 'ultima'
            self.assertEqual(vigia_saude.horas_sem_ler_o_backup(True, self.AGORA, estado)[0], 0)
            self.assertEqual(estado.read_text().strip(), self.AGORA.isoformat())
            depois = self.AGORA + timedelta(hours=27)
            self.assertEqual(vigia_saude.horas_sem_ler_o_backup(False, depois, estado)[0], 27)
            self.assertEqual(estado.read_text().strip(), self.AGORA.isoformat())  # falha não mexe na hora
            self.assertEqual(vigia_saude.horas_sem_ler_o_backup(True, depois, estado)[0], 0)
            self.assertEqual(estado.read_text().strip(), depois.isoformat())

    def test_sem_hora_guardada_a_contagem_comeca_agora(self):
        with tempfile.TemporaryDirectory() as pasta:
            estado = Path(pasta) / 'ultima'
            for conteudo in (None, 'lixo\n', '2026-10-07T03:30:28\n'):
                if conteudo is not None:
                    estado.write_text(conteudo)
                self.assertEqual(vigia_saude.horas_sem_ler_o_backup(False, self.AGORA, estado)[0], 0)
                self.assertEqual(estado.read_text().strip(), self.AGORA.isoformat())
            estado.unlink()
            self.assertEqual(vigia_saude.horas_sem_ler_o_backup(False, self.AGORA, estado, grava=False)[0], 0)
            self.assertFalse(estado.exists())  # --sem-banco não grava
            # pasta sem escrita não derruba o vigia, e ele diz que não guardou
            self.assertEqual(vigia_saude.horas_sem_ler_o_backup(True, self.AGORA, Path('/proc/nao/existe')),
                             (0, False))

    def test_hora_e_trocada_de_uma_vez_e_falha_nao_apaga_a_anterior(self):
        with tempfile.TemporaryDirectory() as pasta:
            estado = Path(pasta) / 'ultima'
            self.assertEqual(vigia_saude.horas_sem_ler_o_backup(True, self.AGORA, estado), (0, True))
            self.assertEqual([p.name for p in Path(pasta).iterdir()], ['ultima'])  # sem sobra do temporário
            depois = self.AGORA + timedelta(hours=30)
            # disco cheio na hora de gravar: a hora anterior fica, nada de arquivo vazio
            with mock.patch.object(Path, 'write_text', side_effect=OSError(28, 'No space left on device')):
                self.assertEqual(vigia_saude.horas_sem_ler_o_backup(True, depois, estado), (0, False))
            with mock.patch.object(vigia_saude.os, 'replace', side_effect=OSError(28, 'No space left on device')):
                self.assertEqual(vigia_saude.horas_sem_ler_o_backup(True, depois, estado), (0, False))
            self.assertEqual(estado.read_text().strip(), self.AGORA.isoformat())
            self.assertEqual([p.name for p in Path(pasta).iterdir()], ['ultima'])
            # a contagem segue da última hora guardada, em vez de recomeçar a cada rodada
            self.assertEqual(vigia_saude.horas_sem_ler_o_backup(False, depois, estado), (30, True))

    def test_hora_que_nao_da_para_guardar_aparece_na_medida(self):
        _, fecha = roda_o_vigia(estado_do_backup=Path('/proc/nao/existe/ultima'))
        detalhe = {c.args[0]: c.args[1] for c in fecha.call_args_list}['vigia:backup-applications']
        self.assertIn('não foi possível guardar a hora da leitura em /proc/nao/existe/ultima', detalhe)
        _, fecha = roda_o_vigia()
        detalhe = {c.args[0]: c.args[1] for c in fecha.call_args_list}['vigia:backup-applications']
        self.assertNotIn('guardar a hora', detalhe)

    def test_ssh_mudo_ha_mais_de_26_h_abre_tarefa(self):
        with tempfile.TemporaryDirectory() as pasta:
            estado = Path(pasta) / 'ultima'
            for horas, abre_tarefa in ((25, False), (27, True)):
                estado.write_text((datetime.now(timezone.utc) - timedelta(hours=horas)).isoformat())
                abre, fecha = roda_o_vigia(log_do_backup='', estado_do_backup=estado)
                abertas = {c.args[0]: c.args for c in abre.call_args_list}
                self.assertEqual('vigia:backup-applications' in abertas, abre_tarefa, horas)
                self.assertNotIn('vigia:backup-applications', [c.args[0] for c in fecha.call_args_list])
            self.assertIn('por SSH; sem leitura há 27 h (máx. 26)', abertas['vigia:backup-applications'][3])
            # o SSH voltou com rodada boa: encerra e a contagem zera
            abre, fecha = roda_o_vigia(estado_do_backup=estado)
            self.assertNotIn('vigia:backup-applications', [c.args[0] for c in abre.call_args_list])
            self.assertIn('vigia:backup-applications', [c.args[0] for c in fecha.call_args_list])
            self.assertLess(vigia_saude.horas_sem_ler_o_backup(False, datetime.now(timezone.utc), estado)[0], 0.1)

    def test_falha_abre_tarefa_para_ops(self):
        abre, _ = roda_o_vigia(log_do_backup='2026-10-07T03:30:28+00:00 saida=1\n')
        abertas = {c.args[0]: c.args for c in abre.call_args_list}
        self.assertIn('vigia:backup-applications', abertas)
        self.assertEqual(abertas['vigia:backup-applications'][1], 'ops')
        self.assertIn('saída 1', abertas['vigia:backup-applications'][3])


class VigiaRotinas(unittest.TestCase):
    """Alerta `vigia:rotinas-desatualizadas`: publicador em erro ou link fora da main."""
    AGORA = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
    NO_AR, NOVO = 'a' * 40, 'b' * 40

    def execucoes(self, *status, passo=10, ha=3, visto=NO_AR):
        """Execuções da mais nova (há `ha` min) para a mais antiga, uma a cada `passo` min.

        `visto` é o commit que cada execução `ok` viu na main (as com erro não informam)."""
        return [(self.AGORA - timedelta(minutes=ha + n * passo), s, visto if s == 'ok' else None)
                for n, s in enumerate(status)]

    def rotinas(self, execucoes, publicado=NO_AR, remoto=NO_AR):
        return vigia_saude.rotinas(execucoes, publicado, remoto, self.AGORA)

    def test_em_dia_nao_alerta(self):
        self.assertEqual(self.rotinas(self.execucoes('ok', 'ok')), (False, 'no ar: aaaaaaaaaaaa, igual à main'))

    def test_erro_ha_mais_de_uma_hora_alerta(self):
        ruim, detalhe = self.rotinas(self.execucoes(*['error'] * 7, 'ok'))
        self.assertTrue(ruim)
        self.assertIn('com erro desde 07/10 10:57 UTC', detalhe)

    def test_erro_recente_nao_alerta(self):
        self.assertFalse(self.rotinas(self.execucoes(*['error'] * 5, 'ok'))[0])

    def test_erro_antigo_que_ja_passou_nao_alerta(self):
        self.assertFalse(self.rotinas(self.execucoes('ok', *['error'] * 9))[0])

    def test_link_fora_da_main_alerta(self):
        ruim, detalhe = self.rotinas(self.execucoes('ok', ha=45), remoto=self.NOVO)  # job parou de rodar
        self.assertEqual((ruim, detalhe), (True, 'no ar: aaaaaaaaaaaa; main do GitHub: bbbbbbbbbbbb'))
        self.assertTrue(self.rotinas(self.execucoes('error', 'ok'), remoto=self.NOVO)[0])
        self.assertTrue(self.rotinas([], remoto=self.NOVO)[0])
        self.assertTrue(self.rotinas(self.execucoes('ok', ha=45), publicado='', remoto=self.NOVO)[0])

    def test_push_recem_feito_espera_a_proxima_publicacao(self):
        # a última execução viu o commit antigo: o push veio depois dela
        ruim, detalhe = self.rotinas(self.execucoes('ok', 'ok'), remoto=self.NOVO)
        self.assertFalse(ruim)
        self.assertIn('dentro da espera', detalhe)
        self.assertFalse(self.rotinas(self.execucoes('ok'), publicado='', remoto=self.NOVO)[0])

    def test_publicador_que_sai_com_0_sem_mover_o_link_alerta_na_hora(self):
        # 12 execuções `ok` seguidas, todas dizendo que viram a main de agora, e o link parado:
        # lido do JSON do gateway, como o vigia lê
        entradas = [{'runAtMs': int((self.AGORA - timedelta(minutes=3 + 10 * n)).timestamp() * 1000),
                     'status': 'ok', 'summary': '{"publicado":false,"commit":"%s"}' % self.NOVO}
                    for n in range(12)]
        with mock.patch.object(vigia_saude, 'roda', return_value=json.dumps({'entries': entradas})):
            execucoes = vigia_saude.execucoes_do_publicador()
        ruim, detalhe = vigia_saude.rotinas(execucoes, self.NO_AR, self.NOVO, self.AGORA)
        self.assertTrue(ruim, detalhe)
        self.assertIn('no ar: aaaaaaaaaaaa; main do GitHub: bbbbbbbbbbbb', detalhe)
        self.assertIn('saiu com sucesso sem publicar', detalhe)
        # sem link nenhum e o job dizendo que está tudo publicado: também
        self.assertTrue(vigia_saude.rotinas(execucoes, '', self.NOVO, self.AGORA)[0])
        # execução `ok` que não diz o commit não prova que o push é posterior a ela
        ruim, detalhe = self.rotinas(self.execucoes('ok', 'ok', visto=None), remoto=self.NOVO)
        self.assertTrue(ruim)
        self.assertIn('sem dizer o commit', detalhe)

    def test_link_com_alvo_que_nao_e_commit_alerta_na_hora(self):
        for alvo in ('teste', 'a' * 39, 'A' * 40, 'a' * 41, '.parcial-x1Y2z3'):
            for remoto in (self.NOVO, None):  # não depende do GitHub
                ruim, detalhe = self.rotinas(self.execucoes('ok', 'ok'), publicado=alvo, remoto=remoto)
                self.assertTrue(ruim, alvo)
                self.assertIn('não é um commit', detalhe)
        with tempfile.TemporaryDirectory() as base, mock.patch.object(vigia_saude, 'ROTINAS_PUBLICADO', Path(base)):
            os.symlink('releases/teste', Path(base) / 'atual')
            agora = datetime.now(timezone.utc)
            abre, _ = roda_o_vigia(commit_publicado=vigia_saude.commit_publicado(), commit_da_main=self.NOVO,
                                   execucoes_do_publicador=[(agora - timedelta(minutes=3), 'ok', self.NO_AR)])
        self.assertIn("'teste', que não é um commit", self.chaves(abre)['vigia:rotinas-desatualizadas'][3])

    def test_leituras_das_rotinas_tem_limite_de_10_s(self):
        # a soma dos limites do vigia não pode passar muito do limite do job (90 s)
        with mock.patch.object(vigia_saude, 'roda', return_value='') as roda:
            vigia_saude.execucoes_do_publicador()
            vigia_saude.commit_da_main()
        self.assertEqual([c.kwargs['timeout'] for c in roda.call_args_list], [10, 10])

    def test_leituras_do_gateway_tem_limite_de_5_e_15_s(self):
        # systemctl show 5 + journalctl 15 + certificado 8 + ssh 25 + rotinas 10 + 10 = 73 s (job: 90 s)
        with mock.patch.object(vigia_saude, 'roda', return_value='') as roda:
            vigia_saude.gateway('1 hour ago')
        self.assertEqual([c.kwargs['timeout'] for c in roda.call_args_list], [5, 15])

    def test_sem_leitura_nao_decide(self):
        self.assertIsNone(self.rotinas(self.execucoes('ok'), remoto=None)[0])
        self.assertIsNone(self.rotinas(self.execucoes('ok'), publicado=None)[0])
        self.assertIsNone(self.rotinas(None, remoto=self.NOVO)[0])
        self.assertFalse(self.rotinas(None)[0])  # link igual à main dispensa o job
        self.assertTrue(self.rotinas(self.execucoes(*['error'] * 8), remoto=None)[0])  # erro basta

    def test_le_as_execucoes_do_gateway(self):
        resumo = json.dumps('{"publicado":true,"commit":"%s","anterior":"%s","removidos":0}' % (self.NOVO, self.NO_AR))
        saida = ('{"entries": [{"runAtMs": 1791366420020, "status": "error", "summary": "ERRO: sem rede"},'
                 ' {"runAtMs": 1791365820000, "status": "skipped"},'
                 ' {"runAtMs": 1791365220000, "status": "ok", "summary": %s},'
                 ' {"runAtMs": 1791364620000, "status": "ok"},'
                 ' {"runAtMs": 1791364020000, "status": "ok", "summary": {"commit": 1}}]}' % resumo)
        with mock.patch.object(vigia_saude, 'roda', return_value=saida) as roda:
            execucoes = vigia_saude.execucoes_do_publicador()
        self.assertIn(vigia_saude.ROTINAS_JOB, roda.call_args.args[0])
        self.assertEqual([(s, c) for _, s, c in execucoes],
                         [('error', None), ('ok', self.NOVO), ('ok', None), ('ok', None)])
        self.assertEqual(execucoes[0][0], datetime.fromtimestamp(1791366420.02, timezone.utc))
        for muda in ('', 'erro: gateway fora do ar', '{"entries": [{"status": "ok"}]}', '[]'):
            with mock.patch.object(vigia_saude, 'roda', return_value=muda):
                self.assertIsNone(vigia_saude.execucoes_do_publicador(), muda)

    def test_le_o_link_e_a_main(self):
        with tempfile.TemporaryDirectory() as base, mock.patch.object(vigia_saude, 'ROTINAS_PUBLICADO', Path(base)):
            self.assertEqual(vigia_saude.commit_publicado(), '')
            os.symlink(f'releases/{self.NO_AR}', Path(base) / 'atual')
            self.assertEqual(vigia_saude.commit_publicado(), self.NO_AR)
        with mock.patch.object(vigia_saude, 'roda', return_value=f'{self.NOVO}\trefs/heads/main\n'):
            self.assertEqual(vigia_saude.commit_da_main(), self.NOVO)
        with mock.patch.object(vigia_saude, 'roda', return_value=''):
            self.assertIsNone(vigia_saude.commit_da_main())

    def chaves(self, chamadas):
        return {c.args[0]: c.args for c in chamadas.call_args_list}

    def test_abre_tarefa_para_ops_e_fecha_quando_normaliza(self):
        parado = [(datetime.now(timezone.utc) - timedelta(minutes=45), 'ok', self.NO_AR)]  # main() usa a hora real
        abre, fecha = roda_o_vigia(execucoes_do_publicador=parado, commit_da_main=self.NOVO)
        aberta = self.chaves(abre)['vigia:rotinas-desatualizadas']
        self.assertEqual(aberta[1], 'ops')
        self.assertIn('main do GitHub: bbbbbbbbbbbb', aberta[3])
        self.assertNotIn('vigia:rotinas-desatualizadas', self.chaves(fecha))

        abre, fecha = roda_o_vigia(execucoes_do_publicador=parado)
        self.assertNotIn('vigia:rotinas-desatualizadas', self.chaves(abre))
        self.assertIn('igual à main', self.chaves(fecha)['vigia:rotinas-desatualizadas'][1])

    def test_sem_leitura_nao_abre_nem_encerra_tarefa(self):
        abre, fecha = roda_o_vigia(execucoes_do_publicador=None, commit_da_main=None)
        self.assertNotIn('vigia:rotinas-desatualizadas', {**self.chaves(abre), **self.chaves(fecha)})


class VigiaSuite(unittest.TestCase):
    """Alerta `vigia:suite-main`: a suíte da main publicada falhou ou parou de ser conferida."""
    AGORA = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)

    def estado(self, ha=2, **campos):
        return json.dumps({'quando': (self.AGORA - timedelta(hours=ha)).isoformat(timespec='seconds'),
                           'commit': 'a' * 40, 'saida': 0, 'testes': 150, 'falhas': [], **campos})

    def suite(self, texto, horas_publicado=5.0):
        return vigia_saude.suite(texto, horas_publicado, self.AGORA)

    def chaves(self, chamadas):
        return {c.args[0]: c.args for c in chamadas.call_args_list}

    def test_suite_que_passou_nao_alerta(self):
        self.assertEqual(self.suite(self.estado()),
                         (False, 'suíte da main aaaaaaaaaaaa, conferida em 09/10 10:00 UTC: 150 testes, sem falha'))

    def test_suite_que_falhou_alerta_e_diz_o_teste(self):
        ruim, detalhe = self.suite(self.estado(saida=1, falhas=['FAIL: test_x (tests.T.test_x)']))
        self.assertTrue(ruim)
        self.assertIn('falhou (saída 1); FAIL: test_x', detalhe)

    def test_suite_interrompida_pelo_tempo_alerta(self):
        ruim, detalhe = self.suite(self.estado(saida=124, testes=None))
        self.assertTrue(ruim)
        self.assertIn('limite de tempo', detalhe)

    def test_conferencia_parada_ha_mais_de_30_h_alerta(self):
        self.assertFalse(self.suite(self.estado(ha=29))[0])
        ruim, detalhe = self.suite(self.estado(ha=31))
        self.assertTrue(ruim)
        self.assertIn('há 31 h (máx. 30): a conferência diária não rodou', detalhe)

    def test_sem_estado_espera_a_primeira_conferencia_e_depois_alerta(self):
        self.assertIsNone(self.suite('', horas_publicado=0.2)[0])
        self.assertIsNone(self.suite('', horas_publicado=None)[0])
        ruim, detalhe = self.suite('', horas_publicado=3)
        self.assertTrue(ruim)
        self.assertIn('nunca foi conferida', detalhe)

    def test_estado_ilegivel_alerta_e_sem_leitura_nao_decide(self):
        for texto in ('{pela metade', '[]', json.dumps({'quando': '2026-10-09T10:00:00', 'saida': 0}),
                      json.dumps({'quando': '2026-10-09T10:00:00+00:00', 'saida': 'x'})):
            ruim, detalhe = self.suite(texto)
            self.assertTrue(ruim, texto)
            self.assertIn('ilegível', detalhe)
        self.assertIsNone(self.suite(None)[0])

    def test_falhas_que_nao_e_lista_e_estado_ilegivel(self):
        for campos in ({'saida': 1, 'falhas': 5}, {'saida': 0, 'falhas': 5}, {'saida': 1, 'falhas': 'FAIL: x'},
                       {'saida': 1, 'falhas': {'a': 1}}):
            ruim, detalhe = self.suite(self.estado(**campos))
            self.assertTrue(ruim, campos)
            self.assertIn('ilegível', detalhe)
        ruim, detalhe = self.suite(self.estado(saida=1, falhas=None))  # sem a lista: falhou, sem os nomes
        self.assertTrue(ruim)
        self.assertIn('falhou (saída 1)', detalhe)
        self.assertEqual(self.suite(self.estado(saida=1, falhas=[5, None]))[1].split('; ')[1:], ['5', 'None'])

    def test_falhas_que_nao_e_lista_nao_derruba_o_vigia_e_abre_tarefa(self):
        agora = datetime.now(timezone.utc).isoformat(timespec='seconds')  # main() usa a hora real
        abre, fecha = roda_o_vigia(estado_da_suite=json.dumps({'quando': agora, 'saida': 1, 'falhas': 5}))
        self.assertIn('ilegível', self.chaves(abre)['vigia:suite-main'][3])
        self.assertNotIn('vigia:suite-main', self.chaves(fecha))

    def test_arquivo_que_nao_e_utf8_e_estado_ilegivel_e_alerta(self):
        with tempfile.TemporaryDirectory() as base, \
                mock.patch.object(vigia_saude, 'SUITE_ESTADO', Path(base) / 'suite-main'):
            (Path(base) / 'suite-main').write_bytes(b'\xff\xfe{{')
            texto = vigia_saude.estado_da_suite()
            self.assertIsNotNone(texto)  # None seria "sem leitura": nunca abriria tarefa
            ruim, detalhe = self.suite(texto)
            self.assertTrue(ruim)
            self.assertIn('ilegível', detalhe)
            abre, fecha = roda_o_vigia(estado_da_suite=texto)
            self.assertIn('vigia:suite-main', self.chaves(abre))

    def test_le_o_arquivo_de_estado_e_a_idade_do_link(self):
        with tempfile.TemporaryDirectory() as base, \
                mock.patch.object(vigia_saude, 'SUITE_ESTADO', Path(base) / 'suite-main'), \
                mock.patch.object(vigia_saude, 'ROTINAS_PUBLICADO', Path(base)):
            self.assertEqual(vigia_saude.estado_da_suite(), '')
            self.assertIsNone(vigia_saude.horas_da_publicacao())
            (Path(base) / 'suite-main').write_text('x\n')
            os.symlink('releases/' + 'a' * 40, Path(base) / 'atual')
            self.assertEqual(vigia_saude.estado_da_suite(), 'x\n')
            self.assertLess(vigia_saude.horas_da_publicacao(), 0.1)
            (Path(base) / 'suite-main').unlink()
            (Path(base) / 'suite-main').mkdir()
            self.assertIsNone(vigia_saude.estado_da_suite())

    def test_abre_tarefa_para_ops_e_fecha_quando_normaliza(self):
        agora = datetime.now(timezone.utc).isoformat(timespec='seconds')  # main() usa a hora real
        falhou = json.dumps({'quando': agora, 'commit': 'a' * 40, 'saida': 1, 'testes': 150,
                             'falhas': ['FAIL: test_x (tests.T.test_x)']})
        abre, fecha = roda_o_vigia(estado_da_suite=falhou)
        aberta = self.chaves(abre)['vigia:suite-main']
        self.assertEqual(aberta[1], 'ops')
        self.assertIn('FAIL: test_x', aberta[3])
        self.assertNotIn('vigia:suite-main', self.chaves(fecha))

        abre, fecha = roda_o_vigia()
        self.assertNotIn('vigia:suite-main', self.chaves(abre))
        self.assertIn('sem falha', self.chaves(fecha)['vigia:suite-main'][1])

    def test_sem_leitura_nao_abre_nem_encerra_tarefa(self):
        for texto, horas in ((None, 5.0), ('', 0.1)):
            abre, fecha = roda_o_vigia(estado_da_suite=texto, horas_da_publicacao=horas)
            self.assertNotIn('vigia:suite-main', {**self.chaves(abre), **self.chaves(fecha)})


class AmbienteDaLimpeza:
    """Tudo falso: /tmp, /proc, estado, caches, repositórios, medida do disco e comandos
    (npm, du, sudo, docker, git, psql). Nenhum teste da limpeza encosta no servidor."""
    HORAS = 3600

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        raiz = Path(self.tmp.name)
        self.base, self.proc, self.estado, self.apt = raiz / 'tmp', raiz / 'proc', raiz / 'estado', raiz / 'apt'
        for pasta in (self.base, self.proc, self.apt):
            pasta.mkdir()
        (self.apt / 'a.deb').write_bytes(b'x' * 10)
        self.livre = [20 * limpeza_disco.MB]
        self.comandos = []
        self.codigos = {}  # primeiro argumento -> código de saída
        self.depois = limpeza_disco.time.time() + 7 * self.HORAS  # tudo o que o teste cria já tem 7 h
        self.tarde = self.depois + 48 * self.HORAS  # hora das etapas com prazo de 12 h ou 24 h
        self.projetos, self.npx, self.logs = raiz / 'projetos', raiz / 'npx', raiz / 'claude-logs'
        self.cache_node, self.store = self.base / 'node-compile-cache', raiz / 'pnpm-store'
        self.imagens, self.conteineres = [], {}  # docker: imagens (dict) e contêiner -> id da imagem
        self.repos = {}  # git: caminho do repositório -> respostas
        self.quadro = ''  # psql: "repo pedido" das tarefas em_andamento
        self.falha, self.indevidos = set(), []  # comandos que falham; comandos que a limpeza não pode mandar
        self.addCleanup(lambda: self.assertEqual(self.indevidos, []))
        troca = mock.patch.multiple(
            limpeza_disco, TMP=self.base, RAIZES=(str(raiz),), PROC=self.proc, ESTADO=self.estado, APT_CACHE=self.apt,
            NPM_CACHE=raiz / 'npm', espaco=lambda: (100 * limpeza_disco.MB, 80 * limpeza_disco.MB, self.livre[0]),
            comando=self.comando, PROJETOS=self.projetos, NPX=self.npx, CLAUDE_LOGS=self.logs,
            COMPILE_CACHE=self.cache_node, PNPM_STORE=self.store,
            PROIBIDOS=tuple(str(raiz / p) for p in ('backups', 'docker', 'postgresql', '.openclaw', '.agents')))
        for p in (troca, mock.patch.object(limpeza_disco.shutil, 'which', return_value='/usr/bin/npm'),
                  mock.patch.object(limpeza_disco.time, 'time', lambda: self.depois)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def comando(self, args, timeout):
        self.comandos.append(' '.join(args))
        if args[0] in self.falha or ' '.join(args[:2]) in self.falha:
            return 1, 'falhou'
        if args[0] == 'docker':
            return self.docker(args[1:])
        if args[0] == 'git':
            return self.git(args[3], args[4:])
        if args[0] == 'psql':
            return 0, self.quadro
        if args[:3] == ['npm', 'cache', 'clean']:
            self.livre[0] += 5 * limpeza_disco.MB
        return self.codigos.get(args[0], 0), '7\t/x'

    def imagem(self, nome, horas=30, conteiner=False, mb=100):
        """Imagem no docker de mentira, criada `horas` antes de `self.depois`."""
        ident = 'sha256:' + f'{len(self.imagens) + 1:064x}'
        self.imagens.append({'id': ident, 'nome': nome, 'criada': self.depois - horas * self.HORAS, 'mb': mb})
        if conteiner:
            self.conteineres[f'{len(self.conteineres) + 1:064x}'] = ident
        return ident

    def docker(self, args):
        if args[0] == 'images':
            return 0, '\n'.join('\t'.join([i['id'], *i['nome'].rsplit(':', 1)]) for i in self.imagens)
        if args[:2] == ['ps', '-aq']:
            return 0, '\n'.join(self.conteineres)
        if args[0] == 'inspect':
            return 0, '\n'.join(self.conteineres[c] for c in args[3:])
        if args[:2] == ['image', 'inspect']:
            formato = '%Y-%m-%dT%H:%M:%S.123456789Z'
            return 0, '\n'.join(
                f"{i['id']}\t{datetime.fromtimestamp(i['criada'], timezone.utc).strftime(formato)}"
                f"\t{i['mb'] * limpeza_disco.MB}" for i in self.imagens if i['id'] in args[4:])
        if args[0] == 'rmi' and len(args) == 2:
            achada = next((i for i in self.imagens if i['nome'] == args[1]), None)
            if achada is None or achada['id'] in self.conteineres.values():
                return 1, 'conflict: unable to remove'
            self.imagens.remove(achada)
            return 0, 'Untagged'
        self.indevidos.append('docker ' + ' '.join(args))
        return 1, 'comando inesperado'

    def git(self, repo, args):
        r = self.repos.get(repo)
        if r is None:
            return 128, 'fatal: not a git repository'
        if args[0] == 'status':
            return 0, r['status']
        if args[0] == 'ls-files':
            return 0, r['versionado']
        if args[0] == 'check-ignore':
            return (0 if r['ignorado'] else 1), ''
        if args[0] == 'log':
            return 0, str(int(r['commit']))
        self.indevidos.append('git ' + ' '.join(args))
        return 1, 'comando inesperado'

    def repo(self, nome, **git):
        """Repositório de mentira com .next e node_modules gerados, árvore limpa e commit antigo."""
        pasta = self.projetos / nome
        for arquivo in ('.git/HEAD', 'package.json', 'package-lock.json', 'src/pagina.tsx',
                        '.next/server/pagina.js', 'node_modules/pacote/index.js'):
            (pasta / arquivo).parent.mkdir(parents=True, exist_ok=True)
            (pasta / arquivo).write_text('x' * 5000)
        self.repos[str(pasta)] = {'status': '', 'versionado': '', 'ignorado': True,
                                  'commit': self.depois - 7 * self.HORAS, **git}
        return pasta

    def arquivo(self, caminho, horas=None, lido=None):
        """Arquivo mexido (e lido) há `horas` de `self.tarde`; sem `horas`, agora mesmo."""
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text('x' * 5000)
        if horas is not None:
            os.utime(caminho, (self.tarde - (horas if lido is None else lido) * self.HORAS,
                               self.tarde - horas * self.HORAS))
        return caminho

    def build(self, nome, *extras, fonte=None):
        """Build como o CMake deixa: marca apontando a fonte (fora, por padrão) e CMakeFiles/."""
        pasta = self.base / nome
        (pasta / 'CMakeFiles').mkdir(parents=True)
        for arquivo in ('CMakeFiles/a.o', *extras):
            (pasta / arquivo).parent.mkdir(parents=True, exist_ok=True)
            (pasta / arquivo).write_text('x')
        self.marca(pasta, fonte or Path(self.tmp.name) / 'codigo')
        return pasta

    def marca(self, pasta, fonte=None):
        linha = f'CMAKE_HOME_DIRECTORY:INTERNAL={fonte}\n' if fonte else ''
        (pasta / limpeza_disco.MARCA).write_text(f'# cache\nCMAKE_BUILD_TYPE:STRING=Release\n{linha}')

    def processo(self, pid, args, pai=1, cwd=None, aberto=None):
        pasta = self.proc / str(pid)
        (pasta / 'fd').mkdir(parents=True)
        (pasta / 'cmdline').write_bytes('\0'.join(args).encode())
        (pasta / 'stat').write_text(f'{pid} ({args[0]} x) S {pai} 1 1')
        (pasta / 'maps').write_text('')
        os.symlink(str(cwd or '/'), pasta / 'cwd')
        if aberto:
            os.symlink(str(aberto), pasta / 'fd' / '3')

    def etapa_tmp(self, simula=False):
        return limpeza_disco.etapa_tmp(simula, self.depois, lambda: 30)

    def prazo_que_acaba(self, chamadas):
        """Prazo que dá tempo nas primeiras `chamadas` consultas e depois zera."""
        restam = [chamadas]

        def prazo():
            restam[0] -= 1
            return 30 if restam[0] >= 0 else 0
        return prazo



class LimpezaDisco(AmbienteDaLimpeza, unittest.TestCase):
    """A limpeza só age acima do limiar, só apaga o que se refaz e nunca derruba o vigia."""

    def test_abaixo_do_limiar_nao_faz_nada(self):
        self.build('t1/build')
        r = limpeza_disco.limpa(simula=False, limiar=80)
        self.assertFalse(r['rodou'])
        self.assertEqual(self.comandos, [])
        self.assertTrue((self.base / 't1/build').exists())

    def test_simulacao_lista_e_nao_apaga(self):
        pasta = self.build('t1/build')
        r = limpeza_disco.limpa(simula=True)
        self.assertTrue(r['rodou'])
        self.assertTrue(pasta.exists())
        self.assertEqual([i['pasta'] for i in r['etapas']['tmp_cmake']['itens']], [str(pasta)])
        self.assertFalse(any(e['feito'] for e in r['etapas'].values()))
        self.assertNotIn('npm cache clean --force', self.comandos)
        self.assertNotIn('sudo -n apt-get clean', self.comandos)
        self.assertFalse(self.estado.exists())  # simulação não grava log nem a hora do npm

    def test_acima_do_limiar_roda_as_tres_etapas_e_registra(self):
        pasta = self.build('t1/build')
        fonte = self.base / 't1' / 'main.cpp'
        fonte.write_text('int main(){}')
        r = limpeza_disco.limpa(simula=False)
        self.assertFalse(pasta.exists())
        self.assertTrue(fonte.exists())  # fonte e log ao lado do build ficam
        self.assertIn('npm cache clean --force', self.comandos)
        self.assertIn('sudo -n apt-get clean', self.comandos)
        self.assertEqual([n for n, e in r['etapas'].items() if e['feito']], ['npm_cache', 'tmp_cmake', 'apt'])
        self.assertEqual(list(r['etapas']), [n for n, _ in limpeza_disco.ETAPAS])  # as demais não tinham o que apagar
        self.assertEqual((r['livre_antes_mb'], r['livre_depois_mb'], r['liberado_mb']), (20, 25, 5))
        self.assertEqual(r['etapas']['npm_cache']['liberado_mb'], 5)
        log = (self.estado / 'limpeza-disco.log').read_text()
        self.assertIn('"liberado_mb": 5', log)
        self.assertIn(str(pasta), log)

    def test_build_recente_fica(self):
        pasta = self.build('t1/build')
        os.utime(pasta / limpeza_disco.MARCA, (self.depois - self.HORAS, self.depois - self.HORAS))
        r = self.etapa_tmp()
        self.assertTrue(pasta.exists())
        self.assertIn('mexido há 1.0 h', r['mantidos'][0]['motivo'])

    def test_build_em_uso_fica(self):
        com_cwd, com_arquivo, livre = self.build('a/build'), self.build('b/build'), self.build('c/build')
        self.processo(10, ['make'], cwd=com_cwd / 'CMakeFiles')
        self.processo(11, ['ld'], aberto=com_arquivo / 'CMakeFiles/a.o')
        r = self.etapa_tmp()
        self.assertTrue(com_cwd.exists() and com_arquivo.exists())
        self.assertFalse(livre.exists())
        self.assertEqual(len(r['mantidos']), 2)

    def test_build_dentro_do_codigo_fica(self):
        pasta = self.build('projeto', 'CMakeLists.txt')
        r = self.etapa_tmp()
        self.assertTrue(pasta.exists())
        self.assertIn('CMakeLists.txt', r['mantidos'][0]['motivo'])

    def test_marca_na_raiz_com_fonte_em_subpasta_fica(self):
        # `cmake -S src -B .` de dentro da pasta de trabalho: X tem o build, a fonte e anotações
        x = self.build('X', 'src/CMakeLists.txt', 'src/main.cpp', 'notas.md', fonte=self.base / 'X/src')
        r = self.etapa_tmp()
        self.assertEqual(r['itens'], [])
        self.assertIn('dentro da pasta', r['mantidos'][0]['motivo'])
        for arquivo in ('src/CMakeLists.txt', 'src/main.cpp', 'notas.md', limpeza_disco.MARCA):
            self.assertTrue((x / arquivo).exists(), arquivo)

    def test_cmakelists_em_qualquer_nivel_fica(self):
        # a marca aponta fonte fora, mas há código dentro: fica; o que o CMake gera/baixa não conta
        com_codigo = self.build('a/build', 'x/y/CMakeLists.txt', 'x/y/main.cpp')
        so_build = self.build('b/build', '_deps/lib-src/CMakeLists.txt', 'sub/CMakeFiles/CMakeTmp/CMakeLists.txt')
        r = self.etapa_tmp()
        self.assertTrue((com_codigo / 'x/y/main.cpp').exists())
        self.assertIn('x/y/CMakeLists.txt', r['mantidos'][0]['motivo'])
        self.assertFalse(so_build.exists())
        self.assertEqual([i['pasta'] for i in r['itens']], [str(so_build)])

    def test_marca_solta_num_clone_fica(self):
        # CMakeCache.txt como arquivo de teste dentro de um checkout: não é build
        clone = self.base / 'clone'
        (clone / '.git').mkdir(parents=True)
        fix = clone / 'tests/fix'
        fix.mkdir(parents=True)
        self.marca(fix, Path(self.tmp.name) / 'codigo')
        (fix / 'esperado.txt').write_text('x')
        r = self.etapa_tmp()
        self.assertEqual(r['itens'], [])
        self.assertIn('CMakeFiles', r['mantidos'][0]['motivo'])
        self.assertTrue((fix / 'esperado.txt').exists())

    def test_marca_sem_a_linha_da_fonte_fica(self):
        pasta = self.build('t1/build')
        self.marca(pasta)  # sem CMAKE_HOME_DIRECTORY
        r = self.etapa_tmp()
        self.assertTrue(pasta.exists())
        self.assertIn('CMAKE_HOME_DIRECTORY', r['mantidos'][0]['motivo'])

    def test_fonte_dentro_por_link_fica(self):
        # a marca aponta a fonte por um link que cai dentro da pasta: compara pelo caminho real
        pasta = self.build('t1/build', 'codigo/main.cpp')
        atalho = Path(self.tmp.name) / 'atalho'
        os.symlink(pasta / 'codigo', atalho)
        self.marca(pasta, atalho)
        self.etapa_tmp()
        self.assertTrue((pasta / 'codigo/main.cpp').exists())

    def test_build_dentro_de_worktree_sai_so_o_build(self):
        wt = self.base / 'wt'
        (wt / '.git').mkdir(parents=True)
        (wt / 'CMakeLists.txt').write_text('x')
        (wt / 'main.cpp').write_text('x')
        build = self.build('wt/build', fonte=wt)
        r = self.etapa_tmp()
        self.assertEqual([i['pasta'] for i in r['itens']], [str(build)])
        self.assertFalse(build.exists())
        self.assertTrue((wt / '.git').is_dir() and (wt / 'main.cpp').exists() and (wt / 'CMakeLists.txt').exists())

    def test_procura_de_codigo_respeita_o_tempo_e_nao_segue_link(self):
        fora = Path(self.tmp.name) / 'fora'
        fora.mkdir()
        (fora / 'CMakeLists.txt').write_text('x')
        pasta = self.build('t1/build', 'sub/a.o')
        os.symlink(fora, pasta / 'sub/atalho')  # o CMakeLists.txt atrás do link não conta
        self.assertIsNone(limpeza_disco.codigo_dentro(str(pasta), lambda: 30))
        usados = limpeza_disco.caminhos_em_uso(str(self.base))
        self.assertEqual(limpeza_disco.avalia_build(str(pasta), self.depois, usados, lambda: 0),
                         (0, 'sem tempo nesta rodada'))
        self.assertTrue(pasta.exists())
        self.assertEqual(self.etapa_tmp()['itens'][0]['pasta'], str(pasta))
        self.assertTrue((fora / 'CMakeLists.txt').exists())

    def test_nao_segue_link_nem_apaga_a_base(self):
        fora = Path(self.tmp.name) / 'fora' / 'build'
        fora.mkdir(parents=True)
        self.marca(fora, Path(self.tmp.name) / 'codigo')
        os.symlink(fora, self.base / 'link')            # link para build fora da base
        os.symlink(fora.parent, self.base / 'pasta-link')  # link para pasta que contém build
        (self.base / limpeza_disco.MARCA).write_text('x')  # marca solta na raiz da base
        dentro = self.build('t1/build')
        os.symlink(fora, dentro / 'atalho')              # link dentro do build: sai o link, não o alvo
        self.etapa_tmp()
        self.assertFalse(dentro.exists())
        self.assertTrue(self.base.exists())
        self.assertTrue((fora / limpeza_disco.MARCA).exists())
        self.assertTrue((self.base / 'link').is_symlink())

    def test_nao_entra_em_node_modules_nem_em_git(self):
        nm, git = self.build('app/node_modules/x/build'), self.build('repo/.git/build')
        self.assertEqual(self.etapa_tmp()['itens'], [])
        self.assertTrue(nm.exists() and git.exists())

    def test_base_fora_de_tmp_e_recusada(self):
        with mock.patch.object(limpeza_disco, 'base_permitida', return_value=False):
            pasta = self.build('t1/build')
            self.assertIn('nada apagado', self.etapa_tmp()['erro'])
        self.assertTrue(pasta.exists())
        self.assertFalse(limpeza_disco.base_permitida(str(Path.home() / 'projetos')))
        self.assertFalse(limpeza_disco.base_permitida('/var/lib/docker'))

    def test_tmpdir_do_ambiente_nao_alarga_a_base(self):
        # com TMPDIR apontando para outro lugar, a pasta temporária do sistema não vira base
        fora = Path(self.tmp.name) / 'home'
        pasta = fora / 'projetos/x/build'
        (pasta / 'CMakeFiles').mkdir(parents=True)
        self.marca(pasta, Path(self.tmp.name) / 'codigo')
        with mock.patch.object(limpeza_disco, 'RAIZES', (str(self.base),)), \
                mock.patch.object(tempfile, 'tempdir', str(fora)):
            self.assertEqual(tempfile.gettempdir(), str(fora))
            self.assertFalse(limpeza_disco.base_permitida(str(fora / 'projetos')))
            r = limpeza_disco.etapa_tmp(False, self.depois, lambda: 30, base=fora)
        self.assertIn('nada apagado', r['erro'])
        self.assertTrue(pasta.exists())
        self.assertEqual(limpeza_disco.RAIZES, (str(Path(self.tmp.name)),))  # o setUp troca; no código é só /tmp

    def test_raiz_de_producao_e_so_tmp(self):
        self.addCleanup(mock.patch.stopall)
        mock.patch.stopall()  # desfaz as trocas do setUp: vale a constante do código
        self.assertEqual(limpeza_disco.RAIZES, ('/tmp',))

    def test_sem_tempo_na_procura_nao_apaga_nada(self):
        builds = {str(self.build(f'p{i}/build')) for i in range(3)}
        # dá tempo de olhar a base, uma pasta e o build dela; o resto fica para a próxima rodada
        achados = limpeza_disco.procura_builds(str(self.base), self.prazo_que_acaba(3))
        self.assertEqual(len(achados), 1)
        self.assertLess(set(achados), builds)
        self.assertEqual(limpeza_disco.procura_builds(str(self.base), lambda: 0), [])
        self.assertEqual(len(limpeza_disco.procura_builds(str(self.base))), 3)

    def test_sem_tempo_para_ver_quem_usa_nao_apaga_nada(self):
        pasta = self.build('t1/build')
        self.processo(10, ['sleep'])
        self.processo(11, ['make'], cwd=pasta)  # o processo que usa o build é o último a ser lido
        with self.assertRaises(TimeoutError):
            limpeza_disco.caminhos_em_uso(str(self.base), prazo=lambda: 0)
        r = limpeza_disco.etapa_tmp(False, self.depois, lambda: 0)
        self.assertEqual((r['feito'], r['detalhe']), (False, 'pulado: sem tempo nesta rodada'))
        self.assertTrue(pasta.exists())

    def test_sem_tempo_no_meio_da_avaliacao_ou_antes_de_apagar_fica(self):
        pasta = self.build('t1/build', 'sub/a.o', 'sub/b/c.o')
        usados = set()
        # 3 consultas na procura de código (raiz, sub, sub/b) e acaba na 1ª da conta de tamanho e idade
        self.assertEqual(limpeza_disco.avalia_build(str(pasta), self.depois, usados, self.prazo_que_acaba(3)),
                         (0, 'sem tempo nesta rodada'))
        self.assertIsNone(limpeza_disco.avalia_build(str(pasta), self.depois, usados)[1])
        # avaliado e pronto para sair, mas o tempo acaba ao reconferir quem usa: fica
        de_verdade = limpeza_disco.caminhos_em_uso
        chamadas = []

        def em_uso(base, proc=None, prazo=lambda: 1):
            chamadas.append(1)
            if len(chamadas) > 1:
                raise TimeoutError
            return de_verdade(base, proc, prazo)
        with mock.patch.object(limpeza_disco, 'caminhos_em_uso', em_uso):
            r = self.etapa_tmp()
        self.assertTrue(pasta.exists())
        self.assertEqual((r['itens'], r['mantidos'][0]['motivo']), ([], 'sem tempo nesta rodada'))

    def test_pasta_trocada_depois_da_avaliacao_fica(self):
        pasta = self.build('t1/build')
        guardada = Path(self.tmp.name) / 'guardada'
        de_verdade = limpeza_disco.avalia_build

        def avalia_e_troca(caminho, *resto):
            resultado = de_verdade(caminho, *resto)
            os.rename(caminho, guardada)  # depois de avaliada, outra pasta toma o lugar dela
            (Path(caminho) / 'CMakeFiles').mkdir(parents=True)
            (Path(caminho) / 'importante.dat').write_text('x')
            return resultado
        with mock.patch.object(limpeza_disco, 'avalia_build', avalia_e_troca):
            r = self.etapa_tmp()
        self.assertTrue((pasta / 'importante.dat').exists())
        self.assertEqual(r['itens'], [])
        self.assertIn('trocada por outra', r['erro'])

    def test_outro_disco_montado_dentro_do_build_fica(self):
        pasta = self.build('t1/build', 'montado/a.o')
        de_verdade = os.lstat

        def lstat(caminho, *a, **k):
            st = de_verdade(caminho, *a, **k)
            if str(caminho).endswith('/montado'):
                st = os.stat_result((*st[:2], st.st_dev + 1, *st[3:]))
            return st
        with mock.patch.object(limpeza_disco.os, 'lstat', lstat):
            r = self.etapa_tmp()
        self.assertTrue((pasta / 'montado/a.o').exists())
        self.assertEqual(r['mantidos'][0]['motivo'], 'tem outro disco montado dentro (montado)')

    def test_arquivo_de_gente_solto_na_raiz_do_build_fica(self):
        # `cmake ~/projetos/x` de dentro da pasta de trabalho: build de verdade com anotação ao lado
        for i, solto in enumerate(('notas.md', 'rascunho.cpp', 'remendo.patch', 'lista.txt')):
            pasta = self.build(f's{i}/build', solto)
            r = self.etapa_tmp()
            self.assertTrue((pasta / solto).exists(), solto)
            self.assertIn(f'tem {solto} solto na raiz', r['mantidos'][-1]['motivo'])
        # o que o CMake grava na raiz não segura o build, nem arquivo de gente em subpasta gerada
        so_build = self.build('z/build', 'install_manifest.txt', 'install_manifest_dev.txt', 'Makefile',
                              'cmake_install.cmake', 'compile_commands.json', 'sub/gerado.cpp')
        r = self.etapa_tmp()
        self.assertFalse(so_build.exists())
        self.assertEqual([i['pasta'] for i in r['itens']], [str(so_build)])

    def test_fonte_relativa_na_marca_fica(self):
        # marca escrita à mão: `src` seria resolvido contra a pasta de trabalho do vigia, não a do build
        pasta = self.build('R', 'src/main.cpp', fonte='src')
        r = self.etapa_tmp()
        self.assertTrue((pasta / 'src/main.cpp').exists())
        self.assertEqual(r['itens'], [])
        self.assertIn('fonte relativa (src)', r['mantidos'][0]['motivo'])

    def test_npm_rodando_pula_o_cache(self):
        self.processo(20, ['npm', 'ci'])
        r = limpeza_disco.limpa(simula=False)
        self.assertIn('npm rodando', r['etapas']['npm_cache']['detalhe'])
        self.assertNotIn('npm cache clean --force', self.comandos)
        self.assertNotIn('erro', r['etapas']['npm_cache'])

    def test_quem_conta_como_npm_rodando(self):
        self.processo(20, ['node', '/usr/lib/node_modules/npm/bin/npm-cli.js', 'install'])
        self.processo(21, ['npm exec @x/servidor --stdio'])       # título regravado pelo node, com filho
        self.processo(22, ['node', '/home/u/.npm/_npx/1/servidor'], pai=21)
        self.processo(23, ['npx', 'pacote'])                       # ainda baixando: sem filho
        self.processo(24, ['node', '--max-old-space-size=2048', '/x/openclaw/dist/index.js', 'gateway'])
        self.assertEqual([o.split()[0] for o in limpeza_disco.npm_em_uso()], ['20', '23'])

    def test_cache_do_npm_espera_o_intervalo(self):
        limpeza_disco.limpa(simula=False)
        self.comandos.clear()
        r = limpeza_disco.limpa(simula=False)
        self.assertIn('cache limpo há', r['etapas']['npm_cache']['detalhe'])
        self.assertNotIn('npm cache clean --force', self.comandos)

    def test_sem_sudo_pula_o_apt_sem_erro(self):
        self.codigos['sudo'] = 1
        r = limpeza_disco.limpa(simula=False)
        self.assertIn('sem sudo sem senha', r['etapas']['apt']['detalhe'])
        self.assertNotIn('erro', r['etapas']['apt'])
        self.assertNotIn('sudo -n apt-get clean', self.comandos)

    def test_apt_sem_pacote_nem_chama_o_sudo(self):
        (self.apt / 'a.deb').unlink()
        limpeza_disco.limpa(simula=False)
        self.assertFalse(any(c.startswith('sudo') for c in self.comandos))

    def test_etapa_que_estoura_nao_derruba_as_outras(self):
        with mock.patch.object(limpeza_disco, 'ETAPAS', (('npm_cache', mock.Mock(side_effect=RuntimeError('pane'))),
                                                         ('apt', limpeza_disco.etapa_apt))):
            r = limpeza_disco.limpa(simula=False)
        self.assertIn('RuntimeError: pane', r['etapas']['npm_cache']['erro'])
        self.assertTrue(r['etapas']['apt']['feito'])

    def test_falha_da_limpeza_nao_derruba_o_vigia(self):
        with mock.patch.object(limpeza_disco, 'limpa', side_effect=RuntimeError('pane')):
            self.assertIn('RuntimeError: pane', vigia_saude.limpeza(False)['erro'])
        with mock.patch.dict(sys.modules, {'limpeza_disco': None}):  # módulo que não importa
            self.assertIn('ModuleNotFoundError', vigia_saude.limpeza(False)['erro'])

    def test_vigia_simula_no_teste_e_respeita_o_desligado(self):
        with mock.patch.object(limpeza_disco, 'limpa', return_value={'rodou': False}) as limpa:
            vigia_saude.limpeza(True)
            limpa.assert_called_with(simula=True)
            vigia_saude.limpeza(False)
            limpa.assert_called_with(simula=False)
            with mock.patch.object(vigia_saude, 'LIMPEZA', 'simula'):
                vigia_saude.limpeza(False)
                limpa.assert_called_with(simula=True)
            limpa.reset_mock()
            with mock.patch.object(vigia_saude, 'LIMPEZA', '0'):
                self.assertFalse(vigia_saude.limpeza(False)['rodou'])
            limpa.assert_not_called()

    def test_vigia_mede_o_disco_como_o_df(self):
        # 38 GB com 5% de reserva do root: 30 usados e 6,1 livres dão 83% no df (79% sobre o total)
        uso = mock.Mock(total=38_000, used=30_000, free=6_100)
        with mock.patch.object(vigia_saude.shutil, 'disk_usage', return_value=uso):
            self.assertEqual(vigia_saude.disco(), 83)
            self.assertEqual(vigia_saude.disco(), round(limpeza_disco.uso_pct(uso.used, uso.free)))
        # com o mesmo disco, 86% no df abre o alerta; antes eram 82% sobre o total e passava calado
        uso = mock.Mock(total=38_000, used=31_000, free=5_100)
        with mock.patch.object(vigia_saude.shutil, 'disk_usage', return_value=uso):
            self.assertGreater(vigia_saude.disco(), vigia_saude.DISCO_MAX_PCT)

    def test_ajuda_do_vigia_avisa_que_sem_a_opcao_apaga(self):
        self.assertIn('--sem-banco', vigia_saude.__doc__)
        self.assertIn('APAGA', vigia_saude.__doc__)

    def test_vigia_chama_a_limpeza_e_sobrevive_a_falha_dela(self):
        with mock.patch.object(limpeza_disco, 'limpa', side_effect=RuntimeError('pane')) as limpa:
            _, fecha = roda_o_vigia(limpeza=None)
        limpa.assert_called_once_with(simula=False)
        self.assertTrue(fecha.called)  # a medida e o quadro já estavam gravados


class LimpezaForaDoTmp(AmbienteDaLimpeza, unittest.TestCase):
    """Etapas da tarefa 278 (docker, logs, _npx, store do pnpm, .next, node_modules): cada uma
    só apaga com a trava de uso satisfeita, e caminho proibido não sai nem passado por engano."""

    def etapa(self, etapa, simula=False, agora=None):
        return etapa(simula, self.tarde if agora is None else agora, lambda: 30)

    def cheio(self):
        """Põe o `/` em 85%: acima dos 80% que liberam o node_modules."""
        p = mock.patch.object(limpeza_disco, 'espaco',
                              lambda: (100 * limpeza_disco.MB, 85 * limpeza_disco.MB, 15 * limpeza_disco.MB))
        p.start()
        self.addCleanup(p.stop)

    def motivos(self, r):
        return {Path(m.get('pasta', m.get('imagem', ''))).name: m['motivo'] for m in r['mantidos']}

    # --- (a) imagens Docker

    def test_docker_tira_so_imagem_com_tag_sem_conteiner(self):
        self.imagem('avilaops-app:531217e', mb=450)
        self.imagem('lojas-db:teste', conteiner=True)
        self.imagem('postgres:18-alpine')
        self.imagem('<none>:<none>')
        self.imagem('avilaops-app:novo', horas=1)
        r = self.etapa(limpeza_disco.etapa_docker, agora=self.depois)
        self.assertEqual([c for c in self.comandos if c.startswith('docker rmi')], ['docker rmi avilaops-app:531217e'])
        self.assertEqual([i['nome'] for i in self.imagens],
                         ['lojas-db:teste', 'postgres:18-alpine', '<none>:<none>', 'avilaops-app:novo'])
        self.assertEqual(r['itens'], [{'imagem': 'avilaops-app:531217e', 'mb': 450}])
        self.assertEqual(self.motivos(r), {'lojas-db:teste': 'tem contêiner', 'postgres:18-alpine': 'mantida de propósito',
                                           'avilaops-app:novo': 'criada há 1.0 h (mín. 2)'})

    def test_docker_outra_tag_da_imagem_guardada_tambem_fica(self):
        ident = self.imagem('postgres:18-alpine')
        self.imagens.append({**self.imagens[0], 'nome': 'banco-local:1'})  # mesma imagem, outro nome
        r = self.etapa(limpeza_disco.etapa_docker, agora=self.depois)
        self.assertEqual(r['itens'], [])
        self.assertEqual([i['id'] for i in self.imagens], [ident, ident])

    def test_docker_no_ensaio_so_lista(self):
        self.imagem('avilaops-app:531217e', mb=450)
        r = self.etapa(limpeza_disco.etapa_docker, simula=True, agora=self.depois)
        self.assertEqual((r['feito'], r['estimado_mb'], r['itens'][0]['imagem']), (False, 450, 'avilaops-app:531217e'))
        self.assertEqual(len(self.imagens), 1)
        self.assertFalse(any('rmi' in c for c in self.comandos))

    def test_docker_com_build_ou_save_rodando_pula(self):
        self.imagem('avilaops-app:531217e')
        self.processo(30, ['docker', 'save', 'avilaops-app:531217e'])
        r = self.etapa(limpeza_disco.etapa_docker, agora=self.depois)
        self.assertIn('pulado: docker em uso', r['detalhe'])
        self.assertEqual(self.comandos, [])

    def test_docker_sem_saber_dos_conteineres_nao_apaga(self):
        self.imagem('avilaops-app:531217e')
        for falha in ('docker', 'docker ps', 'docker inspect', 'docker image'):
            self.falha = {falha}
            self.conteineres = {'c1': 'sha256:' + 'f' * 64}
            r = self.etapa(limpeza_disco.etapa_docker, agora=self.depois)
            self.assertIn('pulado', r['detalhe'], falha)
        self.assertFalse(any('rmi' in c for c in self.comandos))

    def test_docker_conteiner_criado_depois_da_lista_segura(self):
        ident = self.imagem('avilaops-app:531217e')
        de_verdade, chamadas = limpeza_disco.imagens_com_conteiner, []

        def com_conteiner(prazo):
            chamadas.append(1)
            if len(chamadas) == 2:  # alguém deu `docker run` entre a lista e o rmi
                self.conteineres['c9'] = ident
            return de_verdade(prazo)

        with mock.patch.object(limpeza_disco, 'imagens_com_conteiner', com_conteiner):
            r = self.etapa(limpeza_disco.etapa_docker, agora=self.depois)
        self.assertEqual(self.motivos(r), {'avilaops-app:531217e': 'contêiner novo desde a avaliação'})
        self.assertFalse(any('rmi' in c for c in self.comandos))

    def test_docker_so_aceita_leitura_e_rmi_de_nome_com_tag(self):
        for args in (['volume', 'rm', 'pgdata'], ['volume', 'prune', '-f'], ['system', 'prune', '-af'],
                     ['image', 'prune', '-a'], ['rm', '-f', 'c1'], ['rmi', '-f', 'app:1'], ['rmi', 'app:1', 'app:2'],
                     ['rmi', 'sha256:' + 'a' * 64], ['rmi', 'postgres:18-alpine'], ['rmi', 'semtag'], ['compose', 'down', '-v']):
            codigo, saida = limpeza_disco.docker(args, lambda: 30)
            self.assertIsNone(codigo, args)
            self.assertIn('recusado', saida)
        self.assertEqual(self.comandos, [])

    # --- (b) .next

    def test_next_sai_de_repo_limpo_e_o_resto_fica(self):
        repo = self.repo('loja')
        (repo / 'apps/web/.next').mkdir(parents=True)
        (repo / 'apps/web/package.json').write_text('{}')
        r = self.etapa(limpeza_disco.etapa_next)
        self.assertFalse((repo / '.next').exists() or (repo / 'apps/web/.next').exists())
        for fica in ('node_modules/pacote/index.js', 'src/pagina.tsx', '.git/HEAD', 'apps/web/package.json'):
            self.assertTrue((repo / fica).exists(), fica)
        self.assertEqual([i['pasta'] for i in r['itens']], [str(repo / '.next'), str(repo / 'apps/web/.next')])
        self.assertNotIn('erro', r)

    def test_next_no_ensaio_so_lista_e_estima(self):
        repo = self.repo('loja')
        (repo / '.next/grande').write_bytes(b'x' * 3 * limpeza_disco.MB)
        r = self.etapa(limpeza_disco.etapa_next, simula=True)
        self.assertTrue((repo / '.next/grande').exists())
        self.assertEqual((r['feito'], r['estimado_mb'], r['itens']), (False, 3, [{'pasta': str(repo / '.next'), 'mb': 3}]))
        self.assertIn('apagaria 1 .next', r['detalhe'])

    def test_next_de_repo_com_arvore_suja_fica(self):
        repo = self.repo('loja', status=' M src/pagina.tsx\n?? rascunho.md')
        r = self.etapa(limpeza_disco.etapa_next)
        self.assertTrue((repo / '.next/server/pagina.js').exists())
        self.assertEqual(self.motivos(r), {'.next': 'árvore git suja (2 arquivo(s))'})

    def test_next_com_processo_no_repo_fica(self):
        casos = {'cwd': dict(args=['node', 'server.js'], cwd='{repo}/apps'),
                 'raiz': dict(args=['bash'], cwd='{repo}'),
                 'aberto': dict(args=['node', 'x'], aberto='{repo}/.next/trace'),
                 'linha': dict(args=['node', '{repo}/node_modules/.bin/next', 'start']),
                 'shell': dict(args=['bash', '-c', 'cd {repo}; npm run build'])}
        for pid, (nome, caso) in enumerate(casos.items(), 40):
            repo = self.repo(nome)
            (repo / 'apps').mkdir()
            self.processo(pid, [a.format(repo=repo) for a in caso['args']],
                          **{k: v.format(repo=repo) for k, v in caso.items() if k != 'args'})
        vizinho = self.repo('cwd-outro')  # nome parecido não confunde: é outro repositório
        r = self.etapa(limpeza_disco.etapa_next)
        for nome in casos:
            self.assertTrue((self.projetos / nome / '.next/server/pagina.js').exists(), nome)
        self.assertFalse((vizinho / '.next').exists())
        self.assertEqual(len(r['mantidos']), 5)
        self.assertTrue(all(m['motivo'].startswith('processo no repositório') for m in r['mantidos']))

    def test_next_processo_que_chega_depois_da_avaliacao_segura(self):
        repo = self.repo('loja')
        de_verdade, chamadas = limpeza_disco.trava_do_repo, []

        def trava(caminho, prazo):
            chamadas.append(1)
            if len(chamadas) == 2:  # entre a medida e o apagar alguém entrou no repositório
                self.processo(77, ['npm', 'run', 'build'], cwd=repo)
            return de_verdade(caminho, prazo)

        with mock.patch.object(limpeza_disco, 'trava_do_repo', trava):
            r = self.etapa(limpeza_disco.etapa_next)
        self.assertTrue((repo / '.next/server/pagina.js').exists())
        self.assertIn('processo no repositório', r['mantidos'][0]['motivo'])

    def test_next_versionada_nao_ignorada_ou_sem_package_json_fica(self):
        a = self.repo('versionada', versionado='.next/server/pagina.js')
        b = self.repo('nao-ignorada', ignorado=False)
        c = self.repo('sem-package')
        (c / 'package.json').unlink()
        d = self.repo('sem-git')
        del self.repos[str(d)]  # o git não responde por ele
        r = self.etapa(limpeza_disco.etapa_next)
        for repo in (a, b, c, d):
            self.assertTrue((repo / '.next/server/pagina.js').exists(), repo.name)
        self.assertEqual(r['itens'], [])
        self.assertEqual(sorted(m['motivo'][:22] for m in r['mantidos']),
                         ['git status não respond', 'não é ignorada pelo gi', 'sem package.json ao la',
                          'tem arquivo versionado'])

    def test_next_com_arquivo_guardado_ou_por_link_fica(self):
        guardado = self.repo('guardado')
        (guardado / '.next/server/config.js.antes-t276').write_text('x')
        fora = Path(self.tmp.name) / 'fora/.next'
        fora.mkdir(parents=True)
        (fora / 'a.js').write_text('x')
        ligado = self.repo('ligado')
        limpeza_disco.shutil.rmtree(ligado / '.next')
        os.symlink(fora, ligado / '.next')
        os.symlink(self.repo('alvo'), self.projetos / 'atalho')  # repositório alcançado por link não conta duas vezes
        aninhado = self.repo('pai')
        limpeza_disco.shutil.rmtree(aninhado / '.next')
        for arquivo in ('outro/.git/HEAD', 'outro/package.json', 'outro/.next/a.js', '.work/openclaw/x/.next/a.js'):
            (aninhado / arquivo).parent.mkdir(parents=True, exist_ok=True)
            (aninhado / arquivo).write_text('x')
        r = self.etapa(limpeza_disco.etapa_next)
        self.assertIn('config.js.antes-t276 guardado', self.motivos(r)['.next'])
        self.assertTrue((guardado / '.next/server/pagina.js').exists())
        self.assertTrue((fora / 'a.js').exists() and (ligado / '.next').is_symlink())
        self.assertTrue((aninhado / 'outro/.next/a.js').exists() and (aninhado / '.work/openclaw/x/.next/a.js').exists())
        self.assertEqual([i['pasta'] for i in r['itens']], [str(self.projetos / 'alvo/.next')])

    # --- (f) node_modules

    def test_node_modules_nao_roda_ate_80_por_cento(self):
        repo = self.repo('loja')
        r = self.etapa(limpeza_disco.etapa_node_modules)
        self.assertEqual(r, {'feito': False, 'detalhe': 'pulado: disco em 80.0%, não passa de 80%'})
        self.assertTrue((repo / 'node_modules/pacote/index.js').exists())
        self.assertEqual(self.comandos, [])  # nem consulta o quadro

    def test_node_modules_sai_acima_de_80_com_todas_as_travas(self):
        self.cheio()
        repo = self.repo('loja')
        self.quadro = 'infra ampliar o limpeza_disco.py\nlojas.avilaops.com corrigir o carrinho do cliente\n'
        r = self.etapa(limpeza_disco.etapa_node_modules)
        self.assertFalse((repo / 'node_modules').exists())
        for fica in ('.next/server/pagina.js', 'package-lock.json', 'src/pagina.tsx'):
            self.assertTrue((repo / fica).exists(), fica)
        self.assertEqual([i['pasta'] for i in r['itens']], [str(repo / 'node_modules')])
        self.assertTrue(r['feito'])

    def test_node_modules_no_ensaio_so_lista(self):
        self.cheio()
        repo = self.repo('loja')
        r = self.etapa(limpeza_disco.etapa_node_modules, simula=True)
        self.assertTrue((repo / 'node_modules/pacote/index.js').exists())
        self.assertEqual((r['feito'], [i['pasta'] for i in r['itens']]), (False, [str(repo / 'node_modules')]))

    def test_node_modules_com_tarefa_em_andamento_para_o_repo_fica(self):
        self.cheio()
        pela_coluna, pelo_pedido = self.repo('Mello'), self.repo('erp')
        outro = self.repo('loja')
        self.quadro = 'Mello ajustar o painel\n interpretar o log e corrigir o build em ~/projetos/ERP.\nloja2 outra coisa\n'
        r = self.etapa(limpeza_disco.etapa_node_modules)
        self.assertTrue((pela_coluna / 'node_modules').exists() and (pelo_pedido / 'node_modules').exists())
        self.assertFalse((outro / 'node_modules').exists())  # "loja2" e "interpretar" não são "loja" nem "erp"
        self.assertEqual(set(self.motivos(r).values()), {'tarefa em_andamento no quadro para o repositório'})
        self.assertEqual(len(r['mantidos']), 2)

    def test_node_modules_sem_resposta_do_quadro_nao_roda(self):
        self.cheio()
        repo = self.repo('loja')
        self.falha = {'psql'}
        r = self.etapa(limpeza_disco.etapa_node_modules)
        self.assertIn('pulado: o quadro não respondeu', r['detalhe'])
        self.assertTrue((repo / 'node_modules').exists())

    def test_node_modules_seguro_por_cada_trava(self):
        self.cheio()
        self.repo('suja', status='?? novo.ts')
        self.processo(50, ['node', 'servidor.js'], cwd=self.repo('com-processo'))
        self.repo('commit-novo', commit=self.tarde - 3 * self.HORAS)
        (self.repo('sem-lock') / 'package-lock.json').unlink()
        self.repo('versionado', versionado='node_modules/pacote/index.js')
        self.arquivo(self.repo('instalado-agora') / 'node_modules/novo/index.js', horas=2)
        r = self.etapa(limpeza_disco.etapa_node_modules)
        self.assertEqual(r['itens'], [])
        self.assertEqual({nome: motivo[:19] for nome, motivo in (
            (Path(m['pasta']).parent.name, m['motivo']) for m in r['mantidos'])},
            {'suja': 'árvore git suja (1 ', 'com-processo': 'processo no reposit', 'commit-novo': 'commit há 3.0 h (mí',
             'sem-lock': 'sem lockfile: a rei', 'versionado': 'tem arquivo version', 'instalado-agora': 'mexido há 2.0 h (mí'})
        for nome in ('suja', 'com-processo', 'commit-novo', 'sem-lock', 'versionado', 'instalado-agora'):
            self.assertTrue((self.projetos / nome / 'node_modules/pacote/index.js').exists(), nome)

    def test_node_modules_de_subpasta_usa_o_lockfile_da_raiz(self):
        self.cheio()
        repo = self.repo('mono')
        for arquivo in ('webmail/package.json', 'webmail/node_modules/p/index.js'):
            (repo / arquivo).parent.mkdir(parents=True, exist_ok=True)
            (repo / arquivo).write_text('x')
        r = self.etapa(limpeza_disco.etapa_node_modules)
        self.assertEqual(len(r['itens']), 2)
        self.assertFalse((repo / 'webmail/node_modules').exists())
        self.assertTrue((repo / 'webmail/package.json').exists())

    def test_rodada_inteira_no_ensaio_soma_o_que_liberaria_e_nao_apaga(self):
        self.cheio()
        repo = self.repo('loja')
        (repo / '.next/grande').write_bytes(b'x' * 2 * limpeza_disco.MB)
        self.imagem('avilaops-app:531217e', mb=450)
        with mock.patch.object(limpeza_disco.time, 'time', lambda: self.tarde):
            r = limpeza_disco.limpa(simula=True)
        self.assertEqual(r['etapas']['docker_imagens']['estimado_mb'], 450)
        self.assertEqual(r['etapas']['next']['estimado_mb'], 2)
        self.assertEqual(r['estimado_mb'], sum(e.get('estimado_mb') or 0 for e in r['etapas'].values()))
        self.assertGreaterEqual(r['estimado_mb'], 452)
        self.assertTrue((repo / '.next/grande').exists() and (repo / 'node_modules').exists())
        self.assertEqual(len(self.imagens), 1)
        self.assertNotIn('itens', limpeza_disco.resumo(r)['etapas']['next'])  # a linha do vigia segue curta

    # --- (c) ~/.npm/_npx

    def test_npx_sem_processo_e_sem_leitura_ha_12_h_sai(self):
        parada, viva = self.npx / 'aaaa', self.npx / 'bbbb'
        for entrada in (parada, viva, self.npx / 'cccc'):
            self.arquivo(entrada / 'node_modules/.bin/servidor', horas=40)
        self.arquivo(self.npx / 'cccc/node_modules/pacote/index.js', horas=40, lido=3)  # lida há 3 h
        self.processo(60, ['node', str(viva / 'node_modules/.bin/servidor'), '--stdio'])
        r = self.etapa(limpeza_disco.etapa_npx, agora=self.tarde - 30 * self.HORAS)
        self.assertEqual(r['itens'], [])  # só 10 h depois da última mexida: ainda não
        r = self.etapa(limpeza_disco.etapa_npx)
        self.assertFalse(parada.exists())
        self.assertTrue((viva / 'node_modules/.bin/servidor').exists() and (self.npx / 'cccc').exists())
        self.assertEqual([i['pasta'] for i in r['itens']], [str(parada)])
        self.assertEqual({nome: motivo[:22] for nome, motivo in self.motivos(r).items()},
                         {'bbbb': 'em uso por processo (/', 'cccc': 'lido ou mexido há 3.0 '})

    def test_npx_baixando_pula_a_etapa(self):
        self.arquivo(self.npx / 'aaaa/node_modules/x/index.js', horas=40)
        self.processo(61, ['npx', 'pacote'])  # sem filho: ainda está gravando em _npx
        r = self.etapa(limpeza_disco.etapa_npx)
        self.assertIn('pulado: npm rodando', r['detalhe'])
        self.assertTrue((self.npx / 'aaaa').exists())

    def test_npx_no_ensaio_so_lista(self):
        self.arquivo(self.npx / 'aaaa/node_modules/x/index.js', horas=40)
        r = self.etapa(limpeza_disco.etapa_npx, simula=True)
        self.assertEqual([i['pasta'] for i in r['itens']], [str(self.npx / 'aaaa')])
        self.assertTrue((self.npx / 'aaaa/node_modules/x/index.js').exists())

    # --- (d) logs do claude-cli e node-compile-cache

    def test_logs_do_claude_com_mais_de_um_dia_saem(self):
        velho = self.arquivo(self.logs / '-home-u-projetos/mcp-logs-github/2026-10-07.jsonl', horas=30)
        novo = self.arquivo(self.logs / '-home-u-projetos/mcp-logs-github/2026-10-09.jsonl', horas=5)
        aberto = self.arquivo(self.logs / '-home-u/mcp-logs-openclaw/sessao.jsonl', horas=30)
        outro = self.arquivo(self.logs / '-home-u/cache-de-outra-coisa/dado.json', horas=30)
        guardado = self.arquivo(self.logs / '-home-u/mcp-logs-openclaw/a.jsonl.antes-t223', horas=30)
        self.processo(70, ['claude'], aberto=aberto)
        ensaio = self.etapa(limpeza_disco.etapa_claude_logs, simula=True)
        self.assertTrue(velho.exists())
        self.assertEqual((ensaio['feito'], sum(i['arquivos'] for i in ensaio['itens'])), (False, 1))
        r = self.etapa(limpeza_disco.etapa_claude_logs)
        self.assertFalse(velho.exists())
        for fica in (novo, aberto, outro, guardado, velho.parent):
            self.assertTrue(fica.exists(), fica)
        self.assertEqual(r['itens'], [{'pasta': str(self.logs / '-home-u-projetos'), 'arquivos': 1, 'mb': 0.0}])
        self.assertIn('1 aberto(s) por processo ficaram', r['detalhe'])
        self.assertTrue(r['feito'])

    def test_compile_cache_nao_lido_ha_um_dia_sai(self):
        velho = self.arquivo(self.cache_node / 'v24-x64-1000/aaa', horas=30)
        lido = self.arquivo(self.cache_node / 'v24-x64-1000/bbb', horas=30, lido=2)
        os.symlink(velho, self.cache_node / 'atalho')
        r = self.etapa(limpeza_disco.etapa_compile_cache)
        self.assertFalse(velho.exists())
        self.assertTrue(lido.exists() and (self.cache_node / 'atalho').is_symlink())
        self.assertEqual(sum(i['arquivos'] for i in r['itens']), 1)
        with mock.patch.object(limpeza_disco, 'COMPILE_CACHE', Path(self.tmp.name).parent / 'fora-do-tmp'):
            self.assertIn('nada apagado', self.etapa(limpeza_disco.etapa_compile_cache)['erro'])

    # --- (e) store do pnpm

    def test_store_do_pnpm_so_perde_arquivo_com_um_link(self):
        orfao = self.arquivo(self.store / 'v11/files/ab/orfao', horas=30)
        usado = self.arquivo(self.store / 'v11/files/cd/usado', horas=30)
        indice = self.arquivo(self.store / 'v11/index/ab.json', horas=30)
        em_projeto = self.projetos / 'loja/node_modules/p/index.js'
        em_projeto.parent.mkdir(parents=True)
        os.link(usado, em_projeto)
        agora_mesmo = self.depois - 7 * self.HORAS + 60  # o ctime dos arquivos é de um minuto atrás
        self.assertEqual(self.etapa(limpeza_disco.etapa_pnpm, agora=agora_mesmo)['itens'], [])
        self.assertTrue(orfao.exists())
        self.assertEqual(self.etapa(limpeza_disco.etapa_pnpm, simula=True)['estimado_mb'], 0)
        self.assertTrue(orfao.exists())
        r = self.etapa(limpeza_disco.etapa_pnpm)
        self.assertFalse(orfao.exists())
        self.assertTrue(usado.exists() and em_projeto.exists() and indice.exists())
        self.assertEqual(r['itens'], [{'pasta': str(self.store / 'v11/files/ab'), 'arquivos': 1, 'mb': 0.0}])

    def test_store_do_pnpm_com_instalacao_rodando_pula(self):
        orfao = self.arquivo(self.store / 'v11/files/ab/orfao', horas=30)
        self.processo(62, ['node', '/x/corepack/pnpm.cjs', 'install'])
        r = self.etapa(limpeza_disco.etapa_pnpm)
        self.assertIn('pulado: gerenciador de pacotes rodando', r['detalhe'])
        self.assertTrue(orfao.exists())

    # --- caminhos proibidos

    def test_caminho_proibido_e_recusado_mesmo_passado_por_engano(self):
        raiz = Path(self.tmp.name)
        proibida = raiz / '.openclaw'
        repo = proibida / 'workspace'
        with mock.patch.object(limpeza_disco, 'PROJETOS', proibida), mock.patch.object(limpeza_disco, 'NPX', proibida / 'npx'), \
                mock.patch.object(limpeza_disco, 'CLAUDE_LOGS', raiz / 'backups'), \
                mock.patch.object(limpeza_disco, 'PNPM_STORE', raiz / '.agents/store'):
            self.projetos, self.npx, self.logs, self.store = proibida, proibida / 'npx', raiz / 'backups', raiz / '.agents/store'
            repo = self.repo('workspace')
            self.cheio()
            entrada = self.arquivo(self.npx / 'aaaa/x', horas=40)
            dump = self.arquivo(self.logs / 'mcp-logs-x/gapp.dump', horas=40)
            do_store = self.arquivo(self.store / 'v11/files/ab/x', horas=40)
            resultados = [self.etapa(e) for e in (
                limpeza_disco.etapa_next, limpeza_disco.etapa_node_modules, limpeza_disco.etapa_npx,
                limpeza_disco.etapa_claude_logs, limpeza_disco.etapa_pnpm)]
        for fica in (repo / '.next/server/pagina.js', repo / 'node_modules/pacote/index.js', entrada, dump, do_store):
            self.assertTrue(fica.exists(), fica)
        self.assertFalse(any(r['feito'] for r in resultados))
        for r in resultados[:3]:
            self.assertTrue(all(m['motivo'].startswith('caminho proibido') for m in r['mantidos']) and r['mantidos'])
        for r in resultados[3:]:
            self.assertIn('caminho proibido', r['erro'])

    def test_recusa_barra_proibido_guardado_link_e_fora_da_raiz(self):
        raiz = Path(self.tmp.name)
        boa = self.arquivo(self.npx / 'aaaa/x').parent
        self.assertIsNone(limpeza_disco.recusa(boa, self.npx))
        self.assertIn('fora de', limpeza_disco.recusa(self.npx, self.npx))            # a própria raiz nunca sai
        self.assertIn('fora de', limpeza_disco.recusa(boa, self.projetos))
        self.assertIn('caminho proibido', limpeza_disco.recusa(raiz, raiz.parent))   # contém pasta proibida
        self.assertIn('caminho proibido', limpeza_disco.recusa(raiz / 'docker/volumes/pgdata', raiz))
        self.assertIn('guardado', limpeza_disco.recusa(self.arquivo(self.npx / 'Caddyfile.antes-t12/x').parent, self.npx))
        os.symlink(raiz / 'postgresql', self.npx / 'banco')
        self.assertIn('caminho proibido', limpeza_disco.recusa(self.npx / 'banco', self.npx))
        os.symlink(boa, self.npx / 'atalho')
        self.assertIn('link simbólico', limpeza_disco.recusa(self.npx / 'atalho', self.npx))
        self.assertIn('link simbólico', limpeza_disco.recusa(self.npx / 'atalho/x', self.npx))
        with mock.patch.object(limpeza_disco.os, 'getuid', return_value=os.getuid() + 1):
            self.assertEqual(limpeza_disco.recusa(boa, self.npx), 'de outro usuário')

    def test_proibidos_e_comandos_de_producao(self):
        self.addCleanup(mock.patch.stopall)
        mock.patch.stopall()  # desfaz as trocas do setUp: valem as constantes do código
        casa = Path.home()
        self.assertEqual(set(limpeza_disco.PROIBIDOS), {'/opt/backups', '/var/lib/docker', '/var/lib/postgresql',
                                                        str(casa / '.openclaw'), str(casa / '.agents')})
        for caminho, raiz in (('/opt/backups/diario/gapp.dump', '/opt'), ('/var/lib/docker/volumes/pg/_data', '/var/lib'),
                              ('/var/lib/postgresql/18/main', '/var'), (casa / '.openclaw/tmp/x', casa),
                              (casa / '.agents/claude/scratch', casa), (casa, '/home'), ('/opt', '/'),
                              (casa / 'projetos/infra/Caddyfile.antes-t276', casa / 'projetos')):
            self.assertIsNotNone(limpeza_disco.recusa(caminho, raiz), caminho)
        self.assertEqual(limpeza_disco.DOCKER_MANTER, ('postgres:18-alpine',))
        self.assertEqual(limpeza_disco.DOCKER_PERMITIDO, (('images',), ('ps',), ('inspect',), ('image', 'inspect'), ('rmi',)))
        self.assertEqual((limpeza_disco.NODE_MODULES_PCT, limpeza_disco.NODE_MODULES_HORAS, limpeza_disco.NPX_HORAS,
                          limpeza_disco.LOGS_HORAS), (80, 24, 12, 24))
        self.assertEqual([n for n, _ in limpeza_disco.ETAPAS][-1], 'node_modules')  # a única cara de refazer fica por último


class PublicaRotinas(unittest.TestCase):
    """O publicador só troca o link `atual` quando a `main` da origem está íntegra."""
    SCRIPT = Path(__file__).resolve().parents[1] / 'rotinas-openclaw' / 'publica_rotinas.sh'
    TESTES = 'tests/test_rotinas_openclaw.py'
    TESTE_BOM = 'import unittest\n\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        raiz = Path(self.tmp.name)
        self.origem, self.base = raiz / 'origem', raiz / 'publicado'
        self.estado_da_suite = raiz / 'estado' / 'suite-main'
        self.git('init', '--quiet', '-b', 'main', str(self.origem), cwd=raiz)
        self.escreve(self.TESTES, self.TESTE_BOM)
        self.commita('rotina.py', 'print("v1")\n')

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args, cwd=None):
        return subprocess.run(
            ['git', '-c', 'user.name=t', '-c', 'user.email=t@t', '-c', 'commit.gpgsign=false', *args],
            cwd=cwd or self.origem, check=True, capture_output=True, text=True).stdout.strip()

    def escreve(self, caminho, texto):
        arquivo = self.origem / caminho
        arquivo.parent.mkdir(exist_ok=True)
        arquivo.write_text(texto)

    def commita(self, nome, texto):
        self.escreve(f'rotinas-openclaw/{nome}', texto)
        self.git('add', '-A')
        self.git('commit', '--quiet', '-m', nome)
        return self.git('rev-parse', 'HEAD')

    def publica(self, origem=None, **extra):
        env = {**os.environ, 'ROTINAS_PUBLICADO': str(self.base), 'ROTINAS_ORIGEM': str(origem or self.origem),
               'ROTINAS_SUITE_ESTADO': str(self.estado_da_suite), **extra}
        return subprocess.run(['bash', str(self.SCRIPT)], env=env, capture_output=True, text=True)

    def confere(self, *args, **extra):
        env = {**os.environ, 'ROTINAS_PUBLICADO': str(self.base),
               'ROTINAS_SUITE_ESTADO': str(self.estado_da_suite), **extra}
        return subprocess.run([sys.executable, str(self.SCRIPT.with_name('suite_da_main.py')), *args],
                              env=env, capture_output=True, text=True)

    def com_o_conferidor(self):
        """Põe na origem o suite_da_main.py de verdade (as outras origens de teste não têm)."""
        return self.commita('suite_da_main.py', self.SCRIPT.with_name('suite_da_main.py').read_text())

    def suite_guardada(self):
        return json.loads(self.estado_da_suite.read_text())

    def atual(self):
        return (self.base / 'atual' / 'rotinas-openclaw' / 'rotina.py').read_text()

    def test_publica_o_commit_da_origem_e_nao_repete(self):
        commit = self.git('rev-parse', 'HEAD')
        r = self.publica()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('"publicado":true', r.stdout)
        self.assertEqual(os.readlink(self.base / 'atual'), f'releases/{commit}')
        self.assertEqual(self.atual(), 'print("v1")\n')
        self.assertIn('"publicado":false', self.publica().stdout)

    def test_arquivo_sem_commit_na_origem_nao_e_publicado(self):
        self.publica()
        (self.origem / 'rotinas-openclaw' / 'rotina.py').write_text('print("sujo")\n')
        self.assertIn('"publicado":false', self.publica().stdout)
        self.assertEqual(self.atual(), 'print("v1")\n')

    def test_erro_de_sintaxe_mantem_a_versao_anterior(self):
        self.publica()
        antes = os.readlink(self.base / 'atual')
        self.commita('rotina.py', 'def quebrado(:\n')
        r = self.publica()
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(os.readlink(self.base / 'atual'), antes)
        self.assertEqual(self.atual(), 'print("v1")\n')
        self.commita('rotina.py', 'print("v3")\n')
        self.assertEqual(self.publica().returncode, 0)
        self.assertEqual(self.atual(), 'print("v3")\n')

    def test_origem_fora_do_ar_mantem_a_versao_anterior(self):
        self.publica()
        antes = os.readlink(self.base / 'atual')
        r = self.publica(origem=Path(self.tmp.name) / 'nao-existe')
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(os.readlink(self.base / 'atual'), antes)

    def test_commit_sem_a_pasta_mantem_a_versao_anterior(self):
        self.publica()
        antes = os.readlink(self.base / 'atual')
        self.git('rm', '-r', '--quiet', 'rotinas-openclaw')
        self.git('commit', '--quiet', '-m', 'sem pasta')
        self.assertNotEqual(self.publica().returncode, 0)
        self.assertEqual(os.readlink(self.base / 'atual'), antes)

    def test_teste_que_falha_mantem_a_versao_anterior(self):
        self.publica()
        antes = os.readlink(self.base / 'atual')
        self.escreve(self.TESTES, self.TESTE_BOM.replace('pass', 'self.fail("regra quebrada")'))
        ruim = self.commita('rotina.py', 'print("v2")\n')
        r = self.publica()
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertIn('regra quebrada', r.stderr)
        self.assertEqual(os.readlink(self.base / 'atual'), antes)
        self.assertEqual(self.atual(), 'print("v1")\n')
        self.assertFalse((self.base / 'releases' / ruim).exists())
        self.assertEqual(self.publica().returncode, 4)  # não passa na segunda tentativa
        self.escreve(self.TESTES, self.TESTE_BOM)
        self.commita('rotina.py', 'print("v3")\n')
        self.assertEqual(self.publica().returncode, 0)
        self.assertEqual(self.atual(), 'print("v3")\n')

    def test_commit_sem_os_testes_mantem_a_versao_anterior(self):
        self.publica()
        antes = os.readlink(self.base / 'atual')
        self.git('rm', '--quiet', self.TESTES)
        self.commita('rotina.py', 'print("v2")\n')
        self.assertNotEqual(self.publica().returncode, 0)
        self.assertEqual(os.readlink(self.base / 'atual'), antes)

    def test_versao_publicada_fica_sem_escrita_e_sem_pycache(self):
        self.escreve(self.TESTES, 'import sys\nfrom pathlib import Path\n'
                     'sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rotinas-openclaw"))\n'
                     'import rotina  # noqa: E402,F401\n' + self.TESTE_BOM)
        commit = self.commita('rotina.py', 'print("v1")\n')
        self.assertEqual(self.publica().returncode, 0)
        versao = self.base / 'releases' / commit
        com_escrita = [str(p) for p in (versao, *versao.rglob('*')) if p.stat().st_mode & 0o222]
        self.assertEqual(com_escrita, [])
        self.assertEqual(list(versao.rglob('__pycache__')), [])
        self.assertEqual(self.atual(), 'print("v1")\n')

    def test_sobra_sem_escrita_de_execucao_interrompida_e_apagada(self):
        # interrompido entre tirar a escrita e dar o nome final: sobra um .parcial-* sem escrita
        sobra = self.base / 'releases' / '.parcial-abc123' / 'rotinas-openclaw'
        sobra.mkdir(parents=True)
        (sobra / 'rotina.py').write_text('print("pela metade")\n')
        subprocess.run(['chmod', '-R', 'a-w', str(sobra.parent)], check=True)
        r = self.publica()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([p.name for p in (self.base / 'releases').iterdir()], [self.git('rev-parse', 'HEAD')])
        self.assertEqual(self.atual(), 'print("v1")\n')

    def test_guarda_a_atual_e_a_anterior(self):
        commits = []
        for n in range(6):
            commits.append(self.commita('rotina.py', f'print({n})\n'))
            self.assertEqual(self.publica().returncode, 0)
        guardadas = {p.name for p in (self.base / 'releases').iterdir()}
        self.assertIn(commits[-1], guardadas)
        self.assertIn(commits[-2], guardadas)
        self.assertLessEqual(len(guardadas), 3)  # as sem escrita também saem na limpeza

    # Conferência diária da suíte do commit publicado (suite_da_main.py, chamado pelo publicador)

    def test_publicador_confere_a_suite_do_commit_publicado_sem_mudar_o_resumo(self):
        commit = self.com_o_conferidor()
        temporarios = Path(self.tmp.name) / 'tmp'  # a pasta da suíte extraída não pode sobrar
        temporarios.mkdir()
        r = self.publica(TMPDIR=str(temporarios))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.count('\n'), 1)  # o resumo do job continua uma linha só
        self.assertIn(f'"publicado":true,"commit":"{commit}"', r.stdout)
        guardada = self.suite_guardada()
        self.assertEqual((guardada['commit'], guardada['saida'], guardada['testes']), (commit, 0, 1))
        self.assertEqual(list(temporarios.iterdir()), [])

    def test_origem_sem_o_conferidor_publica_do_mesmo_jeito(self):
        self.assertEqual(self.publica().returncode, 0)
        self.assertFalse(self.estado_da_suite.exists())

    def test_suite_so_roda_de_novo_com_commit_novo_ou_passado_o_intervalo(self):
        self.com_o_conferidor()
        self.publica()
        primeira = self.suite_guardada()
        self.assertIn('"publicado":false', self.publica().stdout)
        self.assertEqual(self.suite_guardada(), primeira)  # mesmo commit, dentro das 24 h: não roda

        velha = {**primeira, 'quando': (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(timespec='seconds')}
        self.estado_da_suite.write_text(json.dumps(velha))
        self.publica()
        self.assertGreater(self.suite_guardada()['quando'], velha['quando'])

        novo = self.commita('rotina.py', 'print("v2")\n')
        self.publica()
        self.assertEqual(self.suite_guardada()['commit'], novo)

    def test_conferencia_com_falha_e_repetida_em_1_h_e_nao_em_24_h(self):
        agora = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)

        def e_hora(saida, minutos):
            estado = {'commit': 'a' * 40, 'saida': saida, 'quando': agora - timedelta(minutes=minutos)}
            return suite_da_main.e_hora(estado, 'a' * 40, agora)
        self.assertEqual((suite_da_main.REPETE_H, suite_da_main.INTERVALO_H), (1, 24))
        self.assertFalse(e_hora(1, 59))
        self.assertTrue(e_hora(1, 60))
        self.assertTrue(e_hora(124, 61))  # interrompida pelo limite também é falha
        self.assertFalse(e_hora(0, 61))
        self.assertFalse(e_hora(0, 23 * 60 + 59))
        self.assertTrue(e_hora(0, 24 * 60))

        # De ponta a ponta: falha do momento (a suíte de agora passa) some na rodada depois de 1 h.
        self.com_o_conferidor()
        self.publica()
        boa = self.suite_guardada()
        for minutos, roda in ((30, False), (61, True)):
            ruim = {**boa, 'saida': 124, 'estourou': True, 'quando':
                    (datetime.now(timezone.utc) - timedelta(minutes=minutos)).isoformat(timespec='seconds')}
            self.estado_da_suite.write_text(json.dumps(ruim))
            self.publica()
            self.assertEqual(self.suite_guardada()['saida'], 0 if roda else 124, minutos)

    def test_teste_fora_das_rotinas_que_falha_fica_no_estado_e_nao_derruba_o_publicador(self):
        # O publicador só roda tests/test_rotinas_openclaw.py: o resto da suíte só a conferência vê.
        self.escreve('tests/test_outro.py', self.TESTE_BOM.replace('pass', 'self.fail("venceu com o relogio")'))
        commit = self.com_o_conferidor()
        r = self.publica()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(os.readlink(self.base / 'atual'), f'releases/{commit}')
        guardada = self.suite_guardada()
        self.assertEqual((guardada['saida'], guardada['testes']), (1, 2))
        self.assertEqual(len(guardada['falhas']), 1)
        self.assertIn('FAIL: test_ok', guardada['falhas'][0])
        with mock.patch.object(vigia_saude, 'SUITE_ESTADO', self.estado_da_suite):
            ruim, detalhe = vigia_saude.suite(vigia_saude.estado_da_suite(), 1.0, datetime.now(timezone.utc))
        self.assertTrue(ruim)
        self.assertIn('test_outro', detalhe)

    def test_conferencia_a_mao_forca_simula_e_devolve_o_codigo(self):
        self.com_o_conferidor()
        self.publica()
        antes = self.estado_da_suite.read_text()
        r = self.confere()
        self.assertEqual((r.returncode, json.loads(r.stdout)['conferiu']), (0, False))
        self.escreve('tests/test_outro.py', self.TESTE_BOM.replace('pass', 'self.fail("venceu")'))
        self.commita('rotina.py', 'print("v2")\n')
        self.publica(ROTINAS_SUITE_ESTADO=str(self.estado_da_suite) + '.outro')  # publica sem tocar no estado
        r = self.confere('--simula')
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn('venceu', r.stderr)
        self.assertTrue(json.loads(r.stdout)['simulacao'])
        self.assertEqual(self.estado_da_suite.read_text(), antes)  # simulação não grava
        self.assertEqual(self.confere('--forca').returncode, 1)
        self.assertEqual(self.suite_guardada()['saida'], 1)

    def test_suite_que_passa_do_limite_e_interrompida_e_fica_como_falha(self):
        self.escreve('tests/test_lento.py', 'import time\n' + self.TESTE_BOM.replace('pass', 'time.sleep(30)'))
        self.com_o_conferidor()
        self.publica(ROTINAS_SUITE_LIMITE_S='1')
        guardada = self.suite_guardada()
        self.assertEqual((guardada['saida'], guardada['estourou']), (124, True))

    def test_conferencia_morta_por_sinal_apaga_a_pasta_mata_a_suite_e_nao_grava(self):
        # O job morto no limite de tempo deixava suite-main-* (1,5 MB) em /tmp a cada rodada.
        temporarios, pid = Path(self.tmp.name) / 'tmp', Path(self.tmp.name) / 'pid-da-suite'
        temporarios.mkdir()
        self.escreve('tests/test_lento.py', 'import os\nimport time\nfrom pathlib import Path\n' + self.TESTE_BOM.replace(
            'pass', 'Path(os.environ["PID_DA_SUITE"]).write_text(str(os.getpid()))\n        time.sleep(60)'))
        self.com_o_conferidor()
        self.publica(ROTINAS_SUITE_ESTADO=str(self.estado_da_suite) + '.outro', ROTINAS_SUITE_LIMITE_S='1')
        for sinal in (signal.SIGTERM, signal.SIGHUP):
            pid.unlink(missing_ok=True)
            p = subprocess.Popen(
                [sys.executable, str(self.SCRIPT.with_name('suite_da_main.py')), '--forca'],
                env={**os.environ, 'ROTINAS_PUBLICADO': str(self.base), 'TMPDIR': str(temporarios),
                     'ROTINAS_SUITE_ESTADO': str(self.estado_da_suite), 'PID_DA_SUITE': str(pid)},
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                prazo = time.monotonic() + 30
                while not (pid.exists() and pid.read_text()) and time.monotonic() < prazo:
                    time.sleep(0.05)
                self.assertTrue(pid.exists(), 'a suíte não começou')
                self.assertEqual([s.name[:11] for s in temporarios.iterdir()], ['suite-main-'])
                p.send_signal(sinal)
                self.assertEqual(p.wait(timeout=20), 128 + sinal)
            finally:
                p.kill()
                p.wait()
            self.assertEqual(list(temporarios.iterdir()), [])
            self.assertFalse(self.estado_da_suite.exists())
            with self.assertRaises(ProcessLookupError):  # a suíte não fica órfã rodando
                os.kill(int(pid.read_text()), 0)

    def test_sobra_de_conferencia_com_mais_de_1_h_e_apagada_no_comeco(self):
        temporarios = Path(self.tmp.name) / 'tmp'
        velha, nova, alheia = (temporarios / n for n in ('suite-main-velha', 'suite-main-nova', 'outra-velha'))
        ha_2_h = time.time() - 2 * 3600
        for pasta in (velha, nova, alheia):
            (pasta / 'codigo').mkdir(parents=True)
            (pasta / 'codigo' / 'a.py').write_text('x\n')
            subprocess.run(['chmod', '-R', 'a-w', str(pasta)], check=True)  # como sobra de versão publicada
            if pasta != nova:
                os.utime(pasta, (ha_2_h, ha_2_h))
        self.com_o_conferidor()
        r = self.publica(TMPDIR=str(temporarios))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.suite_guardada()['saida'], 0)
        self.assertEqual(sorted(p.name for p in temporarios.iterdir()), ['outra-velha', 'suite-main-nova'])

    def test_temporarios_dos_testes_ficam_na_pasta_da_conferencia_e_somem_com_ela(self):
        temporarios = Path(self.tmp.name) / 'tmp'
        temporarios.mkdir()
        self.escreve('tests/test_sujo.py', 'import tempfile\n' + self.TESTE_BOM.replace(
            'pass', 'self.assertIn("suite-main-", tempfile.mkdtemp())  # e deixa a pasta para trás'))
        self.com_o_conferidor()
        self.publica(TMPDIR=str(temporarios))
        guardada = self.suite_guardada()
        self.assertEqual((guardada['saida'], guardada['testes']), (0, 2), guardada)
        self.assertEqual(list(temporarios.iterdir()), [])

    def test_estado_e_gravado_por_temporario_de_nome_proprio_que_nao_sobra(self):
        # O nome fixo (suite-main.novo) embaralhava duas conferências ao mesmo tempo.
        arquivo = self.estado_da_suite
        nomes = []
        de_verdade = os.replace
        with mock.patch.object(suite_da_main.os, 'replace',
                               side_effect=lambda de, para: (nomes.append(Path(de).name), de_verdade(de, para))):
            suite_da_main.guarda(arquivo, {'saida': 0, 'n': 1})
            suite_da_main.guarda(arquivo, {'saida': 0, 'n': 2})
        self.assertEqual(len(set(nomes)), 2)
        self.assertEqual(json.loads(arquivo.read_text())['n'], 2)
        self.assertEqual(arquivo.stat().st_mode & 0o777, 0o644)
        with mock.patch.object(suite_da_main.os, 'replace', side_effect=OSError('disco cheio')), \
                self.assertRaises(OSError):
            suite_da_main.guarda(arquivo, {'saida': 1, 'n': 3})
        self.assertEqual(json.loads(arquivo.read_text())['n'], 2)  # o estado antigo fica
        self.assertEqual([p.name for p in arquivo.parent.iterdir()], ['suite-main'])

    # A conferência cabe no que sobra do job (120 s), ou fica para a rodada seguinte

    def test_limites_da_conferencia_cabem_no_job_do_publicador(self):
        script = self.SCRIPT.read_text()
        self.assertIn('JOB_LIMITE_S="${ROTINAS_JOB_LIMITE_S:-120}"', script)
        self.assertIn('FOLGA_S=10 ', script)
        self.assertIn('timeout 10 git -C "$repo" archive "$commit" "$PASTA" "$TESTES" | timeout 10 tar', script)
        self.assertEqual(suite_da_main.LIMITE_EXTRAI_S, 10)
        # rodada sem publicação: extrair (archive + tar) e a suíte no limite acabam antes do teto
        self.assertLessEqual(2 * suite_da_main.LIMITE_EXTRAI_S + suite_da_main.LIMITE_S, 120 - 10 - 5)

    def test_sem_tempo_sobrando_no_job_a_conferencia_fica_para_a_rodada_seguinte(self):
        commit = self.com_o_conferidor()
        r = self.publica(ROTINAS_JOB_LIMITE_S='30')  # sobram 20 s, menos que o mínimo de 30
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f'"publicado":true,"commit":"{commit}"', r.stdout)
        self.assertFalse(self.estado_da_suite.exists())
        self.assertIn('"publicado":false', self.publica().stdout)  # na seguinte, com tempo, confere
        self.assertEqual(self.suite_guardada()['commit'], commit)

    def test_conferencia_que_passa_do_teto_do_job_e_interrompida_sem_mudar_o_resumo(self):
        temporarios = Path(self.tmp.name) / 'tmp'
        temporarios.mkdir()
        self.escreve('tests/test_lento.py', 'import time\n' + self.TESTE_BOM.replace('pass', 'time.sleep(60)'))
        commit = self.com_o_conferidor()
        inicio = time.monotonic()
        r = self.publica(TMPDIR=str(temporarios), ROTINAS_JOB_LIMITE_S='13', ROTINAS_CONFERE_MIN_S='1')
        self.assertLess(time.monotonic() - inicio, 13)  # teto de 3 s, bem antes do limite da suíte (75 s)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.count('\n'), 1)
        self.assertIn(f'"publicado":true,"commit":"{commit}"', r.stdout)
        self.assertFalse(self.estado_da_suite.exists())  # sem estado: a rodada seguinte tenta de novo
        self.assertEqual(list(temporarios.iterdir()), [])

    def test_sem_versao_publicada_ou_sem_o_commit_nao_grava_estado(self):
        r = self.confere()
        self.assertEqual(r.returncode, 2)
        self.assertIn('sem versão publicada', r.stdout)
        self.com_o_conferidor()
        self.publica(ROTINAS_SUITE_ESTADO=str(self.estado_da_suite) + '.outro')
        os.remove(self.base / 'atual')
        os.symlink('releases/' + 'c' * 40, self.base / 'atual')  # commit que o repo.git não tem
        r = self.confere()
        self.assertEqual(r.returncode, 2)
        self.assertFalse(self.estado_da_suite.exists())


if __name__ == '__main__':
    unittest.main()
