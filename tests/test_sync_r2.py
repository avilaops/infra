import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'applications/sync-r2.sh'


class SyncR2(unittest.TestCase):
    """Cópia versionada do envio de backups ao R2 (applications/README.md)."""

    def test_sintaxe(self):
        r = subprocess.run(['bash', '-n', str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_so_copia_nunca_apaga_no_destino(self):
        codigo = '\n'.join(l for l in SCRIPT.read_text().splitlines() if not l.lstrip().startswith('#'))
        comandos = set(re.findall(r'\brclone (\w+)', codigo))
        self.assertEqual(comandos, {'copy', 'listremotes', 'size'})

    def test_pre_migracao_fica_fora_do_envio(self):
        # rclone de mentira; os caminhos fixos do script viram diretorios temporarios.
        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            (raiz / 'bin').mkdir()
            (raiz / 'db').mkdir()
            rclone = raiz / 'bin/rclone'
            rclone.write_text('#!/bin/bash\n'
                              'case "$1" in\n'
                              ' listremotes) echo r2: ;;\n'
                              ' size) echo "{}" ;;\n'
                              ' *) printf "%s\\n" "$@" > "$TEST_ROOT/args-$(basename "$2")" ;;\n'
                              'esac\n')
            rclone.chmod(0o755)
            copia = raiz / 'sync-r2.sh'
            copia.write_text(SCRIPT.read_text()
                             .replace('/opt/backups/db', str(raiz / 'db'))
                             .replace('/var/log/avila-r2.log', str(raiz / 'log'))
                             .replace('/var/backups/cliente_portal', str(raiz / 'ausente')))
            r = subprocess.run(['bash', str(copia)], capture_output=True, text=True,
                               env=dict(os.environ, PATH=str(raiz / 'bin') + ':' + os.environ['PATH'], TEST_ROOT=tmp))
            self.assertEqual(r.returncode, 0, r.stderr)
            args = (raiz / 'args-db').read_text().splitlines()
            self.assertEqual(args[:3], ['copy', str(raiz / 'db'), 'r2:avilaops-backups/db'])
            filtros = [args[i + 1] for i, a in enumerate(args) if a == '--filter']
            # A primeira regra que casa decide: a exclusao tem de vir antes do "+ *.sql.gz".
            self.assertEqual(filtros, ['- pre-migracao-*', '+ *.sql.gz', '+ *.tar.gz', '- *'])
            # --include/--exclude junto com --filter muda a ordem de avaliacao no rclone.
            self.assertFalse([a for a in args if a.startswith(('--include', '--exclude'))])


if __name__ == '__main__':
    unittest.main()
