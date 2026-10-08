import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/build-pesado.sh'
INSTALADOR = SCRIPT.with_name('instalar-build-pesado.sh')


def vivo(pid):
    try:
        estado = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return estado != 'Z'


class BuildPesado(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = {k: v for k, v in os.environ.items()
                    if k != 'NODE_OPTIONS' and not k.startswith('BUILD_PESADO_')}
        self.env['BUILD_PESADO_LOCK'] = str(self.root / 'build.lock')

    def run_script(self, *args, **env):
        return subprocess.run(['bash', str(SCRIPT), *args], env=dict(self.env, **env),
                              text=True, capture_output=True, timeout=60)

    def segurar_trava(self, segundos):
        processo = subprocess.Popen(['bash', str(SCRIPT), 'sleep', str(segundos)], env=self.env)
        self.addCleanup(processo.wait)
        self.addCleanup(processo.terminate)
        dono = self.root / 'build.lock.dono'
        for _ in range(50):
            if dono.exists():
                return processo
            time.sleep(0.1)
        self.fail('o primeiro disparo nao pegou a trava')

    def test_sintaxe(self):
        self.assertEqual(subprocess.run(['bash', '-n', str(SCRIPT)]).returncode, 0)

    def test_segundo_disparo_espera_o_primeiro(self):
        marca = self.root / 'ordem.log'
        primeiro = subprocess.Popen(
            ['bash', str(SCRIPT), 'bash', '-c', f'echo inicio-a >> {marca}; sleep 2; echo fim-a >> {marca}'],
            env=self.env)
        for _ in range(50):
            if marca.exists():
                break
            time.sleep(0.1)
        segundo = self.run_script('bash', '-c', f'echo inicio-b >> {marca}')
        primeiro.wait()
        self.assertEqual(segundo.returncode, 0, segundo.stderr)
        self.assertEqual(marca.read_text().split(), ['inicio-a', 'fim-a', 'inicio-b'])
        self.assertIn('Aguardando a trava', segundo.stderr)
        self.assertIn('trava liberada', segundo.stderr)

    def test_desiste_quando_a_espera_estoura(self):
        self.segurar_trava(5)
        marca = self.root / 'rodou'
        resultado = self.run_script('touch', str(marca), BUILD_PESADO_ESPERA_S='1')
        self.assertEqual(resultado.returncode, 75)
        self.assertIn('desisti', resultado.stderr)
        self.assertFalse(marca.exists())

    def test_teto_de_heap(self):
        mostra = ('bash', '-c', 'printf %s "$NODE_OPTIONS"')
        self.assertEqual(self.run_script(*mostra).stdout, '--max-old-space-size=1024')
        self.assertEqual(self.run_script(*mostra, BUILD_PESADO_HEAP_MB='768').stdout,
                         '--max-old-space-size=768')
        self.assertEqual(self.run_script(*mostra, NODE_OPTIONS='--enable-source-maps').stdout,
                         '--enable-source-maps --max-old-space-size=1024')
        self.assertEqual(self.run_script(*mostra, NODE_OPTIONS='--max-old-space-size=2048').stdout,
                         '--max-old-space-size=2048')

    def test_devolve_a_saida_do_comando_e_solta_a_trava(self):
        self.assertEqual(self.run_script('bash', '-c', 'exit 7').returncode, 7)
        self.assertEqual(self.run_script('true', BUILD_PESADO_ESPERA_S='0').returncode, 0)

    def test_chamada_aninhada_nao_espera_por_si_mesma(self):
        resultado = self.run_script('bash', str(SCRIPT), 'echo', 'dentro', BUILD_PESADO_ESPERA_S='1')
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.assertEqual(resultado.stdout.strip(), 'dentro')

    def test_uso_invalido(self):
        self.assertEqual(self.run_script().returncode, 64)
        self.assertEqual(self.run_script('true', BUILD_PESADO_ESPERA_S='abc').returncode, 64)

    # Ressalvas da revisao da tarefa 239 (tarefa 245).

    def trava_livre(self):
        return subprocess.run(['flock', '-n', self.env['BUILD_PESADO_LOCK'], 'true']).returncode == 0

    def disparar_com_neto(self, comando, **env):
        """Sobe o wrapper com um shell que deixa um `sleep` como neto; devolve (wrapper, pid do neto)."""
        arquivo = self.root / 'neto.pid'
        wrapper = subprocess.Popen(
            ['bash', str(SCRIPT), 'bash', '-c', comando, '_', str(arquivo)],
            env=dict(self.env, **env), stderr=subprocess.PIPE, text=True)

        def limpar():
            if arquivo.exists() and arquivo.read_text().strip():
                try:
                    os.kill(int(arquivo.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if wrapper.poll() is None:
                wrapper.kill()
            wrapper.communicate()
        self.addCleanup(limpar)
        for _ in range(50):
            if arquivo.exists() and arquivo.read_text().strip():
                return wrapper, int(arquivo.read_text())
            time.sleep(0.1)
        self.fail('o comando nao subiu')

    def test_ressalva1_term_alcanca_o_neto_antes_de_soltar_a_trava(self):
        wrapper, neto = self.disparar_com_neto('sleep 30 & echo $! > "$1"; wait')
        self.assertFalse(self.trava_livre())
        wrapper.send_signal(signal.SIGTERM)
        self.assertEqual(wrapper.wait(timeout=20), 143)
        self.assertFalse(vivo(neto), 'o neto sobreviveu ao TERM no wrapper')
        self.assertTrue(self.trava_livre())
        self.assertFalse((self.root / 'build.lock.dono').exists())

    def test_ressalva1_trava_fica_presa_enquanto_o_grupo_nao_esvazia(self):
        # O shell morre com o TERM, o neto ignora: a trava espera o neto, que so
        # cai com o KILL do fim da carencia.
        wrapper, neto = self.disparar_com_neto(
            '(trap "" TERM; exec sleep 30) & echo $! > "$1"; wait', BUILD_PESADO_CARENCIA_S='2')
        time.sleep(0.3)
        wrapper.send_signal(signal.SIGTERM)
        time.sleep(0.8)
        self.assertTrue(vivo(neto))
        self.assertIsNone(wrapper.poll(), 'o wrapper saiu com o grupo ainda vivo')
        self.assertFalse(self.trava_livre())
        self.assertEqual(wrapper.wait(timeout=20), 143)
        self.assertFalse(vivo(neto))
        self.assertIn('Enviando KILL', wrapper.stderr.read())
        self.assertTrue(self.trava_livre())

    def test_ressalva1_fim_normal_nao_espera_processo_deixado_para_tras(self):
        wrapper, neto = self.disparar_com_neto('sleep 4 & echo $! > "$1"')
        self.assertEqual(wrapper.wait(timeout=20), 0)
        self.assertTrue(vivo(neto))
        self.assertTrue(self.trava_livre())

    def test_ressalva2_hup_int_e_quit_tambem_encerram_o_grupo(self):
        for sinal in (signal.SIGHUP, signal.SIGINT, signal.SIGQUIT):
            with self.subTest(sinal=sinal.name):
                (self.root / 'neto.pid').unlink(missing_ok=True)
                wrapper, neto = self.disparar_com_neto('sleep 30 & echo $! > "$1"; wait')
                wrapper.send_signal(sinal)
                self.assertEqual(wrapper.wait(timeout=20), 128 + sinal)
                self.assertFalse(vivo(neto))
                self.assertTrue(self.trava_livre())

    def test_ressalva3_espera_com_zero_a_esquerda_nao_fura_a_trava(self):
        self.segurar_trava(5)
        marca = self.root / 'rodou'
        for valor in ('08', '09', '00', '0x10', '-1', '1 ', '9999999999'):
            with self.subTest(valor=valor):
                resultado = self.run_script('touch', str(marca), BUILD_PESADO_ESPERA_S=valor)
                self.assertEqual(resultado.returncode, 64, resultado.stderr)
                self.assertFalse(marca.exists())
        self.assertEqual(self.run_script('true', BUILD_PESADO_CARENCIA_S='08').returncode, 64)

    def test_ressalva4_variavel_herdada_nao_fura_a_trava(self):
        dono = self.segurar_trava(20)
        marca = self.root / 'rodou'
        trava = self.env['BUILD_PESADO_LOCK']
        # So a variavel antiga; pid de processo morto; pid vivo que nao e ancestral.
        morto = subprocess.Popen(['true'])
        morto.wait()
        for pid in (None, str(morto.pid), str(dono.pid), '1', 'abc'):
            with self.subTest(pid=pid):
                env = {'BUILD_PESADO_TRAVA': trava, 'BUILD_PESADO_ESPERA_S': '1'}
                if pid is not None:
                    env['BUILD_PESADO_TRAVA_PID'] = pid
                resultado = self.run_script('touch', str(marca), **env)
                self.assertEqual(resultado.returncode, 75, resultado.stderr)
                self.assertFalse(marca.exists())

    def test_ressalva4_aninhada_a_varios_niveis_continua_dispensada(self):
        resultado = self.run_script('bash', '-c', f'bash -c "bash {SCRIPT} echo fundo"',
                                    BUILD_PESADO_ESPERA_S='1')
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.assertEqual(resultado.stdout.strip(), 'fundo')

    def test_ressalva5_argumentos_nao_vao_para_o_dono_nem_para_quem_espera(self):
        segredo = 'TOKEN=valor-que-nao-pode-vazar'
        primeiro = subprocess.Popen(['bash', str(SCRIPT), 'bash', '-c', 'sleep 3', '_', segredo],
                                    env=self.env, cwd=self.root)
        self.addCleanup(primeiro.wait)
        self.addCleanup(primeiro.terminate)
        dono = self.root / 'build.lock.dono'
        for _ in range(50):
            if dono.exists() and dono.read_text():
                break
            time.sleep(0.1)
        texto = dono.read_text()
        self.assertNotIn('valor-que-nao-pode-vazar', texto)
        self.assertNotIn('sleep 3', texto)
        self.assertRegex(texto, rf'^pid {primeiro.pid}, desde .*: bash, em {self.root}\n$')
        segundo = self.run_script('true', BUILD_PESADO_ESPERA_S='0')
        self.assertEqual(segundo.returncode, 75)
        self.assertNotIn('valor-que-nao-pode-vazar', segundo.stderr)

    def test_comando_inexistente_e_stdin(self):
        self.assertEqual(self.run_script('comando-que-nao-existe-245').returncode, 127)
        eco = subprocess.run(['bash', str(SCRIPT), 'cat'], env=self.env, input='oi\n',
                             text=True, capture_output=True, timeout=60)
        self.assertEqual((eco.returncode, eco.stdout), (0, 'oi\n'))
        self.assertEqual(self.run_script('printf', '[%s]', 'a b', "c'd", '').stdout, "[a b][c'd][]")


class InstalarBuildPesado(unittest.TestCase):
    """Ressalva 7: ~/.local/bin recebe copia de commit enviado, nao link para o checkout."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        (self.repo / 'scripts').mkdir(parents=True)
        self.git('init', '-q', '-b', 'main')
        self.fonte = self.repo / 'scripts/build-pesado.sh'
        self.revisado = self.commit(SCRIPT.read_text())
        self.git('update-ref', 'refs/remotes/origin/main', self.revisado)
        self.destino = self.root / 'bin/build-pesado'
        self.destino.parent.mkdir()
        self.destino.symlink_to(self.fonte)

    def git(self, *args):
        return subprocess.run(
            ['git', '-C', str(self.repo), '-c', 'user.name=teste', '-c', 'user.email=teste@exemplo.invalid',
             '-c', 'commit.gpgsign=false', *args],
            check=True, text=True, capture_output=True).stdout.strip()

    def commit(self, conteudo):
        self.fonte.write_text(conteudo)
        self.git('add', 'scripts/build-pesado.sh')
        self.git('commit', '-q', '-m', 'versao')
        return self.git('rev-parse', 'HEAD')

    def instalar(self, *args):
        env = dict(os.environ, BUILD_PESADO_REPO=str(self.repo), BUILD_PESADO_DESTINO=str(self.destino))
        return subprocess.run(['bash', str(INSTALADOR), *args], env=env, text=True,
                              capture_output=True, timeout=60)

    def test_sintaxe(self):
        self.assertEqual(subprocess.run(['bash', '-n', str(INSTALADOR)]).returncode, 0)

    def test_troca_o_link_por_copia_do_commit_enviado(self):
        resultado = self.instalar()
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.assertFalse(self.destino.is_symlink())
        self.assertTrue(os.access(self.destino, os.X_OK))
        instalado = self.destino.read_text()
        self.assertTrue(instalado.startswith(SCRIPT.read_text()))
        self.assertIn(f'a partir do commit {self.revisado}.', instalado)
        self.assertEqual(self.fonte.read_text(), SCRIPT.read_text(), 'escreveu atraves do link')
        self.assertEqual([p.name for p in self.destino.parent.iterdir()], ['build-pesado'])
        # Edicao no checkout, com ou sem commit local, nao muda o que esta instalado.
        self.fonte.write_text('#!/bin/sh\nexit 99\n')
        self.assertEqual(self.destino.read_text(), instalado)
        rodou = subprocess.run([str(self.destino), 'echo', 'instalado'], text=True, capture_output=True,
                               env=dict(os.environ, BUILD_PESADO_LOCK=str(self.root / 'i.lock')))
        self.assertEqual((rodou.returncode, rodou.stdout.strip()), (0, 'instalado'), rodou.stderr)

    def test_recusa_commit_que_nao_esta_em_origin_main(self):
        local = self.commit('#!/bin/sh\nexit 99\n')
        for ref in (local, 'HEAD', 'main'):
            with self.subTest(ref=ref):
                resultado = self.instalar(ref)
                self.assertEqual(resultado.returncode, 65, resultado.stderr)
                self.assertTrue(self.destino.is_symlink())
        self.assertEqual(self.instalar('nao-existe').returncode, 64)
        self.assertEqual([p.name for p in self.destino.parent.iterdir()], ['build-pesado'])

    def test_instala_o_commit_pedido_e_nao_a_arvore_de_trabalho(self):
        self.commit('#!/bin/sh\nexit 99\n')
        resultado = self.instalar(self.revisado)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.assertTrue(self.destino.read_text().startswith(SCRIPT.read_text()))


if __name__ == '__main__':
    unittest.main()
