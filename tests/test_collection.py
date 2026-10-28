import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from wallets.collection import Collector, normalize_trade, normalize_transfer
from wallets.storage import Store

NOW = datetime(2026, 10, 28, tzinfo=timezone.utc)
WALLET, T1, T2, T3 = 'A' * 44, 'B' * 44, 'C' * 44, 'D' * 44

def trade(**kwargs):
    result = {'tx_hash': 'hash', 'ins_index': 0, 'inner_ins_index': 0,
              'block_unix_time': int((NOW - timedelta(days=1)).timestamp()), 'block_number': 10,
              'base': {'address': T1, 'amount': '1000000', 'decimals': 6, 'type_swap': 'from', 'price': '2'},
              'quote': {'address': T2, 'amount': '2000000000', 'decimals': 9, 'type_swap': 'to', 'price': '1'},
              'volume_usd': '2'}
    result.update(kwargs)
    return result

class FakeClient:
    def __init__(self, rows=None, more=False):
        self.rows, self.more, self.calls = rows or [], more, []
    def request(self, method, path, params=None, body=None, **kwargs):
        self.calls.append((method, path, params, body))
        if path == '/trader/txs/seek_by_time':
            return {'success': True, 'data': {'items': self.rows if params['offset'] == 0 else [],
                                             'hasNext': self.more and params['offset'] == 0}}
        if path == '/wallet/v2/pnl/details':
            return {'success': True, 'data': {'tokens': [], 'summary': {'unique_tokens': 0}}}
        if path == '/wallet/v2/transfer': return {'success': True, 'data': []}
        if path == '/wallet/v2/current-net-worth':
            return {'success': True, 'data': {'items': [], 'total': 0, 'current_timestamp': int(NOW.timestamp())}}
        raise AssertionError(path)

