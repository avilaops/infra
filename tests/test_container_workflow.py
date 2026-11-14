import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest


WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/container.yml'


class ContainerWorkflow(unittest.TestCase):
    def run_validation(self, repository='avilaops/P001', image_repository='', suffix='', publish=False):
        step = WORKFLOW.read_text().split('      - name: Validar publicacao\n', 1)[1]
        script = textwrap.dedent(step.split('        run: |\n', 1)[1].split('      - name: Preparar Docker\n', 1)[0])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'github-env'
            environment = dict(os.environ, GITHUB_REPOSITORY=repository,
                               IMAGE_REPOSITORY=image_repository, SUFFIX=suffix,
                               PUBLISH='true' if publish else 'false', DEFAULT_BRANCH='main',
                               GITHUB_REF='refs/pull/1/merge', GITHUB_EVENT_NAME='pull_request',
                               GITHUB_ENV=str(output))
            result = subprocess.run([shutil.which('bash') or 'bash', '-c', script], env=environment,
                                    capture_output=True, text=True)
            return result, output.read_text() if output.exists() else ''

    def test_default_preserves_existing_repository_and_suffix(self):
        result, output = self.run_validation(repository='AvilaOps/Mail.AvilaOps.Com', suffix='-api')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output, 'IMAGE_NAME=ghcr.io/avilaops/mail.avilaops.com-api\n')

    def test_renamed_products_keep_their_existing_package_paths(self):
        for product, package, suffix in [('P001', 'mail.avilaops.com', '-api'),
                                         ('P001', 'mail.avilaops.com', '-webmail'),
                                         ('P002', 'crm.avilaops.com', '')]:
            with self.subTest(product=product, suffix=suffix):
                result, output = self.run_validation(image_repository=f'AvilaOps/{package}',
                                                     repository=f'avilaops/{product}', suffix=suffix)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(output, f'IMAGE_NAME=ghcr.io/avilaops/{package}{suffix}\n')

    def test_invalid_repository_does_not_write_output(self):
        for repository in ['other-owner/package', 'avilaops/package:latest',
                           'https://ghcr.io/avilaops/package', 'avilaops/../package',
                           'avilaops/package\nINJECTED=value', 'avilaops/package;echo bad']:
            with self.subTest(repository=repository):
                result, output = self.run_validation(image_repository=repository)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(output, '')

    def test_pull_requests_cannot_publish(self):
        result, output = self.run_validation(image_repository='avilaops/mail.avilaops.com', publish=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output, '')


if __name__ == '__main__':
    unittest.main()
