"""Recuperação privada do ranking: nenhuma API de mercado ou entrega de mensagens."""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
from io import StringIO
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from zipfile import ZipFile

from wallets.profit_persistence import save_run
from wallets.storage import Store

SCRIPTS = Path(__file__).parents[1]/'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import profit_export as module

WALLET = 'A'*44
NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)


class ProfitExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'source.sqlite'
        self.output = self.root/'reports'
        self.snapshot = {'started_at': NOW, 'ranking': [
            {'wallet': WALLET, 'rank': 1, 'score': 1, 'evidence_level': 'C',
             'assessment_passed': False, 'reasons': ['prefilter_only'], 'unverified': []}],
            'telemetry': {'started_at': NOW, 'stages': {'top3': 1}}}
        with Store(self.source) as store:
            save_run(store, self.snapshot)
        network = patch('socket.socket.connect', side_effect=AssertionError('Rede proibida'))
        network.start()
        self.addCleanup(network.stop)

    def command(self, args):
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = module.main(args)
        return status, stdout.getvalue(), stderr.getvalue()

    def test_local_copy_requires_no_secrets_and_preserves_source(self):
        before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        with patch.dict(os.environ, {}, clear=True), patch.object(module, 'Vault') as vault, \
                patch.object(module, 'getpass') as prompt:
            status, stdout, stderr = self.command(['--db', str(self.source), '--output', str(self.output)])
        self.assertEqual(status, 0)
        self.assertEqual(stderr, '')
        self.assertEqual(stdout.strip(), str((self.output/'ranking_carteiras.xlsx').resolve()))
        vault.assert_not_called()
        prompt.assert_not_called()
        self.assertEqual(before, hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertNotIn(WALLET, stdout)
        with ZipFile(self.output/'ranking_carteiras.xlsx') as archive:
            self.assertIn(WALLET, archive.read('xl/worksheets/sheet1.xml').decode())

    def test_vault_reads_only_encryption_secret_and_removes_temporary_database(self):
        restored_paths = []
        def restore(path):
            restored_paths.append(Path(path))
            shutil.copyfile(self.source, path)
        vault = Mock()
        vault.restore.side_effect = restore
        with patch.dict(os.environ, {'STATE_ENCRYPTION_KEY': 'private-test-key'}, clear=True), \
                patch.object(module, 'Vault', return_value=vault) as constructor, \
                patch.object(module, 'getpass') as prompt:
            status, stdout, stderr = self.command(['--output', str(self.output)])
        self.assertEqual(status, 0)
        self.assertEqual(stderr, '')
        self.assertEqual(constructor.call_args.args[0], 'private-test-key')
        self.assertIsInstance(constructor.call_args.args[1], module.ExperimentGitHub)
        prompt.assert_not_called()
        vault.save.assert_not_called()
        vault.init.assert_not_called()
        self.assertFalse(restored_paths[0].parent.exists())
        self.assertNotIn('private-test-key', stdout)
        self.assertNotIn(WALLET, stdout)

    def test_hidden_prompt_fallback_and_generic_failure_do_not_leak(self):
        secret = 'secret-'+WALLET
        vault = Mock()
        def restore(path):
            Path(path).write_text(WALLET)
            self.assertTrue(Path(path).exists())
            raise module.VaultError(secret)
        vault.restore.side_effect = restore
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(module, 'Vault', return_value=vault) as constructor, \
                patch.object(module, 'getpass', return_value=secret) as prompt:
            status, stdout, stderr = self.command(['--output', str(self.output)])
        self.assertEqual(status, 1)
        self.assertEqual(stdout, '')
        self.assertNotIn(secret, stderr)
        self.assertNotIn(WALLET, stderr)
        self.assertEqual(constructor.call_args.args[0], secret)
        prompt.assert_called_once()
        self.assertFalse(Path(vault.restore.call_args.args[0]).parent.exists())
        self.assertFalse(self.output.exists())

    def test_empty_saved_ranking_produces_workbook_without_fake_wallets(self):
        with Store(self.source) as store:
            save_run(store, dict(self.snapshot, started_at=NOW.replace(hour=13), ranking=[]))
        path = module.export_latest(self.output, db=self.source)
        with ZipFile(path) as archive:
            xml = archive.read('xl/worksheets/sheet1.xml').decode()
        self.assertNotIn(WALLET, xml)
        self.assertEqual(xml.count('<row '), 1)

    def test_terminal_without_hidden_input_refuses_unsafe_fallback(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(module, 'getpass', side_effect=module.GetPassWarning('entrada visível')), \
                patch.object(module, 'Vault') as vault:
            status, stdout, stderr = self.command(['--output', str(self.output)])
        self.assertEqual(status, 1)
        self.assertEqual(stdout, '')
        self.assertNotIn('entrada visível', stderr)
        vault.assert_not_called()

    def test_missing_snapshot_or_source_is_not_silently_created(self):
        missing = self.root/'missing.sqlite'
        with self.assertRaises(module.ExportError):
            module.export_latest(self.output, db=missing)
        self.assertFalse(missing.exists())
        empty = self.root/'empty.sqlite'
        with Store(empty):
            pass
        with self.assertRaises(module.ExportError):
            module.export_latest(self.output, db=empty)
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
