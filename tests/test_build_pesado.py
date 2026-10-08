import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/build-pesado.sh'


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


if __name__ == '__main__':
    unittest.main()
