from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
import tomllib
import unittest
from wallets.checks import evaluate, automation_signals
from wallets.models import Event, Coverage, TokenResult, Status

NOW = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
CONFIG = tomllib.loads((Path(__file__).parents[1] / 'config.toml').read_text())


def full_case():
    coverage = Coverage(NOW-timedelta(days=28), NOW, NOW, complete=True, all_tokens=True,
                        opening_inventory_known=True, fees_complete=True, marks_complete=True,
                        liquidity_complete=True, execution_complete=True, coordination_complete=True,
                        labels_complete=True)
    results = []
    events = []
    for i in range(20):
        stamp = NOW-timedelta(days=27-i, seconds=i*i*37)
        events.append(Event(str(i), 'wallet', 'token'+str(i), stamp, 'buy', D('1'), D('100'), D('0'), tx_hash='tx'+str(i)))
        monday = (stamp-timedelta(days=stamp.weekday())).date().isoformat()
        results.append(TokenResult('token'+str(i), realized=D('10'), identified_realized=D('10'),
                                   costs=D('100'), realized_cost=D('100'), open_cost=D('0'),
                                   unrealized=D('0'), entries=[D('100')], holding_seconds=[600+i*17],
                                   weekly={monday:D('10')}, reliable=True))
    # A última semana parcial recebe realização, para uma fixture totalmente aprovada.
    monday = (NOW-timedelta(days=NOW.weekday())).date().isoformat()
    results[-1].weekly[monday] = D('10')
    return events, coverage, results


def run_case(events=None, coverage=None, results=None, config=None):
    base_events, base_coverage, base_results = full_case()
    return evaluate('wallet', events if events is not None else base_events,
                    coverage if coverage is not None else base_coverage,
                    results if results is not None else base_results, [], config or CONFIG, now=NOW)


def status(result, name):
    return next(check.status for check in result.checks if check.name == name)


