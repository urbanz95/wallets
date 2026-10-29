"""Cofre experimental compactado: compatibilidade, integridade e limites."""
import base64
from copy import deepcopy
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import zlib

from cryptography.fernet import Fernet

SCRIPTS = Path(__file__).parents[1] / 'scripts'
spec = importlib.util.spec_from_file_location('recent_vault_storage', SCRIPTS / 'state_vault.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FakeGitHub:
    def __init__(self):
        self.data = None
        self.files = {}

    def release(self):
        return deepcopy(self.data)

    def create(self):
        self.data = {'assets': []}

    def upload(self, path):
        number = len(self.files) + 1
        content = Path(path).read_bytes()
        self.files[number] = content
        self.data['assets'].append({'name': Path(path).name, 'id': number, 'size': len(content)})

    def download(self, asset):
        return self.files[asset['id']]


class RecentVaultTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.db = self.root / 'state.sqlite'
        self.github = FakeGitHub()
        self.key = Fernet.generate_key()
        self.fernet = Fernet(self.key)
        self.vault = module.Vault(self.key, self.github, compress_snapshot=True,
                                  max_state_bytes=256 * 1024)
        for target in ('socket.socket.connect', 'urllib.request.urlopen',
                       'urllib.request.OpenerDirector.open'):
            blocker = patch(target, side_effect=AssertionError('Rede proibida no teste'))
            blocker.start()
            self.addCleanup(blocker.stop)

    def latest_body(self):
        asset = module.latest_asset(self.github.release())
        return json.loads(self.fernet.decrypt(self.github.files[asset['id']]))

    def replace_body(self, body):
        asset = module.latest_asset(self.github.release())
        content = self.fernet.encrypt(json.dumps(body, sort_keys=True).encode())
        self.github.files[asset['id']] = content
        self.github.data['assets'][-1]['size'] = len(content)

    def add_evidence(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute('CREATE TABLE evidence(id INTEGER PRIMARY KEY,payload BLOB)')
            connection.executemany('INSERT INTO evidence(payload) VALUES(?)',
                                   [(b'preserve-evidence-' + bytes([index]) + b'x' * 4096,)
                                    for index in range(20)])

    def test_production_default_preserves_the_exact_legacy_envelope_contract(self):
        production = module.Vault(self.key, self.github)
        production.init(self.db)
        self.add_evidence()
        production.save(self.db)
        body = self.latest_body()
        self.assertEqual(set(body), {'schema', 'generation', 'parent', 'created_at', 'sha256', 'database'})
        raw = base64.b64decode(body['database'], validate=True)
        self.assertTrue(raw.startswith(b'SQLite format 3\x00'))
        self.assertEqual(hashlib.sha256(raw).hexdigest(), body['sha256'])
        self.assertEqual(raw, module._snapshot(self.db))
        self.assertFalse(production.compress_snapshot)
        self.assertEqual(production.max_state_bytes, 256 * 1024 * 1024)
        # Estes são exatamente os pressupostos do leitor da main anterior.
        restored = self.root / 'legacy-readable.sqlite'
        restored.write_bytes(raw)
        with sqlite3.connect(restored) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM evidence').fetchone()[0], 20)
        self.assertEqual(module._read_generation(restored), body['generation'])

    def test_compressed_roundtrip_preserves_every_record_and_hashes_plain_sqlite(self):
        self.vault.init(self.db)
        self.add_evidence()
        self.vault.save(self.db)
        body = self.latest_body()
        self.assertEqual(body['database_encoding'], 'zlib')
        stored = base64.b64decode(body['database'], validate=True)
        raw = zlib.decompress(stored)
        self.assertEqual(body['database_size'], len(raw))
        self.assertEqual(raw, module._snapshot(self.db, 256 * 1024))
        self.assertEqual(body['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertNotEqual(body['sha256'], hashlib.sha256(stored).hexdigest())
        self.assertLess(len(stored), len(raw) / 2)
        restored = self.root / 'roundtrip.sqlite'
        self.vault.restore(restored)
        with sqlite3.connect(self.db) as source, sqlite3.connect(restored) as target:
            self.assertEqual(source.execute('SELECT * FROM evidence').fetchall(),
                             target.execute('SELECT * FROM evidence').fetchall())
        self.assertEqual(restored.stat().st_mode & 0o777, 0o600)

    def test_opt_in_compressed_reader_can_restore_old_uncompressed_snapshots(self):
        old = module.Vault(self.key, self.github)
        old.init(self.db)
        self.add_evidence()
        old.save(self.db)
        restored = self.root / 'old-restored.sqlite'
        self.vault.restore(restored)
        with sqlite3.connect(restored) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM evidence').fetchone()[0], 20)
        self.assertNotIn('database_encoding', self.latest_body())

    def test_default_new_reader_also_accepts_a_small_compressed_snapshot(self):
        self.vault.init(self.db)
        self.add_evidence()
        self.vault.save(self.db)
        restored = self.root / 'new-reader.sqlite'
        module.Vault(self.key, self.github).restore(restored)
        self.assertTrue(restored.exists())

    def test_snapshot_limit_is_explicit_and_default_is_resolved_at_call_time(self):
        self.vault.init(self.db)
        self.add_evidence()
        with patch.object(module, 'MAX_STATE_BYTES', 16 * 1024):
            with self.assertRaises(module.VaultError):
                module._snapshot(self.db)
            self.assertTrue(module._snapshot(self.db, 256 * 1024).startswith(b'SQLite format 3\x00'))
        small = module.Vault(self.key, self.github, compress_snapshot=True, max_state_bytes=16 * 1024)
        before = len(self.github.files)
        with self.assertRaises(module.VaultError):
            small.save(self.db)
        self.assertEqual(len(self.github.files), before)

    def test_unknown_encoding_truncation_trailing_data_and_bad_hash_are_rejected(self):
        self.vault.init(self.db)
        original = self.latest_body()
        compressed = base64.b64decode(original['database'])
        variants = []
        for data in (b'not-zlib', compressed[:-1], compressed + b'trailing', compressed + compressed):
            body = deepcopy(original)
            body['database'] = base64.b64encode(data).decode()
            variants.append(body)
        body = deepcopy(original)
        body['database_encoding'] = 'gzip'
        variants.append(body)
        body = deepcopy(original)
        body['sha256'] = hashlib.sha256(compressed).hexdigest()
        variants.append(body)
        for index, body in enumerate(variants):
            with self.subTest(index=index):
                self.replace_body(body)
                path = self.root / f'invalid-{index}.sqlite'
                with self.assertRaises(module.VaultError):
                    self.vault.restore(path)
                self.assertFalse(path.exists())

    def test_declared_decompressed_size_is_required_integral_positive_and_bounded(self):
        self.vault.init(self.db)
        original = self.latest_body()
        for size in (None, True, 0, -1, 1, '8192', 256 * 1024 + 1):
            body = deepcopy(original)
            if size is None:
                del body['database_size']
            else:
                body['database_size'] = size
            with self.subTest(size=size):
                self.replace_body(body)
                with self.assertRaises(module.VaultError):
                    self.vault.restore(self.root / 'invalid-size.sqlite')

    def test_decompression_bomb_is_limited_before_any_sqlite_write(self):
        self.vault.init(self.db)
        body = self.latest_body()
        bomb = b'x' * (8 * 1024 * 1024)
        body['database'] = base64.b64encode(zlib.compress(bomb)).decode()
        body['database_size'] = 1024  # declaração falsa não autoriza expansão
        body['sha256'] = hashlib.sha256(bomb).hexdigest()
        self.replace_body(body)
        decoder = zlib.decompressobj()
        observed = []
        class BoundedDecoder:
            def decompress(self, data, max_length):
                observed.append(max_length)
                return decoder.decompress(data, max_length)
            def __getattr__(self, name):
                return getattr(decoder, name)
        with patch.object(module.zlib, 'decompressobj', return_value=BoundedDecoder()):
            with self.assertRaises(module.VaultError):
                self.vault.restore(self.root / 'bomb.sqlite')
        self.assertEqual(observed, [256 * 1024 + 1])
        self.assertFalse((self.root / 'bomb.sqlite').exists())

    def test_ciphertext_and_uncompressed_restore_limits_are_enforced(self):
        self.vault.init(self.db)
        asset = module.latest_asset(self.github.release())
        original = self.github.files[asset['id']]
        self.github.files[asset['id']] = b'x' * (2 * self.vault.max_state_bytes + 1)
        with self.assertRaises(module.VaultError):
            self.vault.restore(self.root / 'cipher-too-large.sqlite')
        self.github.files[asset['id']] = original
        body = self.latest_body()
        raw = b'x' * (self.vault.max_state_bytes + 1)
        body.pop('database_encoding')
        body.pop('database_size')
        body['database'] = base64.b64encode(raw).decode()
        body['sha256'] = hashlib.sha256(raw).hexdigest()
        self.replace_body(body)
        with self.assertRaises(module.VaultError):
            self.vault.restore(self.root / 'raw-too-large.sqlite')

    def test_invalid_storage_configuration_is_rejected(self):
        for kwargs in ({'compress_snapshot': 1}, {'max_state_bytes': True},
                       {'max_state_bytes': 0}, {'max_state_bytes': -1}, {'max_state_bytes': '512'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(module.VaultError):
                module.Vault(self.key, self.github, **kwargs)

    def test_experiment_main_opts_in_only_for_the_experimental_vault(self):
        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))
        import recent_experiment
        with patch.dict('os.environ', {'STATE_ENCRYPTION_KEY': 'fixture-key',
                                      'BIRDEYE_API_KEY': 'fixture-key', 'HELIUS_API_KEY': 'fixture-key'}), \
             patch.object(recent_experiment.cli, 'load_config', return_value={}), \
             patch.object(recent_experiment, 'Vault') as vault, \
             patch.object(recent_experiment, 'run_experiment', return_value={'telegram_sent': 0}), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(recent_experiment.main([]), 0)
        self.assertEqual(vault.call_args_list[0].kwargs, {})
        self.assertEqual(vault.call_args_list[1].kwargs,
                         {'compress_snapshot': True, 'max_state_bytes': 512 * 1024 * 1024})


if __name__ == '__main__':
    unittest.main()
