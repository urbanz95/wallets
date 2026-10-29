import importlib.util
import base64
import io
import json
import subprocess
from contextlib import redirect_stdout
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from cryptography.fernet import Fernet

spec = importlib.util.spec_from_file_location('state_vault', Path(__file__).parents[1] / 'scripts/state_vault.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class FakeGitHub:
    def __init__(self):
        self.data = None
        self.files = {}
        self.fail_upload = False
    def release(self):
        return self.data
    def create(self):
        self.data = {'assets': []}
    def upload(self, path):
        if self.fail_upload:
            raise module.VaultError('network')
        asset_id = len(self.files) + 1
        content = Path(path).read_bytes()
        self.files[asset_id] = content
        self.data['assets'].append({'name': Path(path).name, 'id': asset_id, 'size': len(content)})
    def download(self, asset):
        return self.files[asset['id']]

class StateVaultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'state.sqlite'
        self.github = FakeGitHub()
        self.key = Fernet.generate_key()
        self.vault = module.Vault(self.key, self.github)

    def test_roundtrip_preserves_sqlite_and_appends_generations(self):
        first = self.vault.init(self.db)
        with sqlite3.connect(self.db) as con:
            con.execute('CREATE TABLE sent(wallet TEXT PRIMARY KEY)')
            con.execute('INSERT INTO sent VALUES(?)', ('approved-wallet',))
        second = self.vault.save(self.db)
        self.assertNotEqual(first, second)
        self.assertEqual(len(self.github.files), 2)
        self.assertNotIn(b'approved-wallet', self.github.files[2])
        self.db.unlink()
        self.vault.restore(self.db)
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute('SELECT wallet FROM sent').fetchone()[0], 'approved-wallet')

    def test_existing_release_cannot_be_reinitialized(self):
        self.vault.init(self.db)
        self.db.unlink()
        with self.assertRaises(module.VaultError):
            self.vault.init(self.db)

    def test_absent_remote_state_cannot_be_restored_or_silently_reset(self):
        with self.assertRaises(module.VaultError):
            self.vault.restore(self.db)
        self.assertFalse(self.db.exists())

    def test_corrupted_latest_does_not_fallback_to_older_state(self):
        self.vault.init(self.db)
        self.vault.save(self.db)
        self.github.files[2] = b'tampered'
        with self.assertRaises(module.VaultError):
            self.vault.restore(self.db)

    def test_wrong_key_is_fail_closed(self):
        self.vault.init(self.db)
        with self.assertRaises(module.VaultError):
            module.Vault(Fernet.generate_key(), self.github).restore(self.db)

    def test_stale_local_copy_cannot_overwrite_newer_remote(self):
        self.vault.init(self.db)
        old = self.db.read_bytes()
        self.vault.save(self.db)
        self.db.write_bytes(old)
        with self.assertRaises(module.VaultError):
            self.vault.save(self.db)

    def test_failed_checkpoint_requires_recovery(self):
        self.vault.init(self.db)
        self.github.fail_upload = True
        with self.assertRaises(module.VaultError):
            self.vault.save(self.db)
        self.github.fail_upload = False
        with self.assertRaises(module.VaultError):
            self.vault.save(self.db)
        self.vault.restore(self.db)
        self.vault.save(self.db)

    def test_empty_remote_release_is_not_a_new_install(self):
        self.github.create()
        with self.assertRaises(module.VaultError):
            self.vault.restore(self.db)
        with self.assertRaises(module.VaultError):
            self.vault.init(self.db)

    def test_remote_asset_listing_is_paginated(self):
        github = module.GitHub()
        replies = [b'{"id":123,"assets":[]}', b'[{"id":1}]\n[{"id":2}]\n']
        with patch.object(github, 'command', side_effect=replies) as command:
            release = github.release()
        self.assertEqual([asset['id'] for asset in release['assets']], [1, 2])
        self.assertIn('--paginate', command.call_args_list[1].args[0])
        self.assertNotIn('--slurp', command.call_args_list[1].args[0])

    def test_remote_asset_pagination_accepts_empty_array_but_not_empty_response(self):
        github = module.GitHub()
        with patch.object(github, 'command', side_effect=[b'{"id":123}', b'[]']):
            self.assertEqual(github.release()['assets'], [])
        for invalid in (b'', b' \n\t', b'null', b'{}', b'[null]', b'[[{"id":1}]]'):
            with self.subTest(invalid=invalid):
                with patch.object(github, 'command', side_effect=[b'{"id":123}', invalid]):
                    with self.assertRaises(module.VaultError):
                        github.release()

    def test_malformed_later_asset_page_blocks_instead_of_returning_older_assets(self):
        github = module.GitHub()
        for invalid in (b'[{"id":1}]\n[{"id":2}', b'[{"id":1}]garbage',
                        b'[{"id":1}]\n{"id":2}', b'[{"id":1}]\n[2]', b'[{"id":1}]\xff'):
            with self.subTest(invalid=invalid):
                with patch.object(github, 'command', side_effect=[b'{"id":123}', invalid]):
                    with self.assertRaises(module.VaultError):
                        github.release()

    def test_checkpoint_does_not_require_plaintext_artifact(self):
        self.vault.init(self.db)
        self.assertTrue(self.db.with_suffix('.sqlite.enc').exists())
        self.assertEqual(self.db.with_suffix('.sqlite.enc').stat().st_mode & 0o777, 0o600)

    def test_failed_upload_keeps_authenticated_pending_copy_and_confirmed_backup(self):
        self.vault.init(self.db)
        confirmed = self.db.with_suffix('.sqlite.enc').read_bytes()
        with sqlite3.connect(self.db) as con:
            con.execute('CREATE TABLE evidence(payload TEXT)')
            con.execute('INSERT INTO evidence VALUES(?)', ('preserve-evidence',))
        self.github.fail_upload = True
        with self.assertRaises(module.VaultError):
            self.vault.save(self.db)
        pending = self.db.with_suffix('.sqlite.pending.enc')
        self.assertEqual(pending.stat().st_mode & 0o777, 0o600)
        body = json.loads(Fernet(self.key).decrypt(pending.read_bytes()))
        self.assertEqual(body['generation'], module._read_generation(self.db))
        self.assertIn(b'preserve-evidence', base64.b64decode(body['database']))
        self.assertEqual(self.db.with_suffix('.sqlite.enc').read_bytes(), confirmed)
        self.assertNotIn(b'preserve-evidence', pending.read_bytes())

    def test_successful_checkpoint_removes_only_pending_copy(self):
        self.vault.init(self.db)
        pending = self.db.with_suffix('.sqlite.pending.enc')
        pending.write_bytes(b'old-pending')
        self.vault.save(self.db)
        self.assertFalse(pending.exists())
        self.assertTrue(self.db.with_suffix('.sqlite.enc').is_file())

    def test_snapshot_compacts_freed_pages_without_removing_live_evidence(self):
        with sqlite3.connect(self.db) as con:
            con.execute('CREATE TABLE evidence(payload BLOB)')
            con.executemany('INSERT INTO evidence VALUES(?)', [(b'x' * 8192,)] * 100)
            con.execute('DELETE FROM evidence')
            con.execute('INSERT INTO evidence VALUES(?)', (b'keep',))
        original_size = self.db.stat().st_size
        with patch.object(module, 'MAX_STATE_BYTES', 64 * 1024):
            compacted = module._snapshot(self.db)
        self.assertLess(len(compacted), original_size)
        copy = Path(self.temp.name) / 'snapshot.sqlite'
        copy.write_bytes(compacted)
        with sqlite3.connect(copy) as con:
            self.assertEqual(con.execute('SELECT payload FROM evidence').fetchone()[0], b'keep')
        self.assertEqual(self.db.stat().st_size, original_size)

    def test_github_failure_reports_http_status_without_authentication_data(self):
        result = subprocess.CompletedProcess([], 1, b'', b'HTTP 403 https://private.invalid/?api-key=SECRET')
        with patch.object(module.subprocess, 'run', return_value=result):
            with self.assertRaises(module.VaultError) as failure:
                module.GitHub().command(['api', 'repos/urbanz95/wallets'])
        self.assertIn('HTTP 403', str(failure.exception))
        self.assertNotIn('SECRET', str(failure.exception))
        self.assertNotIn('private.invalid', str(failure.exception))

    def test_storage_diagnostics_show_only_counts_and_sizes(self):
        with sqlite3.connect(self.db) as con:
            con.execute('CREATE TABLE source_transactions(payload TEXT)')
            con.execute('INSERT INTO source_transactions VALUES(?)', ('PRIVATE-CONTENT',))
        output = io.StringIO()
        with redirect_stdout(output):
            module.print_state_statistics(self.db)
        self.assertIn('payload_bytes', output.getvalue())
        self.assertIn('rows', output.getvalue())
        self.assertNotIn('PRIVATE-CONTENT', output.getvalue())

if __name__ == '__main__':
    unittest.main()
