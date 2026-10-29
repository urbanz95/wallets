"""Recolha -> avaliação observada -> persistência -> relatório, sem rede/envio.

Respostas Birdeye são fixtures identificadas neste teste. Nenhum resultado de
simulação é escrito no estado operacional ou enviado ao Telegram.
"""
from copy import deepcopy
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wallets.accounting import account
from wallets.checks import evaluate
from wallets.cli import load_config
from wallets.collection import Collector
from wallets.reporting import write_reports
from wallets.storage import Store
from wallets.telegram import eligible_for_delivery

NOW = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
WALLET = 'A' * 44
USDC = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
CONFIG = Path(__file__).parents[1] / 'config.toml'


def raw_closed_cycles():
    alphabet = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
    rows = []
    for index in range(20):
        token = 'B' * 42 + alphabet[index // len(alphabet)] + alphabet[index % len(alphabet)]
        stamp = NOW - timedelta(days=27-index, seconds=index*index*37)
        for kind, moment, usd in (
            ('buy', stamp, 100),
            ('sell', stamp + timedelta(seconds=3600+index*17), 110),
        ):
            meme = {'address': token, 'amount': '1000000', 'decimals': 6,
                    'type_swap': 'to' if kind == 'buy' else 'from'}
            settlement = {'address': USDC, 'amount': str(usd*1000000), 'decimals': 6,
                          'type_swap': 'from' if kind == 'buy' else 'to', 'price': '1'}
            rows.append({'tx_hash': f'fixture-{kind}-{index}', 'ins_index': 0,
                         'inner_ins_index': 0, 'owner': WALLET, 'tx_type': 'swap',
                         'block_unix_time': int(moment.timestamp()),
                         'block_number': index*10+(1 if kind == 'buy' else 2),
                         'volume_usd': str(usd), 'base': settlement, 'quote': meme})
    return rows


class RecordedBirdeye:
    """Transporte simulado: só os contratos públicos já suportados pelo coletor."""
    def __init__(self, *, incomplete=False):
        self.rows = raw_closed_cycles()
        self.incomplete = incomplete
        self.calls = []

    def request(self, method, path, params=None, body=None, **kwargs):
        self.calls.append((method, path, params, body))
        if path == '/trader/txs/seek_by_time':
            return {'success': True, 'data': {
                'items': self.rows if params['offset'] == 0 else [],
                'hasNext': self.incomplete,
            }}
        if path == '/blockchain/v1/transaction/detail':
            row = next(row for row in self.rows if row['tx_hash'] == params['signature'])
            return {'success': True, 'data': {
                'signatures': [row['tx_hash']], 'block_time': row['block_unix_time'],
                'slot': row['block_number'], 'status': 'Success', 'fee': 5000}}
        if path == '/wallet/v2/transfer':
            return {'success': True, 'data': []}
        if path == '/wallet/v2/pnl/details':
            return {'success': True, 'data': {'tokens': [], 'summary': {'unique_tokens': 0}}}
        if path == '/wallet/v2/current-net-worth':
            return {'success': True, 'data': {'items': [], 'current_timestamp': int(NOW.timestamp())},
                    'pagination': {'total': 0}}
        raise AssertionError(f'Contrato inesperado: {method} {path}')


class ObservedIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'state.sqlite'
        self.config = deepcopy(load_config(CONFIG))
        self.config['selection']['mode'] = 'observed'
        # A janela é um cenário de teste, não uma alteração aos limiares.
        self.start = NOW-timedelta(days=28)
        for target in ('socket.socket.connect', 'urllib.request.OpenerDirector.open',
                       'urllib.request.urlopen'):
            blocker = patch(target, side_effect=AssertionError('Rede proibida neste teste'))
            blocker.start()
            self.addCleanup(blocker.stop)

    def collect_and_evaluate(self, store, *, incomplete=False):
        client = RecordedBirdeye(incomplete=incomplete)
        collector_config = deepcopy(self.config)
        if incomplete:
            collector_config['api']['max_pages'] = 1
        collector = Collector(client, store, collector_config, clock=lambda: NOW)
        events, coverage, provider = collector.collect(WALLET, self.start, NOW)
        result = evaluate(WALLET, events, coverage, account(events, coverage), [],
                          self.config, now=NOW, provider_metrics=provider)
        return result, client

    def test_complete_observed_cycles_survive_reopen_and_report_without_enabling_telegram(self):
        with Store(self.db) as store:
            evaluation, client = self.collect_and_evaluate(store)
            self.assertTrue(evaluation.coverage.complete)
            for name in ('all_tokens', 'opening_inventory_known', 'fees_complete', 'marks_complete',
                         'liquidity_complete', 'execution_complete', 'coordination_complete', 'labels_complete'):
                self.assertFalse(getattr(evaluation.coverage, name), name)
            self.assertTrue(evaluation.observed_eligible,
                            [(check.name, check.reason) for check in evaluation.decision_checks])
            self.assertFalse(evaluation.eligible)
            self.assertFalse(eligible_for_delivery(evaluation, self.config, NOW))
            self.assertEqual(evaluation.metrics['selection']['metrics']['gross_result_before_fees'], Decimal('200'))
            self.assertEqual(len(store.get_events(WALLET, self.start, NOW)), 80)
            snapshot = store.save_evaluation(evaluation)
            # A mesma recolha repetida não duplica as pernas dos swaps.
            repeated, _ = self.collect_and_evaluate(store)
            self.assertEqual(len(store.get_events(WALLET, self.start, NOW)), 80)
            self.assertEqual(store.save_evaluation(repeated), snapshot)
            self.assertEqual(len(client.calls), 6)

        with Store(self.db) as reopened:
            saved = reopened.latest_evaluations()[0]
            self.assertEqual(saved.id, snapshot)
            self.assertTrue(saved.observed_eligible)
            self.assertFalse(saved.eligible)
            self.assertFalse(eligible_for_delivery(saved, self.config, NOW))
            self.assertEqual(len(saved.unverified_checks), 8)
            output = self.root / 'reports'
            write_reports([saved], output)

        with (output/'report.csv').open(encoding='utf-8', newline='') as handle:
            row = next(csv.DictReader(handle))
        self.assertEqual(row['observed_selected'], 'True')
        self.assertEqual(row['eligible_checks'], 'False')
        self.assertEqual(row['observed_closed_tokens'], '20')
        self.assertEqual(row['observed_gross_result_before_fees'], '200')
        self.assertIn('fees_usd', row['unverified'])
        html = (output/'report.html').read_text(encoding='utf-8')
        self.assertIn('SELECIONADA NOS CRITÉRIOS OBSERVADOS — APENAS RELATÓRIO', html)
        self.assertIn('PRÉ-VISUALIZAÇÃO LOCAL — SEM ENVIO', html)
        self.assertIn('NÃO VERIFICADO', html)
        raw_report = json.loads((output/'report.json').read_text(encoding='utf-8'))
        self.assertEqual(raw_report['evaluations'][0]['metrics']['selection']['policy'], 'observed-v1')

    def test_truncated_feed_blocks_observed_selection_despite_profitable_cycles(self):
        with Store(self.db) as store:
            result, _ = self.collect_and_evaluate(store, incomplete=True)
            self.assertFalse(result.coverage.complete)
            self.assertTrue(result.coverage.truncated)
            self.assertFalse(result.observed_eligible)
            self.assertFalse(eligible_for_delivery(result, self.config, NOW))
            self.assertNotEqual(result.decision_checks[0].status.value, 'APROVADA')
            self.assertEqual(result.metrics['selection']['metrics']['closed_tokens_count'], 20)
            self.assertEqual(result.metrics['selection']['metrics']['gross_result_before_fees'], Decimal('200'))


if __name__ == '__main__':
    unittest.main()
