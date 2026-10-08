import subprocess
import unittest
from pathlib import Path

PASTA = Path(__file__).resolve().parents[1] / 'applications'


class RotacionarBackups(unittest.TestCase):
    """Cópia versionada do rotacionador de dumps do `applications` (applications/README.md)."""
    SCRIPT = PASTA / 'rotacionar-backups.sh'
    TESTE = PASTA / 'rotacionar-backups.teste.sh'

    def test_sintaxe(self):
        for arquivo in (self.SCRIPT, self.TESTE):
            r = subprocess.run(['bash', '-n', str(arquivo)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_regra_da_rotacao_em_diretorio_temporario(self):
        r = subprocess.run(['bash', str(self.TESTE), str(self.SCRIPT)], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('== 0 erro(s)', r.stdout)
        self.assertNotIn('ERRO', r.stdout)


if __name__ == '__main__':
    unittest.main()
