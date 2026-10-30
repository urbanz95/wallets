"""Sondagem Wallet API: exatamente uma tentativa, estado privado e zero Telegram."""
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from wallets.birdeye import BirdeyeClient
from wallets.profit_budget import ProfitBudget
from wallets.profit_persistence import latest_run, save_run
from wallets import profit_probe as module
from wallets.storage import Store


NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
WALLET = 'A'*44


class ProfitProbeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = Store(self.root/'production.sqlite')
        self.store = Store(self.root/'profit.sqlite')
        self.addCleanup(self.source.close)
        self.addCleanup(self.store.close)
        self.config = {'api': {'max_retries': 9, 'max_requests_per_run': 60,
                               'max_credits_per_run': 800, 'monthly_credit_ceiling': 28000},
                       'helius': {'max_credits_per_run': 300, 'monthly_credit_ceiling': 900000}}
        self.budget = ProfitBudget(self.source, Mock(), self.config, now=NOW,
                                   space_check=lambda *args, **kwargs: None)
        self.budget.bind_store(self.store)
        self.snapshot = {'started_at': NOW.isoformat(), 'candidates': [{'wallet': WALLET}],
                         'ranking': [{'wallet': WALLET, 'evidence_level': 'C'}]}
        save_run(self.store, self.snapshot)
        self.original = latest_run(self.store)
        self.checkpoint = Mock()
        blocker = patch('socket.socket.connect', side_effect=AssertionError('Rede proibida'))
        blocker.start()
        self.addCleanup(blocker.stop)

    def call(self, transport):
        clients = []
        def factory(key, store, config, **kwargs):
            client = BirdeyeClient('fixture-key', store, config, transport=transport, **kwargs)
            clients.append(client)
            return client
        with patch.object(module, 'BirdeyeClient', side_effect=factory):
            summary = module.run_probe(self.store, self.config, self.budget,
                                       self.checkpoint, self.root/'reports', now=NOW)
        return summary, clients

    def test_success_only_one_uncached_post_and_keeps_latest_ranking(self):
        calls = []
        def transport(request):
            calls.append(request)
            # A tentativa já está paga no ledger antes de chegar ao transporte.
            self.assertEqual(self.source.db.execute('SELECT credits FROM budgets').fetchone()[0], 30)
            return {'data': {'tokens': [], 'summary': {}, 'private_wallet': WALLET}}
        # Se houvesse cache, este método impediria a sondagem fresca.
        with patch.object(self.store, 'cache_get', side_effect=AssertionError('Não usar cache')), \
             patch.object(self.store, 'cache_put', side_effect=AssertionError('Não escrever cache')):
            summary, clients = self.call(transport)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].method, 'POST')
        self.assertEqual(calls[0].full_url, 'https://public-api.birdeye.so'+module.ENDPOINT)
        self.assertEqual(json.loads(calls[0].data), {'wallet': WALLET, 'duration': '7d',
            'pnl_method': 'wac', 'position_scope': 'duration_only', 'sort_by': 'last_trade',
            'sort_type': 'desc', 'offset': 0, 'limit': 100})
        self.assertEqual(clients[0].config['max_retries'], 0)
        self.assertEqual(clients[0].config['max_requests_per_run'], 1)
        self.assertEqual(clients[0].config['max_credits_per_run'], 30)
        self.assertEqual(summary['counts']['wallet_probe_success'], 1)
        self.assertEqual(summary['rejection_histogram'], {'wallet_probe_http_200': 1})
        record = json.loads(self.store.get_cursor(module.CURSOR))
        self.assertEqual(record['wallet'], WALLET)
        self.assertEqual(record['response']['data']['private_wallet'], WALLET)
        self.assertEqual(record['http_status'], 200)
        self.assertEqual(latest_run(self.store), self.original)
        self.assertNotIn(WALLET, json.dumps(summary))
        self.assertEqual(self.budget.stats['wallet_probe']['birdeye']['attempted'], 30)
        self.assertEqual(self.budget.stats['wallet_probe']['helius']['reserved'], 0)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM notifications').fetchone()[0], 0)

    def test_http_429_stops_after_one_attempt_and_only_status_is_public(self):
        transport = Mock(side_effect=HTTPError('https://public-api.birdeye.so'+module.ENDPOINT,
            429, WALLET, {}, BytesIO(json.dumps({'message': WALLET}).encode())))
        summary, clients = self.call(transport)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(clients[0].requests, 1)
        self.assertEqual(summary['rejection_histogram'], {'wallet_probe_http_429': 1})
        self.assertEqual(summary['counts']['wallet_probe_success'], 0)
        self.assertNotIn(WALLET, json.dumps(summary))
        self.assertEqual(json.loads(self.store.get_cursor(module.CURSOR))['http_status'], 429)
        self.assertEqual(latest_run(self.store), self.original)
        self.assertEqual(self.budget.stats['wallet_probe']['birdeye']['refunded'], 0)

    def test_timeout_never_retries_or_exposes_error_text(self):
        transport = Mock(side_effect=TimeoutError(WALLET))
        summary, clients = self.call(transport)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(summary['rejection_histogram'], {'wallet_probe_no_http_response': 1})
        self.assertNotIn(WALLET, json.dumps(summary))
        record = json.loads(self.store.get_cursor(module.CURSOR))
        self.assertIsNone(record['http_status'])
        self.assertEqual(record['attempts'], 1)
        self.assertEqual(self.budget.stats['wallet_probe']['birdeye']['attempted'], 30)

    def test_no_known_wallet_does_not_reserve_or_call_network(self):
        save_run(self.store, {'started_at': NOW.isoformat(), 'candidates': []})
        transport = Mock(side_effect=AssertionError('Não chamar rede'))
        summary, clients = self.call(transport)
        self.assertEqual(clients, [])
        transport.assert_not_called()
        self.assertEqual(summary['counts']['wallet_probe_attempts'], 0)
        self.assertEqual(self.budget.stats, {})
        self.assertEqual(self.source.db.execute('SELECT COUNT(*) FROM budgets').fetchone()[0], 0)

    def test_uses_same_snapshot_ranking_only_when_candidates_are_absent(self):
        save_run(self.store, {'started_at': NOW.isoformat(), 'candidates': [],
                              'ranking': [{'wallet': WALLET}]})
        summary, _ = self.call(Mock(return_value={'data': {}}))
        self.assertEqual(summary['counts']['wallet_probe_attempts'], 1)
        self.assertEqual(json.loads(self.store.get_cursor(module.CURSOR))['wallet'], WALLET)