class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'state.sqlite')
    def tearDown(self):
        self.store.close()
        self.temp.cleanup()
    def collector(self, client, pages=5):
        return Collector(client, self.store, {'api': {'max_pages': pages}}, clock=lambda: NOW)

    def test_historical_decimal_price(self):
        events = normalize_trade(trade(), WALLET)
        self.assertEqual([e.quantity for e in events], [Decimal('1'), Decimal('2')])
        self.assertEqual([e.usd_value for e in events], [Decimal('2'), Decimal('2')])
        self.assertEqual([e.kind for e in events], ['sell', 'buy'])
        self.assertTrue(all(e.fee_usd is None for e in events))

    def test_unknown_decimals_not_guessed(self):
        row = trade(base={'address': T1, 'amount': 100, 'type_swap': 'from'})
        self.assertEqual(normalize_trade(row, WALLET)[0].kind, 'unknown')

    def test_liquidity_and_failed_not_buys(self):
        self.assertEqual(normalize_trade(trade(tx_type='add'), WALLET)[0].kind, 'liquidity')
        self.assertEqual(normalize_trade(trade(status='failed'), WALLET)[0].kind, 'failed')
        self.assertEqual(normalize_trade(trade(tx_type='unsupported'), WALLET)[0].kind, 'unknown')

    def test_transfer_keeps_unknown_acquisition_cost(self):
        row = {'tx_hash': 'transfer', 'unix_time': int(NOW.timestamp()), 'token_address': T1,
               'ui_amount': '4.2', 'flow': 'in', 'price': 1, 'value': 4.2}
        event = normalize_transfer(row, WALLET)
        self.assertEqual(event.kind, 'transfer_in')
        self.assertIsNone(event.usd_value)
        self.assertEqual(event.quantity, Decimal('4.2'))

    def test_pagination_dedup_and_incremental(self):
        client = FakeClient([trade(), trade()], more=True)
        collector = self.collector(client)
        start = NOW - timedelta(days=30)
        events, coverage, provider = collector.collect(WALLET, start, NOW)
        self.assertEqual(len(events), 2)
        self.assertFalse(coverage.complete)  # páginas completas não provam retenção
        self.assertFalse(coverage.truncated)
        self.assertTrue(provider['pages_complete']['trades'])
        trader_calls = [x for x in client.calls if x[1] == '/trader/txs/seek_by_time']
        self.assertEqual([x[2]['offset'] for x in trader_calls], [0, 100])
        self.assertIsNotNone(self.store.get_cursor('trader:' + WALLET))
        client.rows, client.more, client.calls = [], False, []
        collector.collect(WALLET, start, NOW)
        self.assertEqual(client.calls[0][2]['after_time'], int((NOW-timedelta(seconds=1)).timestamp()))

    def test_truncation_never_advances_cursor(self):
        client = FakeClient([trade()], more=True)
        events, coverage, _ = self.collector(client, pages=1).collect(WALLET, NOW-timedelta(days=30), NOW)
        self.assertTrue(coverage.truncated)
        self.assertIsNone(self.store.get_cursor('trader:' + WALLET))

    def test_future_information_excluded(self):
        client = FakeClient([trade(block_unix_time=int((NOW+timedelta(days=1)).timestamp()))])
        events, coverage, _ = self.collector(client).collect(WALLET, NOW-timedelta(days=30), NOW)
        self.assertEqual(events, [])
        self.assertTrue(any('fora do período' in x for x in coverage.failures))
        with self.assertRaises(ValueError):
            self.collector(client).collect(WALLET, NOW, NOW+timedelta(days=1))

    def test_multistep_does_not_fabricate_direct_swaps(self):
        rows = [trade(), trade(ins_index=1, base={'address': T2, 'amount': '2000000000',
                'decimals': 9, 'type_swap': 'from', 'price': 1},
                quote={'address': T3, 'amount': '2000000', 'decimals': 6, 'type_swap': 'to', 'price': 1})]
        events, _, _ = self.collector(FakeClient(rows)).collect(WALLET, NOW-timedelta(days=30), NOW)
        self.assertTrue(all(e.multi_step for e in events))

    def test_full_transfer_page_without_cursor_is_truncated(self):
        client = FakeClient()
        original = client.request
        def request(method, path, params=None, body=None, **kwargs):
            if path == '/wallet/v2/transfer': return {'success': True, 'data': [{}] * 100}
            return original(method, path, params, body, **kwargs)
        client.request = request
        _, complete = self.collector(client)._transfers(WALLET, NOW-timedelta(days=30), NOW)
        self.assertFalse(complete)

    def test_discovery_records_cross_token_provenance(self):
        client = FakeClient()
        def request(method, path, params=None, body=None, **kwargs):
            if path == '/defi/v3/token/list':
                return {'success': True, 'data': {'items': [
                    {'address': token, 'recent_listing_time': int((NOW-timedelta(days=2)).timestamp()),
                     'liquidity': 200000, 'volume_24h_usd': 500000, 'price_change_24h_percent': 20}
                    for token in (T1, T2)], 'hasNext': False}}
            return {'success': True, 'data': {'items': [{'owner': WALLET}]}}
        client.request = request
        wallets = self.collector(client).discover(NOW)
        self.assertEqual(wallets, [WALLET])
        self.assertEqual(self.store.discovery_tokens(WALLET), [T1, T2])

    def test_nonmonetary_transfer_actions_not_ordinary_flows(self):
        row = {'tx_hash': 'mint', 'unix_time': int(NOW.timestamp()), 'token_address': T1,
               'ui_amount': '4.2', 'flow': 'in', 'action': 'mint'}
        self.assertEqual(normalize_transfer(row, WALLET).kind, 'unknown')
        row['action'] = 'airdrop'
        self.assertEqual(normalize_transfer(row, WALLET).kind, 'airdrop')

    def test_cursor_not_advanced_if_persistence_fails(self):
        def fail(events): raise ValueError('Conflicting event')
        self.store.put_events = fail
        with self.assertRaises(ValueError):
            self.collector(FakeClient([trade()])).collect(WALLET, NOW-timedelta(days=30), NOW)
        self.assertIsNone(self.store.get_cursor('trader:' + WALLET))

    def test_other_wallet_transaction_excluded(self):
        events, coverage, _ = self.collector(FakeClient([trade(owner=T3)])).collect(WALLET, NOW-timedelta(days=30), NOW)
        self.assertEqual(events, [])
        self.assertIsNone(self.store.get_cursor('trader:' + WALLET))

    def test_swap_uses_single_historical_consideration(self):
        row = trade(quote={'address': T2, 'amount': '2000000000', 'decimals': 9,
                           'type_swap': 'to', 'price': '1.2'})
        events = normalize_trade(row, WALLET)
        self.assertEqual([e.usd_value for e in events], [Decimal('2'), Decimal('2')])
        row.pop('volume_usd')
        self.assertEqual([e.usd_value for e in normalize_trade(row, WALLET)], [Decimal('2'), Decimal('2')])

    def test_missing_instruction_indices_unknown_and_no_id_collision(self):
        row = trade()
        row.pop('ins_index')
        first = normalize_trade(row, WALLET)[0]
        row['volume_usd'] = '3'
        second = normalize_trade(row, WALLET)[0]
        self.assertEqual(first.kind, 'unknown')
        self.assertTrue(first.multi_step)
        self.assertNotEqual(first.id, second.id)

    def test_malformed_row_and_timestamp_fail_closed(self):
        client=FakeClient([None])
        events,coverage,_=self.collector(client).collect(WALLET,NOW-timedelta(days=30),NOW)
        self.assertEqual(events,[])
        self.assertTrue(coverage.failures)
        self.assertTrue(coverage.truncated)
        client=FakeClient([trade(block_unix_time=[])])
        events,coverage,_=self.collector(client).collect(WALLET,NOW-timedelta(days=30),NOW)
        self.assertEqual(events,[])
        self.assertTrue(coverage.failures)
        self.assertIsNone(self.store.get_cursor('trader:'+WALLET))
