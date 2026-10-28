import importlib.util
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
        replies = [b'{"id":123,"assets":[]}', b'[[{"id":1}],[{"id":2}]]']
        with patch.object(github, 'command', side_effect=replies) as command:
            release = github.release()
        self.assertEqual([asset['id'] for asset in release['assets']], [1, 2])
        self.assertIn('--paginate', command.call_args_list[1].args[0])

    def test_checkpoint_does_not_require_plaintext_artifact(self):
        self.vault.init(self.db)
        self.assertTrue(self.db.with_suffix('.sqlite.enc').exists())
        self.assertEqual(self.db.with_suffix('.sqlite.enc').stat().st_mode & 0o777, 0o600)

if __name__ == '__main__':
    unittest.main()