class CheckTests(unittest.TestCase):
    def test_all_eight_are_mandatory_and_fixture_engine_can_pass(self):
        result = run_case()
        self.assertEqual(len(result.checks), 8)
        self.assertTrue(result.eligible, [(c.name,c.reason,c.evidence) for c in result.checks])
        self.assertEqual(result.source, 'fixture')  # a camada de envio deve bloquear esta fonte
        self.assertTrue(all(c.criteria_version == CONFIG['criteria_version'] for c in result.checks))

    def test_missing_data_never_approves(self):
        coverage = Coverage(NOW-timedelta(days=90), NOW, NOW)
        result = run_case(events=[], coverage=coverage, results=[])
        self.assertFalse(result.eligible)
        self.assertEqual(status(result, 'automation'), Status.UNKNOWN)
        self.assertEqual(status(result, 'data_quality'), Status.UNKNOWN)

    def test_small_sample_cannot_pass(self):
        e,c,r = full_case(); result = run_case(e[:2], c, r[:2])
        self.assertFalse(result.eligible)
        self.assertEqual(status(result, 'sample'), Status.FAIL)

    def test_stale_data(self):
        e,c,r = full_case(); c.fetched_at = NOW-timedelta(days=2)
        self.assertEqual(status(run_case(e,c,r), 'data_quality'), Status.UNKNOWN)

    def test_future_data_blocks(self):
        e,c,r = full_case(); e.append(replace(e[0], id='future', timestamp=NOW+timedelta(seconds=1)))
        result = run_case(e,c,r)
        self.assertFalse(result.eligible)
        self.assertEqual(status(result, 'data_quality'), Status.UNKNOWN)

    def test_bot_coverage_absence_blocks_even_without_signals(self):
        e,c,r = full_case(); c.coordination_complete=False
        result = run_case(e,c,r)
        self.assertEqual(status(result, 'automation'), Status.UNKNOWN)
        self.assertEqual(status(result, 'followability'), Status.UNKNOWN)

    def test_single_weak_signal_does_not_exclude(self):
        e,c,r = full_case()
        for token in r: token.holding_seconds=[1]
        result=run_case(e,c,r)
        self.assertEqual(len(result.metrics['automation']['signals']), 1)
        self.assertEqual(status(result,'automation'), Status.PASS)
        self.assertEqual(status(result,'followability'), Status.FAIL)

    def test_strong_combined_bot_signals_fail(self):
        e,c,r=full_case()
        for token in r: token.holding_seconds=[1]
        stamp=NOW-timedelta(hours=1)
        e=[replace(e[0], id=str(i),tx_hash='spam'+str(i), timestamp=stamp+timedelta(seconds=i)) for i in range(120)]
        result=run_case(e,c,r)
        self.assertEqual(status(result,'automation'),Status.FAIL)
        self.assertFalse(result.eligible)

    def test_swap_legs_count_as_one_execution(self):
        e,c,r=full_case(); e=[e[0],replace(e[0],id='second-leg',token='other',kind='sell')]
        signals=automation_signals(e,r,CONFIG)
        self.assertEqual(signals['max_trades_per_day'],1)
        self.assertEqual(signals['same_transaction_round_trips'],0)

    def test_high_concentration_is_explicit_veto(self):
        e,c,r=full_case();r[0].realized=D('12000')
        result=run_case(e,c,r)
        self.assertEqual(status(result,'concentration'),Status.FAIL)
        self.assertFalse(result.eligible)

    def test_unknown_cost_and_missing_fees_block(self):
        e,c,r=full_case();c.fees_complete=False
        self.assertEqual(status(run_case(e,c,r),'accounting'),Status.UNKNOWN)
        e,c,r=full_case();r[0].realized=None;r[0].reliable=False
        self.assertEqual(status(run_case(e,c,r),'concentration'),Status.UNKNOWN)

    def test_open_losses_fail_and_unknown_liquidity_blocks(self):
        e,c,r=full_case();r[0].open_quantity=D('10');r[0].open_cost=D('1000');r[0].unrealized=D('-1000')
        result=run_case(e,c,r)
        self.assertEqual(status(result,'open_risk'),Status.FAIL)
        c.liquidity_complete=False
        self.assertEqual(status(run_case(e,c,r),'open_risk'),Status.UNKNOWN)

    def test_unknown_best_exclusion_is_not_complete(self):
        e,c,r=full_case();r[0].realized=None;r[0].reliable=False
        self.assertIsNone(run_case(e,c,r).metrics['without_best'])

    def test_inactive_weeks_included(self):
        e,c,r=full_case()
        for token in r:token.weekly={'2026-10-26':D('10')}
        result=run_case(e,c,r)
        self.assertEqual(status(result,'consistency'),Status.FAIL)
        self.assertGreater(result.metrics['weeks_count'],1)

    def test_wrong_wallet_or_duplicate_blocks(self):
        e,c,r=full_case();e.append(e[0]);self.assertFalse(run_case(e,c,r).eligible)
        e,c,r=full_case();e[0]=replace(e[0],wallet='different')
        self.assertEqual(status(run_case(e,c,r),'data_quality'),Status.UNKNOWN)

    def test_incomplete_bot_coverage_remains_unknown_even_with_signals(self):
        e,c,r=full_case()
        for token in r: token.holding_seconds=[1]
        stamp=NOW-timedelta(hours=1)
        e=[replace(e[0], id=str(i),tx_hash='spam'+str(i), timestamp=stamp+timedelta(seconds=i)) for i in range(120)]
        c.labels_complete=False
        result=run_case(e,c,r)
        self.assertGreaterEqual(len(result.metrics['automation']['signals']),2)
        self.assertEqual(status(result,'automation'),Status.UNKNOWN)

    def test_effective_criteria_snapshot_is_immutable_copy_and_hashed(self):
        from copy import deepcopy
        from hashlib import sha256
        from wallets.models import dumps
        config=deepcopy(CONFIG)
        result=run_case(config=config)
        expected=sha256(dumps(config).encode()).hexdigest()
        self.assertEqual(result.metrics['_criteria_hash'],expected)
        config['min_tokens']=999
        config['api']['max_pages']=999
        self.assertEqual(result.metrics['_criteria_config']['min_tokens'],20)
        self.assertNotEqual(result.metrics['_criteria_config']['api']['max_pages'],999)
        self.assertNotEqual(result.metrics['_criteria_hash'],sha256(dumps(config).encode()).hexdigest())

    def test_birdeye_label_is_context_and_needs_independent_signal(self):
        events,coverage,tokens=full_case()
        provider={'discovery_labels':[{'evidence':{'tags':['sniper','smart_trader']}}]}
        result=evaluate('wallet',events,coverage,tokens,[],CONFIG,NOW,provider)
        self.assertEqual(status(result,'automation'),Status.PASS)
        self.assertEqual(result.metrics['automation']['observed_birdeye_tags'],['smart_trader','sniper'])
        for token in tokens:token.holding_seconds=[1]
        result=evaluate('wallet',events,coverage,tokens,[],CONFIG,NOW,provider)
        self.assertEqual(status(result,'automation'),Status.FAIL)
        coverage.labels_complete=False
        result=evaluate('wallet',events,coverage,tokens,[],CONFIG,NOW,provider)
        self.assertEqual(status(result,'automation'),Status.UNKNOWN)
