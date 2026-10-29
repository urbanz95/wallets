"""Compressão lossless e limpeza só de cache expirado, sem acesso à rede."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zlib

from wallets.models import dumps, iso
from wallets.storage import (
    MAX_PAYLOAD_BYTES, PAYLOAD_MAGIC, Store, _pack_payload, _unpack_payload,
)

NOW = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)


def transaction(signature='original'):
    return {
        'blockTime': int(NOW.timestamp()),
        'transaction': {'signatures': [signature], 'message': {'instructions': []}},
        'meta': {
            'err': {'InstructionError': [0, 'Custom']},
            'fee': 5000,
            'postBalances': [9007199254740993],
            'logMessages': ['Program log: prova pública — ação' * 100],
            'postTokenBalances': [{'uiTokenAmount': {
                'amount': '900719925474099312345', 'decimals': 9,
            }}],
        },
        'campo_desconhecido_preservado': {'valor': '€'},
    }


def compressed_with_header(raw, expected=None):
    return PAYLOAD_MAGIC + (len(raw) if expected is None else expected).to_bytes(8, 'big') + zlib.compress(raw)


class StorageCompressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.sqlite'

    def test_compressed_cache_and_source_roundtrip_after_reopen_without_loss(self):
        row = transaction()
        value = {'data': [row], 'unknown': None}
        original_hash = hashlib.sha256(dumps(row).encode()).hexdigest()
        with Store(self.path) as store:
            store.cache_put('full', value, NOW + timedelta(hours=1))
            store.put_source_transactions('helius', 'address', [row])
            self.assertEqual(store.db.execute('SELECT typeof(payload) FROM cache').fetchone()[0], 'blob')
            self.assertEqual(store.db.execute('SELECT typeof(payload) FROM source_transactions').fetchone()[0], 'blob')
        with Store(self.path) as store:
            self.assertEqual(store.cache_get('full', NOW), value)
            recovered = store.get_source_transactions('helius', 'address', NOW, NOW)
            self.assertEqual(recovered, [row])
            self.assertEqual(hashlib.sha256(dumps(recovered[0]).encode()).hexdigest(), original_hash)
            self.assertEqual(store.db.execute('PRAGMA user_version').fetchone()[0], 1)

    def test_legacy_text_cache_and_raw_survive_reopen_and_deduplicate(self):
        row = transaction()
        with Store(self.path) as store:
            with store.db:
                store.db.execute('INSERT INTO cache VALUES(?,?,?)',
                                 ('legacy', iso(NOW + timedelta(hours=1)), dumps({'data': row})))
                store.db.execute('INSERT INTO source_transactions VALUES(?,?,?,?,?)',
                                 ('helius', 'address', 'original', iso(NOW), dumps(row)))
        with Store(self.path) as store:
            self.assertEqual(store.cache_get('legacy', NOW), {'data': row})
            self.assertEqual(store.get_source_transactions('helius', 'address', NOW, NOW), [row])
            store.put_source_transactions('helius', 'address', [row, deepcopy(row)])
            self.assertEqual(store.db.execute('SELECT count(*) FROM source_transactions').fetchone()[0], 1)
            self.assertEqual(store.db.execute('SELECT typeof(payload) FROM source_transactions').fetchone()[0], 'text')
            store.cache_put('legacy', {'renewed': row}, NOW + timedelta(hours=2))
            self.assertEqual(store.cache_get('legacy', NOW), {'renewed': row})

    def test_compressed_duplicates_and_changed_raw_roll_back_whole_batch(self):
        original = transaction()
        changed = deepcopy(original)
        changed['meta']['fee'] += 1
        with Store(self.path) as store:
            store.put_source_transactions('helius', 'address', [original, original])
            with self.assertRaisesRegex(ValueError, 'divergente'):
                store.put_source_transactions('helius', 'address', [transaction('new'), changed])
            self.assertEqual(store.get_source_transactions('helius', 'address', NOW, NOW), [original])

    def test_prune_removes_only_expired_cache_and_preserves_cursors_raw_and_quotas(self):
        row = transaction()
        with Store(self.path) as store:
            for key, expiry in [('old', NOW - timedelta(seconds=1)), ('boundary', NOW),
                                ('fresh', NOW + timedelta(microseconds=1))]:
                store.cache_put(key, {'data': row}, expiry)
            store.put_source_transactions('helius', 'address', [row])
            store.set_cursor('pagination', 'last-safe-page')
            self.assertTrue(store.reserve_budget('2026-10', 70, 100))
            self.assertTrue(store.reserve_budget('2026-10', 90, 100, namespace='helius'))
            self.assertEqual(store.prune_cache(NOW), 2)
            self.assertEqual(store.prune_cache(NOW), 0)
            self.assertEqual(store.cache_get('fresh', NOW), {'data': row})
            self.assertEqual(store.get_source_transactions('helius', 'address', NOW, NOW), [row])
            self.assertEqual(store.get_cursor('pagination'), 'last-safe-page')
        with Store(self.path) as store:
            self.assertFalse(store.reserve_budget('2026-10', 31, 100))
            self.assertFalse(store.reserve_budget('2026-10', 11, 100, namespace='helius'))
            self.assertEqual(store.db.execute('SELECT count(*) FROM cache').fetchone()[0], 1)
            self.assertEqual(store.db.execute('SELECT count(*) FROM source_transactions').fetchone()[0], 1)

    def test_codec_rejects_truncated_modified_trailing_or_unknown_stream(self):
        original = _pack_payload('{"proof":"preservada"}')
        damaged = bytearray(original)
        damaged[-1] ^= 1
        for bad in [original[:-1], bytes(damaged), original + b'garbage',
                    original + zlib.compress(b'{}'), b'unknown-format', 4]:
            with self.subTest(kind=type(bad).__name__, size=len(bad) if isinstance(bad, bytes) else None):
                with self.assertRaises(ValueError):
                    _unpack_payload(bad)

    def test_codec_rejects_forged_lengths_and_bounds_expansion(self):
        compressed_bomb = compressed_with_header(b'x' * 100_000, expected=1)
        with self.assertRaises(ValueError):
            _unpack_payload(compressed_bomb)
        for expected in (0, 2, MAX_PAYLOAD_BYTES + 1):
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                _unpack_payload(compressed_with_header(b'x', expected))
        with patch('wallets.storage.MAX_PAYLOAD_BYTES', 32):
            with self.assertRaises(ValueError):
                _pack_payload('x' * 33)
            with self.assertRaises(ValueError):
                _unpack_payload('x' * 33)
            with self.assertRaises(ValueError):
                _unpack_payload(compressed_with_header(b'x' * 33))

    def test_codec_accepts_valid_empty_unicode_and_memoryview_but_rejects_non_utf8(self):
        for text in ('', '{"ação":"€"}', 'x' * 100_000):
            self.assertEqual(_unpack_payload(memoryview(_pack_payload(text))), text)
        with self.assertRaises(ValueError):
            _unpack_payload(compressed_with_header(b'\xff'))

    def test_corrupted_persisted_cache_and_raw_are_errors_not_missing_data(self):
        with Store(self.path) as store:
            with store.db:
                store.db.execute('INSERT INTO cache VALUES(?,?,?)',
                                 ('bad', iso(NOW + timedelta(hours=1)), b'invalid'))
                store.db.execute('INSERT INTO source_transactions VALUES(?,?,?,?,?)',
                                 ('helius', 'address', 'bad', iso(NOW), b'invalid'))
            with self.assertRaises(ValueError):
                store.cache_get('bad', NOW)
            with self.assertRaises(ValueError):
                store.get_source_transactions('helius', 'address', NOW, NOW)

    def test_material_database_size_reduction_without_removing_transactions(self):
        plain_path = Path(self.temp.name) / 'plain.sqlite'
        rows = []
        for i in range(20):
            row = transaction(str(i))
            row['meta']['logMessages'] *= 20
            rows.append(row)
        with Store(plain_path) as plain, Store(self.path) as packed:
            with plain.db:
                plain.db.executemany('INSERT INTO source_transactions VALUES(?,?,?,?,?)',
                                     [('helius', 'address', str(i), iso(NOW), dumps(row))
                                      for i, row in enumerate(rows)])
            packed.put_source_transactions('helius', 'address', rows)
            self.assertEqual(packed.get_source_transactions('helius', 'address', NOW, NOW),
                             plain.get_source_transactions('helius', 'address', NOW, NOW))
            self.assertEqual(packed.db.execute('SELECT count(*) FROM source_transactions').fetchone()[0], 20)
        self.assertLess(self.path.stat().st_size, plain_path.stat().st_size / 4)


if __name__ == '__main__':
    unittest.main()
