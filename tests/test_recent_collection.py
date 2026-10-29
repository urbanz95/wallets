"""Descoberta e recolha da janela recente, sem chamadas reais de rede."""
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from wallets.collection import Collector
from wallets.models import Event, iso
from wallets.storage import Store
from test_collection import FakeClient, NOW, WALLET, T1, T2, T3, trade


class RecentCollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'state.sqlite')
        self.addCleanup(self.store.close)
        self.start = NOW-timedelta(days=7)
        self.config = {
            'recent': {'enabled': True, 'window_days': 7, 'require_activity_hours': 24},
            'discovery': {'min_age_days': 0, 'max_age_days': 7, 'time_frame': '7d', 'max_tokens': 3},
            'api': {'max_pages': 5, 'collect_optional': False, 'max_transaction_details_per_wallet': 2},
        }

    def collector(self, client):
        return Collector(client, self.store, self.config, clock=lambda: NOW)

    def test_recent_query_only_after_time_and_exact_half_open_window(self):
        moments = [self.start-timedelta(seconds=1), self.start, NOW-timedelta(seconds=1), NOW, NOW+timedelta(seconds=1)]
        rows = [trade(tx_hash=f'tx-{i}', block_unix_time=int(stamp.timestamp())) for i, stamp in enumerate(moments)]
        client = FakeClient(rows)
        events, coverage, provider = self.collector(client).collect(WALLET, self.start, NOW)
        query = client.calls[0][2]
        self.assertEqual(query['after_time'], int(self.start.timestamp())-1)
        self.assertNotIn('before_time', query)
        self.assertEqual({event.timestamp for event in events}, {moments[1], moments[2]})
        self.assertTrue(coverage.complete)
        self.assertTrue(provider['history_query']['end_exclusive'])
        self.assertEqual(provider['history_query']['excluded_at_or_after_cutoff'], 2)
        self.assertEqual(coverage.failures, [])

    def test_filter_also_applies_to_events_already_in_store(self):
        old = Event('stored-at-cutoff', WALLET, T1, NOW, 'unknown', Decimal(0), source='birdeye')
        self.store.put_events([old])
        events, _, _ = self.collector(FakeClient()).collect(WALLET, self.start, NOW)
        self.assertEqual(events, [])
        self.assertEqual(len(self.store.get_events(WALLET, self.start, NOW)), 1)

    def test_optional_requests_do_not_spend_history_budget_or_fake_open_risk(self):
        client = FakeClient([trade()])
        _, coverage, provider = self.collector(client).collect(WALLET, self.start, NOW)
        self.assertEqual([call[1] for call in client.calls], ['/trader/txs/seek_by_time', '/wallet/v2/transfer'])
        self.assertFalse(provider['pages_complete']['pnl'])
        self.assertFalse(provider['pages_complete']['holdings'])
        self.assertFalse(coverage.marks_complete)
        self.assertFalse(coverage.fees_complete)
        self.assertNotIn('transaction_details', provider)
        self.assertTrue(any('adiados' in text for text in provider['limitations']))

    def test_optional_recent_pnl_uses_7d_instead_of_lifetime(self):
        self.config['api']['collect_optional'] = True
        self.config['api']['max_transaction_details_per_wallet'] = 0
        client = FakeClient()
        _, _, provider = self.collector(client).collect(WALLET, self.start, NOW)
        pnl = next(call for call in client.calls if call[1] == '/wallet/v2/pnl/details')
        self.assertEqual(pnl[3]['duration'], '7d')
        self.assertEqual(provider['pnl_duration'], '7d')

    def test_multiple_offset_pages_do_not_certify_stable_snapshot(self):
        client = FakeClient([trade()], more=True)
        _, coverage, provider = self.collector(client).collect(WALLET, self.start, NOW)
        self.assertFalse(provider['pages_complete']['trades'])
        self.assertFalse(coverage.complete)
        self.assertTrue(coverage.truncated)
        self.assertTrue(any('offset' in text for text in provider['limitations']))
        self.assertIsNone(self.store.get_cursor('trader:'+WALLET))

    def test_terminal_page_of_100_is_distinct_from_multiple_pages(self):
        client = FakeClient([trade(tx_hash=f'one-page-{i}') for i in range(100)])
        _, coverage, provider = self.collector(client).collect(WALLET, self.start, NOW)
        self.assertTrue(provider['pages_complete']['trades'])
        self.assertTrue(coverage.complete)

    def test_repeated_collection_deduplicates_without_reusing_unsafe_offset(self):
        client = FakeClient([trade()], more=True)
        collector = self.collector(client)
        collector.collect(WALLET, self.start, NOW)
        collector.collect(WALLET, self.start, NOW)
        calls = [call[2]['offset'] for call in client.calls if call[1] == '/trader/txs/seek_by_time']
        self.assertEqual(calls, [0, 100, 0, 100])
        self.assertEqual(len(self.store.get_events(WALLET, self.start, NOW)), 2)

    def test_owner_or_malformed_rows_still_block_feed(self):
        for row in (trade(owner=T3), trade(block_unix_time='bad-time')):
            with self.subTest(row=row):
                _, coverage, provider = self.collector(FakeClient([row])).collect(WALLET, self.start, NOW)
                self.assertFalse(provider['pages_complete']['trades'])
                self.assertTrue(coverage.failures)

    def test_transfer_at_inclusive_api_cutoff_is_outside_local_window(self):
        client = FakeClient()
        original = client.request
        def request(method, path, params=None, body=None, **kwargs):
            if path == '/wallet/v2/transfer':
                return {'success': True, 'data': [{'tx_hash': 'transfer-at-cutoff', 'unix_time': int(NOW.timestamp()),
                        'token_address': T1, 'ui_amount': '2', 'flow': 'in'}]}
            return original(method, path, params, body, **kwargs)
        client.request = request
        events, coverage, _ = self.collector(client).collect(WALLET, self.start, NOW)
        self.assertEqual(events, [])
        self.assertTrue(coverage.complete)

    def test_discovery_uses_7d_and_excludes_old_inactive_and_future_tokens(self):
        tokens = [self.token(T1, days=2), self.token(T2, days=8), self.token(T3, days=2, last_hours=25),
                  self.token('E'*44, days=-1), self.token('F'*44, days=0.2)]
        client = self.discovery_client(tokens)
        self.assertEqual(self.collector(client).discover(NOW), [WALLET])
        trader_calls = [call for call in client.calls if call[1] == '/defi/v2/tokens/top_traders']
        self.assertEqual({call[2]['address'] for call in trader_calls}, {T1, 'F'*44})
        self.assertTrue(all(call[2]['time_frame'] == '7d' for call in trader_calls))
        self.assertEqual(client.calls[0][2]['min_recent_listing_time'], int(self.start.timestamp()))
        self.assertEqual(client.calls[0][2]['max_recent_listing_time'], int(NOW.timestamp()))
        evidence = self.store.discovery_evidence(WALLET)
        self.assertEqual(len(evidence), 2)
        for row in evidence:
            self.assertEqual(row['evidence']['profile'], 'recent-7d')
            self.assertEqual(row['evidence']['time_frame'], '7d')
            self.assertEqual(row['evidence']['window_start'], iso(self.start))
            self.assertEqual(row['evidence']['activity_confirmation'], 'pending_wallet_history')

    def test_missing_token_last_trade_does_not_claim_wallet_recency(self):
        token = self.token(T1)
        del token['last_trade_unix_time']
        self.assertEqual(self.collector(self.discovery_client([token])).discover(NOW), [WALLET])
        self.assertEqual(self.store.discovery_evidence(WALLET)[0]['evidence']['activity_confirmation'],
                         'pending_wallet_history')

    def test_discovery_keeps_candidates_from_both_tokens_despite_three_evaluation_slots(self):
        self.config['discovery']['max_wallets'] = 3
        first = [letter*44 for letter in '123456789A']
        second = [letter*44 for letter in 'BCDEFGHJKL']
        client = FakeClient()
        def request(method, path, params=None, body=None, **kwargs):
            client.calls.append((method, path, params, body))
            if path == '/defi/v3/token/list':
                return {'success': True, 'data': {'items': [self.token(T1), self.token(T2)], 'hasNext': False}}
            self.assertEqual(path, '/defi/v2/tokens/top_traders')
            self.assertEqual(params['offset'], 0, 'Não gastar outra página por token na experiência')
            return {'success': True, 'data': {'items': [{'owner': wallet} for wallet in
                    (first if params['address'] == T1 else second)]}}
        client.request = request
        wallets = self.collector(client).discover(NOW)
        self.assertEqual(len(wallets), 20)
        self.assertEqual(set(wallets), set(first+second))
        self.assertEqual(wallets[:4], [first[0], second[0], first[1], second[1]])
        self.assertEqual(len(client.calls), 3)  # lista + uma página por cada token
        self.assertEqual(len(self.store.discoveries()), 20)

    def test_legacy_discovery_still_applies_its_global_wallet_cap(self):
        self.config['recent']['enabled'] = False
        self.config['discovery']['max_wallets'] = 3
        self.config['discovery'].pop('time_frame')
        client = self.discovery_client([self.token(T1), self.token(T2)])
        original = client.request
        wallets = [letter*44 for letter in 'EFGHJK']
        def request(method, path, params=None, body=None, **kwargs):
            if path == '/defi/v2/tokens/top_traders':
                client.calls.append((method, path, params, body))
                self.assertEqual(params['time_frame'], '30d')
                return {'success': True, 'data': {'items': [{'owner': wallet} for wallet in wallets]}}
            return original(method, path, params, body, **kwargs)
        client.request = request
        self.assertEqual(self.collector(client).discover(NOW), wallets[:3])

    @staticmethod
    def token(address, *, days=1, last_hours=1):
        return {'address': address, 'recent_listing_time': int((NOW-timedelta(days=days)).timestamp()),
                'last_trade_unix_time': int((NOW-timedelta(hours=last_hours)).timestamp()),
                'liquidity': 200000, 'volume_24h_usd': 500000, 'price_change_24h_percent': 20}

    @staticmethod
    def discovery_client(tokens):
        client = FakeClient()
        original = client.request
        def request(method, path, params=None, body=None, **kwargs):
            client.calls.append((method, path, params, body))
            if path == '/defi/v3/token/list':
                return {'success': True, 'data': {'items': tokens, 'hasNext': False}}
            if path == '/defi/v2/tokens/top_traders':
                return {'success': True, 'data': {'items': [{'owner': WALLET}]}}
            return original(method, path, params, body, **kwargs)
        client.request = request
        return client


if __name__ == '__main__':
    unittest.main()
