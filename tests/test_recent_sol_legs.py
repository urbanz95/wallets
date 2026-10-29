"""Componentes SOL nativo/wSOL fictícios; nenhuma consulta ou transação real."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wallets.helius_normalization import normalize_transaction
from wallets.models import SOL
from wallets.storage import Store

WALLET = 'fixture-wallet-beneficiary'
ATA = 'fixture-wrapped-sol-account'
PROGRAM = 'fixture-unsupported-program'
SIGNATURE = 'fixture-sol-components'
STAMP = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)


def fixture(*, wrapped=True, native=True, equal_quantities=False):
    def token_balance(amount):
        return {'accountIndex': 1, 'mint': SOL, 'owner': WALLET,
                'uiTokenAmount': {'amount': str(amount), 'decimals': 9}}
    wrapped_change = 100_000_000 if wrapped else 0
    native_change = (wrapped_change if equal_quantities else 200_000_000) if native else 0
    return {
        'slot': 123, 'transactionIndex': 2, 'blockTime': int(STAMP.timestamp()),
        'transaction': {'signatures': [SIGNATURE], 'message': {
            'accountKeys': [WALLET, ATA, PROGRAM],
            'instructions': [{'programId': PROGRAM, 'accounts': [WALLET, ATA], 'data': '11'}],
        }},
        'meta': {'err': None, 'fee': 5000,
                 'preBalances': [1_000_000_000, 2_000_000, 1],
                 'postBalances': [1_000_000_000-native_change-5000, 2_000_000, 1],
                 'preTokenBalances': [token_balance(300_000_000)],
                 'postTokenBalances': [token_balance(300_000_000+wrapped_change)],
                 'innerInstructions': []},
    }


class NativeSolComponentsTests(unittest.TestCase):
    def setUp(self):
        for target in ('socket.socket.connect', 'urllib.request.urlopen',
                       'urllib.request.OpenerDirector.open'):
            blocker = patch(target, side_effect=AssertionError('Rede proibida neste teste'))
            blocker.start()
            self.addCleanup(blocker.stop)

    def test_native_and_wrapped_unknown_components_have_distinct_stable_identities(self):
        row = fixture()
        result = normalize_transaction(row, WALLET)
        events = result['events']
        prefix = f'helius:{SIGNATURE}:{SOL}:unknown'
        self.assertEqual([event.id for event in events], [prefix, prefix+':native-sol'])
        self.assertEqual([event.quantity for event in events], [Decimal('.1'), Decimal('.2')])
        self.assertTrue(all(event.token == SOL and event.kind == 'unknown'
                            and event.multi_step for event in events))
        self.assertTrue(all(event.usd_value is None and event.fee_usd is None for event in events))
        self.assertFalse(result['evidence']['instruction_coverage_complete'])
        self.assertEqual(result['evidence']['recognized_swaps'], [])
        self.assertEqual(normalize_transaction(deepcopy(row), WALLET)['events'], events)

    def test_equal_quantities_still_represent_separate_components(self):
        events = normalize_transaction(fixture(equal_quantities=True), WALLET)['events']
        self.assertEqual(len(events), 2)
        self.assertEqual([event.quantity for event in events], [Decimal('.1'), Decimal('.1')])
        self.assertEqual(len({event.id for event in events}), 2)

    def test_non_colliding_native_and_wrapped_components_keep_legacy_identity(self):
        prefix = f'helius:{SIGNATURE}:{SOL}:unknown'
        for wrapped, native, quantity in ((False, True, Decimal('.2')),
                                           (True, False, Decimal('.1'))):
            with self.subTest(wrapped=wrapped, native=native):
                events = normalize_transaction(fixture(wrapped=wrapped, native=native), WALLET)['events']
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0].id, prefix)
                self.assertEqual(events[0].quantity, quantity)
                self.assertIsNone(events[0].usd_value)

    def test_lot_persists_idempotently_and_survives_sqlite_reopen(self):
        events = normalize_transaction(fixture(), WALLET)['events']
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'state.sqlite'
            with Store(path) as store:
                store.put_source_events('helius', events)
                store.put_source_events('helius', normalize_transaction(fixture(), WALLET)['events'])
                self.assertEqual(store.db.execute('SELECT COUNT(*) FROM source_events').fetchone()[0], 2)
            with Store(path) as reopened:
                restored = reopened.get_source_events('helius', WALLET,
                    STAMP-timedelta(seconds=1), STAMP+timedelta(seconds=1))
                self.assertEqual({event.id for event in restored}, {event.id for event in events})
                self.assertEqual(sorted(event.quantity for event in restored), [Decimal('.1'), Decimal('.2')])
                self.assertTrue(all(event.kind == 'unknown' and event.usd_value is None
                                    and event.fee_usd is None for event in restored))


if __name__ == '__main__':
    unittest.main()
