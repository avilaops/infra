import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/deploy-container.sh'
SCRIPT_LOCAL = SCRIPT.with_name('deploy-container-local.sh')


class Migracao(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.work = self.root / 'work'
        self.work.mkdir()
        self.envfile = self.root / '.env.production'
        self.envfile.write_text('DB_HOST=host.docker.internal\nDATABASE_URL="postgresql://teste:teste@${DB_HOST}:5432/teste"\n')
        functions = SCRIPT.read_text().split('\n[[ $EUID == 0 ]]')[0]
        self.functions = self.root / 'functions.sh'
        self.functions.write_text(functions)
        self.env = dict(os.environ, PATH=str(self.bin) + ':' + os.environ['PATH'],
                        TMPDIR=str(self.work), TEST_ROOT=str(self.root))
        self.stub('docker', '''#!/bin/bash
set -eu
echo "$1" >> "$TEST_ROOT/docker.log"
case "$1" in
 create) echo container-teste ;;
 cp)
   [ "${FALHAR_CP:-0}" != 1 ] || exit 23
   mkdir -p "$3"
   printf 'schema de teste' > "$3/schema.prisma"
   ;;
 rm) ;;
esac
''')
        self.stub('npx', '''#!/bin/bash
set -eu
# `migrate status`: 0 quando o banco esta em dia, 1 quando ha migracao pendente.
if [ "${4:-}" = status ]; then
  echo status >> "$TEST_ROOT/npx.log"
  [ "${PENDENTE:-0}" != 1 ]
  exit
fi
[ -z "${DATABASE_URL+x}" ]
test -s prisma/schema.prisma
grep -Fq 'DATABASE_URL="postgresql://teste:teste@${DB_HOST}:5432/teste"' .env
grep -Fxq 'DB_HOST=127.0.0.1' .env
echo executada > "$TEST_ROOT/migracao.log"
[ "${FALHAR_MIGRACAO:-0}" != 1 ]
''')

        self.dumps = self.root / 'dumps'
        self.dumps.mkdir()
        # pg_dump de mentira: o conteudo sai do ambiente do teste.
        self.stub('runuser', '''#!/bin/bash
set -eu
echo "$*" >> "$TEST_ROOT/runuser.log"
[ "${FALHAR_DUMP:-0}" != 1 ] || exit 3
printf 'CREATE TABLE t (id int);\\n'
[ "${DUMP_TRUNCADO:-0}" = 1 ] || printf -- '--\\n-- PostgreSQL database dump complete\\n--\\n'
''')

    def stub(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o755)

    def run_bash(self, command, **env):
        return subprocess.run(['bash', '-c', 'source ' + shlex.quote(str(self.functions)) + '\n' + command],
                              env=dict(self.env, **env), capture_output=True, text=True)

    def migrate(self, **env):
        return self.run_bash('MIGRATE_ENV_FILE=.env.production; image=imagem; application=teste; run_migrations "$TEST_ROOT"', **env)

    def test_dotenv_preservado_e_limpeza_no_sucesso(self):
        result = self.migrate(DATABASE_URL='valor-herdado-nao-deve-vencer')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / 'migracao.log').exists())
        self.assertEqual(list(self.work.iterdir()), [])
        self.assertIn('host.docker.internal', self.envfile.read_text())

    def test_falha_ao_copiar_remove_container_e_diretorio(self):
        result = self.migrate(FALHAR_CP='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('rm', (self.root / 'docker.log').read_text().splitlines())
        self.assertEqual(list(self.work.iterdir()), [])
        self.assertFalse((self.root / 'migracao.log').exists())

    def test_migracao_falha_limpa_diretorio(self):
        self.assertNotEqual(self.migrate(FALHAR_MIGRACAO='1').returncode, 0)
        self.assertEqual(list(self.work.iterdir()), [])

    def com_dump(self, **env):
        return self.run_bash('MIGRATE_ENV_FILE=.env.production; MIGRATE_DUMP_DB=loja; MIGRATE_DUMP_DIR="$TEST_ROOT/dumps"; '
                             'image=imagem; application=loja.exemplo; run_migrations "$TEST_ROOT"', **env)

    def arquivos_de_dump(self):
        return sorted(p.name for p in self.dumps.iterdir())

    def test_sem_configurar_dump_nao_consulta_status_nem_dumpa(self):
        result = self.migrate(PENDENTE='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / 'npx.log').exists())
        self.assertFalse((self.root / 'runuser.log').exists())
        self.assertTrue((self.root / 'migracao.log').exists())

    def test_sem_migracao_pendente_nao_gera_dump(self):
        result = self.com_dump()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.arquivos_de_dump(), [])
        self.assertFalse((self.root / 'runuser.log').exists())
        self.assertTrue((self.root / 'migracao.log').exists())

    def test_migracao_pendente_dumpa_antes_e_confere(self):
        result = self.com_dump(PENDENTE='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        arquivos = self.arquivos_de_dump()
        self.assertEqual(len(arquivos), 1, arquivos)
        self.assertRegex(arquivos[0], r'^pre-migracao-loja\.exemplo-\d{8}-\d{6}\.sql\.gz$')
        self.assertIn('-u postgres -- pg_dump --no-owner -- loja', (self.root / 'runuser.log').read_text())
        self.assertIn('dump conferido', result.stderr)
        self.assertTrue((self.root / 'migracao.log').exists())

    def test_dump_que_falha_impede_a_migracao(self):
        result = self.com_dump(PENDENTE='1', FALHAR_DUMP='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('nada foi migrado', result.stderr)
        self.assertEqual(self.arquivos_de_dump(), [])
        self.assertFalse((self.root / 'migracao.log').exists())
        self.assertEqual(list(self.work.iterdir()), [])

    def test_dump_truncado_impede_a_migracao_e_nao_fica_no_lugar_do_bom(self):
        result = self.com_dump(PENDENTE='1', DUMP_TRUNCADO='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('incompleto', result.stderr)
        self.assertEqual(self.arquivos_de_dump(), [])
        self.assertFalse((self.root / 'migracao.log').exists())

    def test_nome_de_banco_estranho_e_recusado(self):
        result = self.run_bash('MIGRATE_ENV_FILE=.env.production; MIGRATE_DUMP_DB="loja; rm -rf /"; MIGRATE_DUMP_DIR="$TEST_ROOT/dumps"; '
                               'image=imagem; application=loja.exemplo; run_migrations "$TEST_ROOT"', PENDENTE='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'runuser.log').exists())
        self.assertFalse((self.root / 'migracao.log').exists())

    def test_sem_opt_in_nao_acessa_docker(self):
        result = self.run_bash('unset MIGRATE_ENV_FILE; run_migrations "$TEST_ROOT"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / 'docker.log').exists())

    def test_modo_invalido_aborta_antes_de_migrar(self):
        result = self.run_bash('DEPLOY_MODE=incorreto; preflight; MIGRATE_ENV_FILE=.env.production; run_migrations "$TEST_ROOT"')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'docker.log').exists())

    def test_compose_ausente_aborta_antes_de_migrar(self):
        result = self.run_bash('DEPLOY_MODE=container; PROJECT_DIR="$TEST_ROOT"; COMPOSE_FILE="$TEST_ROOT/ausente.yml"; COMPOSE_PROJECT=teste; SERVICE=app; CONTAINER=app; HEALTH_URL=http://localhost; preflight; MIGRATE_ENV_FILE=.env.production; run_migrations "$TEST_ROOT"')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'docker.log').exists())

    def test_sobra_de_dump_interrompido_e_removida(self):
        # Deploy morto no meio do dump deixa o ".parcial", que o rotacionador nao ve.
        sobra = self.dumps / '.pre-migracao-loja.exemplo-20260101-000000.sql.gz.parcial'
        sobra.write_text('lixo')
        alheio = self.dumps / '.pre-migracao-outra.exemplo-20260101-000000.sql.gz.parcial'
        alheio.write_text('de outra aplicacao')
        result = self.com_dump(PENDENTE='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(sobra.exists())
        self.assertTrue(alheio.exists())

    def test_dump_que_nao_chega_ao_nome_final_impede_a_migracao(self):
        self.stub('mv', '#!/bin/bash\nexit 1\n')
        result = self.com_dump(PENDENTE='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('nada foi migrado', result.stderr)
        self.assertEqual(self.arquivos_de_dump(), [])
        self.assertFalse((self.root / 'migracao.log').exists())


class MigracaoLocal(unittest.TestCase):
    """deploy-container-local.sh: mesma regra do dump, com a chamada propria dele."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.work = self.root / 'work'
        self.work.mkdir()
        self.dumps = self.root / 'dumps'
        self.dumps.mkdir()
        (self.root / '.env').write_text('DATABASE_URL="postgresql://teste:teste@host.docker.internal:5432/teste"\n')
        self.functions = self.root / 'functions.sh'
        self.functions.write_text(SCRIPT_LOCAL.read_text().split('\n[[ $EUID == 0 ]]')[0])
        self.env = dict(os.environ, PATH=str(self.bin) + ':' + os.environ['PATH'],
                        TMPDIR=str(self.work), TEST_ROOT=str(self.root))
        self.stub('docker', '''#!/bin/bash
set -eu
case "$1" in
 create) echo container-teste ;;
 cp) mkdir -p "$3"; printf 'schema de teste' > "$3/schema.prisma" ;;
 rm) ;;
esac
''')
        self.stub('npx', '''#!/bin/bash
set -eu
[ "$DATABASE_URL" = 'postgresql://teste:teste@127.0.0.1:5432/teste' ]
if [ "${4:-}" = status ]; then
  echo status >> "$TEST_ROOT/ordem.log"
  [ "${PENDENTE:-0}" != 1 ]
  exit
fi
echo migracao >> "$TEST_ROOT/ordem.log"
''')
        self.stub('runuser', '''#!/bin/bash
set -eu
echo dump >> "$TEST_ROOT/ordem.log"
[ "${FALHAR_DUMP:-0}" != 1 ] || exit 3
printf 'CREATE TABLE t (id int);\\n'
[ "${DUMP_TRUNCADO:-0}" = 1 ] || printf -- '--\\n-- PostgreSQL database dump complete\\n--\\n'
''')

    def stub(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o755)

    def migrate(self, dump_db='loja', **env):
        return subprocess.run(['bash', '-c', 'source ' + shlex.quote(str(self.functions)) + '\n'
                               'MIGRATE_ENV_FILE=.env; MIGRATE_DUMP_DB=' + dump_db + '; MIGRATE_DUMP_DIR="$TEST_ROOT/dumps"; '
                               'image=imagem; application=loja.exemplo; run_migrations "$TEST_ROOT"'],
                              env=dict(self.env, **env), capture_output=True, text=True)

    def ordem(self):
        return (self.root / 'ordem.log').read_text().split()

    def arquivos_de_dump(self):
        return sorted(p.name for p in self.dumps.iterdir())

    def test_funcao_de_dump_e_a_mesma_nos_dois_scripts(self):
        def funcao(script):
            texto = script.read_text()
            inicio = texto.index('\ndump_if_pending() {')
            return texto[inicio:texto.index('\n}\n', inicio)]
        self.assertEqual(funcao(SCRIPT), funcao(SCRIPT_LOCAL))

    def test_migracao_pendente_dumpa_antes_de_migrar(self):
        result = self.migrate(PENDENTE='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.ordem(), ['status', 'dump', 'migracao'])
        self.assertEqual(len(self.arquivos_de_dump()), 1)
        self.assertEqual(list(self.work.iterdir()), [])

    def test_sem_migracao_pendente_nao_gera_dump(self):
        result = self.migrate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.ordem(), ['status', 'migracao'])
        self.assertEqual(self.arquivos_de_dump(), [])

    def test_sem_configurar_dump_nao_consulta_status(self):
        result = self.migrate(dump_db='', PENDENTE='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.ordem(), ['migracao'])

    def test_dump_que_falha_impede_a_migracao(self):
        for caso in ({'FALHAR_DUMP': '1'}, {'DUMP_TRUNCADO': '1'}):
            with self.subTest(caso):
                result = self.migrate(PENDENTE='1', **caso)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('nada foi migrado', result.stderr)
                self.assertNotIn('migracao', self.ordem())
                self.assertEqual(self.arquivos_de_dump(), [])
                self.assertEqual(list(self.work.iterdir()), [])

    def test_dump_que_nao_chega_ao_nome_final_impede_a_migracao(self):
        # Aqui a chamada fica dentro de "if !", onde o "set -e" nao vale.
        self.stub('mv', '#!/bin/bash\nexit 1\n')
        result = self.migrate(PENDENTE='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('nada foi migrado', result.stderr)
        self.assertNotIn('migracao', self.ordem())
        self.assertEqual(self.arquivos_de_dump(), [])

class Espaco(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        (self.root / 'docker-root').mkdir()
        functions = SCRIPT.read_text().split('\n[[ $EUID == 0 ]]')[0]
        self.functions = self.root / 'functions.sh'
        self.functions.write_text(functions)
        self.env = dict(os.environ, PATH=str(self.bin) + ':' + os.environ['PATH'], TEST_ROOT=str(self.root))
        # Espaco livre lido de um arquivo, para simular a limpeza liberando disco.
        self.stub('df', '''#!/bin/bash
printf 'Filesystem 1M-blocks Used Available Use%% Mounted\\n/dev/x 100 0 %s 0%% /\\n' "$(cat "$TEST_ROOT/livre")"
''')
        self.stub('docker', '''#!/bin/bash
set -eu
echo "$*" >> "$TEST_ROOT/docker.log"
case "$1 ${2:-}" in
 "info "*) echo "$TEST_ROOT/docker-root" ;;
 "image prune") [ -z "${LIBERA:-}" ] || echo "$LIBERA" > "$TEST_ROOT/livre" ;;
 "image ls") printf '%s\\n' sha256:atual sha256:anterior sha256:velha1 sha256:velha2 sha256:velha1 ;;
esac
''')

    def stub(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o755)

    def run_bash(self, command, livre, **env):
        (self.root / 'livre').write_text(str(livre))
        return subprocess.run(['bash', '-c', 'set -euo pipefail; source ' + shlex.quote(str(self.functions)) + '\n' + command],
                              env=dict(self.env, **env), capture_output=True, text=True)

    def docker_log(self):
        path = self.root / 'docker.log'
        return path.read_text().splitlines() if path.exists() else []

    def test_espaco_suficiente_nao_limpa(self):
        result = self.run_bash('ensure_space', 50000)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('image prune --force', self.docker_log())

    def test_limpeza_que_libera_espaco_segue(self):
        result = self.run_bash('ensure_space', 100, LIBERA='50000')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('image prune --force', self.docker_log())

    def test_sem_espaco_falha_antes_do_pull(self):
        result = self.run_bash('ensure_space; echo nao-deveria-chegar', 100)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Espaco insuficiente', result.stderr)
        self.assertNotIn('nao-deveria-chegar', result.stdout)

    def test_mantem_so_imagem_atual_e_anterior(self):
        result = self.run_bash('IMAGE_REPOSITORY=ghcr.io/avilaops/app; expected_image=sha256:atual; '
                               'previous_image=sha256:anterior; prune_old_images', 50000)
        self.assertEqual(result.returncode, 0, result.stderr)
        removidas = [linha for linha in self.docker_log() if linha.startswith('image rm')]
        self.assertEqual(removidas, ['image rm sha256:velha1', 'image rm sha256:velha2'])


if __name__ == '__main__':
    unittest.main()
