"""Política observável com dados simulados: nunca chama APIs ou Telegram."""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
import tomllib
import unittest

from wallets.models import (
    Check, Coverage, Event, Evaluation, OBSERVED_REQUIRED_CHECKS, REQUIRED_CHECKS,
    SOL, Status, dumps,
)
from wallets.observed_policy import evaluate_observed, OBSERVED_UNVERIFIED_NAMES

NOW = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
WALLET = 'A' * 44
CONFIG = tomllib.loads((Path(__file__).parents[1] / 'config.toml').read_text())


def token(index):
    alphabet = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
    return 'B' * 42 + alphabet[index // len(alphabet)] + alphabet[index % len(alphabet)]


def full_observed_case(count=20, hold_seconds=3600):
    coverage = Coverage(NOW - timedelta(days=28), NOW, NOW, source='birdeye+helius')
    events = []
    for index in range(count):
        stamp = NOW - timedelta(days=27-index, seconds=index*index*37)
        mint = token(index)
        events.extend([
            Event('buy'+str(index), WALLET, mint, stamp, 'buy', D(1), D(100), None,
                  tx_hash='buytx'+str(index), source='birdeye'),
            Event('sell'+str(index), WALLET, mint,
                  stamp+timedelta(seconds=hold_seconds+index*17), 'sell', D(1), D(110), None,
                  tx_hash='selltx'+str(index), source='birdeye'),
        ])
    provider = {'pages_complete': {'trades': True, 'transfers': True, 'pnl': False, 'holdings': False},
                'history_scope': {'start': coverage.start.isoformat(), 'end': coverage.end.isoformat()}}
    config = deepcopy(CONFIG)
    config['selection'] = dict(config.get('selection', {}), min_closed_token_fraction=0.8)
    return events, coverage, provider, config


def run_case(events=None, coverage=None, provider=None, config=None, origins=(), now=NOW):
    base_events, base_coverage, base_provider, base_config = full_observed_case()
    return evaluate_observed(
        WALLET, base_events if events is None else events,
        base_coverage if coverage is None else coverage,
        base_provider if provider is None else provider, origins,
        base_config if config is None else config, now,
    )


def status(selection, name):
    return next(check['status'] for check in selection['checks'] if check['name'] == name)


def all_pass(selection):
    return all(check['status'] == Status.PASS for check in selection['checks'])


class ObservedPolicyTests(unittest.TestCase):
    def test_real_source_path_can_pass_without_global_flags_and_preserves_unknowns(self):
        events, coverage, provider, config = full_observed_case()
        before = asdict(coverage)
        selection = run_case(events, coverage, provider, config)
        self.assertTrue(all_pass(selection), [(c['name'], c['reason'], c['evidence']) for c in selection['checks']])
        self.assertEqual(tuple(c['name'] for c in selection['checks']), OBSERVED_REQUIRED_CHECKS)
        self.assertEqual(asdict(coverage), before)
        self.assertFalse(coverage.complete)
        self.assertFalse(coverage.opening_inventory_known)
        self.assertEqual(selection['metrics']['gross_result_before_fees'], D(200))
        self.assertEqual(selection['metrics']['closed_tokens_count'], 20)
        self.assertIsNone(selection['metrics']['open_losses'])
        self.assertFalse(selection['metrics']['global_wallet_performance_verified'])
        self.assertEqual(tuple(row['name'] for row in selection['unverified']), OBSERVED_UNVERIFIED_NAMES)
        self.assertTrue(all(row['reason'] for row in selection['unverified']))
        self.assertIn('BRUTO', selection['scope'])
        self.assertIn('taxas', selection['scope'])
        self.assertEqual(selection['checks'][0]['coverage'], before)

    def test_selection_roundtrip_passes_local_research_but_not_strict_notification(self):
        import json
        events, coverage, provider, config = full_observed_case()
        selection = run_case(events, coverage, provider, config)
        strict = [Check(name, Status.UNKNOWN, 'Sem evidência global.', {}, coverage.start,
                        coverage.end, asdict(coverage), NOW, config['criteria_version']) for name in REQUIRED_CHECKS]
        evaluation = Evaluation(WALLET, NOW, config['criteria_version'], coverage,
                                strict, {'selection': json.loads(dumps(selection))}, source='birdeye+helius')
        self.assertTrue(evaluation.observed_eligible)
        self.assertFalse(evaluation.eligible)
        self.assertFalse(evaluation.strict_eligible)

    def test_missing_or_quota_truncated_history_never_passes(self):
        for provider in ({}, {'pages_complete': {'trades': True, 'transfers': False}},
                         {'helius': {'pages_complete': True, 'truncated': True, 'failures': []}},
                         {'helius': {'pages_complete': True, 'truncated': False, 'failures': ['Quota atingida.']}}):
            with self.subTest(provider=provider):
                selection = run_case(provider=provider)
                self.assertEqual(status(selection, 'observed_data'), Status.UNKNOWN)
                self.assertFalse(all_pass(selection))

    def test_one_complete_helius_history_is_sufficient_for_observed_scope(self):
        provider = {'pages_complete': {'trades': False, 'transfers': False},
                    'helius': {'pages_complete': True, 'truncated': False, 'failures': [],
                               'reconciliation': {'conflicting_transactions': []}}}
        selection = run_case(provider=provider)
        self.assertTrue(all_pass(selection))
        data = selection['checks'][0]['evidence']
        self.assertTrue(data['helius_history_pages_complete'])
        self.assertFalse(data['birdeye_history_pages_complete'])

    def test_scope_metadata_must_cover_entire_requested_window(self):
        _, coverage, provider, _ = full_observed_case()
        provider['history_scope']['start'] = (coverage.start+timedelta(days=1)).isoformat()
        self.assertEqual(status(run_case(provider=provider), 'observed_data'), Status.UNKNOWN)

    def test_conflicting_sources_are_an_explicit_veto(self):
        _, _, provider, _ = full_observed_case()
        provider['helius'] = {'reconciliation': {'conflicting_transactions': ['disagreement']}}
        selection = run_case(provider=provider)
        self.assertEqual(status(selection, 'observed_data'), Status.FAIL)
        self.assertFalse(all_pass(selection))

    def test_future_duplicate_mixed_source_and_mixed_wallet_are_blocked(self):
        events, _, _, _ = full_observed_case()
        variants = [events+[replace(events[0], id='future', timestamp=NOW+timedelta(seconds=1))],
                    events+[events[0]],
                    [replace(events[0], wallet='C'*44)]+events[1:],
                    [replace(events[0], source='fixture')]+events[1:]]
        for index, rows in enumerate(variants):
            with self.subTest(index=index):
                selection = run_case(events=rows)
                self.assertEqual(status(selection, 'observed_data'), Status.FAIL)
                self.assertFalse(all_pass(selection))

    def test_fixture_or_stale_source_never_passes(self):
        _, coverage, _, _ = full_observed_case()
        selection = run_case(coverage=replace(coverage, source='fixture'))
        self.assertFalse(all_pass(selection))
        self.assertFalse(selection['checks'][0]['evidence']['live_source'])
        selection = run_case(now=NOW+timedelta(hours=25))
        self.assertEqual(status(selection, 'observed_data'), Status.UNKNOWN)

    def test_small_sample_does_not_pass_despite_positive_returns(self):
        events, coverage, provider, config = full_observed_case(count=2)
        selection = run_case(events, coverage, provider, config)
        self.assertEqual(status(selection, 'observed_sample'), Status.FAIL)
        self.assertFalse(all_pass(selection))

    def test_unknown_transfer_cost_is_excluded_and_never_counted_as_profit(self):
        events, _, _, _ = full_observed_case()
        events.extend([
            Event('gift', WALLET, token(30), NOW-timedelta(days=4), 'transfer_in', D(1), source='helius'),
            Event('gift-sale', WALLET, token(30), NOW-timedelta(days=3), 'sell', D(1), D(10000), source='helius'),
        ])
        selection = run_case(events=events)
        measured = selection['metrics']
        self.assertEqual(measured['unknown_tokens_count'], 1)
        self.assertEqual(measured['closed_tokens_count'], 20)
        self.assertEqual(measured['gross_result_before_fees'], D(200))
        self.assertEqual(measured['excluded_tokens'][0]['token'], token(30))
        self.assertEqual(measured['excluded_tokens'][0]['state'], 'UNKNOWN')
        self.assertNotIn(token(30), measured['per_token'])

    def test_too_many_uninterpretable_tokens_fail_scope(self):
        events, _, _, _ = full_observed_case()
        for index in range(6):
            events.append(Event('unknown'+str(index), WALLET, token(30+index),
                                NOW-timedelta(days=4), 'unknown', D(0), source='helius'))
        selection = run_case(events=events)
        self.assertEqual(selection['metrics']['unknown_tokens_count'], 6)
        self.assertEqual(status(selection, 'observed_scope'), Status.FAIL)
        self.assertFalse(all_pass(selection))

    def test_open_positions_are_counted_and_never_reported_as_zero_loss(self):
        events, _, _, _ = full_observed_case()
        events.append(Event('still-open', WALLET, token(30), NOW-timedelta(days=3),
                            'buy', D(10), D(1000), source='birdeye'))
        selection = run_case(events=events)
        measured = selection['metrics']
        self.assertEqual(measured['open_tokens_count'], 1)
        self.assertEqual(measured['open_positions_count'], 1)
        self.assertIsNone(measured['open_losses'])
        self.assertIsNone(measured['unrealized_total'])
        self.assertEqual(measured['gross_result_before_fees'], D(200))
        self.assertEqual(measured['observed_tokens_count'], 21)
        self.assertEqual(len(measured['excluded_tokens']), 1)
        warning = next(row['reason'] for row in selection['unverified'] if row['name']=='open_positions')
        self.assertIn('1 tokens abertos', warning)

    def test_high_concentration_cannot_be_compensated_by_other_metrics(self):
        events, _, _, _ = full_observed_case()
        events = [replace(event, usd_value=D(10100)) if event.id=='sell0' else event for event in events]
        selection = run_case(events=events)
        self.assertEqual(status(selection, 'observed_concentration'), Status.FAIL)
        self.assertEqual(status(selection, 'observed_consistency'), Status.PASS)
        self.assertFalse(all_pass(selection))

    def test_excluding_all_discovery_tokens_blocks_concentration(self):
        selection = run_case(origins=[token(index) for index in range(20)])
        self.assertEqual(selection['metrics']['without_discovery'], D(0))
        self.assertEqual(status(selection, 'observed_concentration'), Status.FAIL)

    def test_combined_fast_and_intense_activity_fails_automation(self):
        events, coverage, provider, config = full_observed_case(hold_seconds=1)
        # Remover o escalonamento de manutenção da fixture para todos fecharem em segundos.
        by_id = {event.id: event for event in events}
        events = [replace(event, timestamp=by_id['buy'+event.id[4:]].timestamp+timedelta(seconds=1))
                  if event.kind=='sell' else event for event in events]
        for index in range(120):
            events.append(Event('native'+str(index), WALLET, SOL, NOW-timedelta(hours=1)+timedelta(seconds=index),
                                'buy', D(1), D(1), tx_hash='native-tx'+str(index), source='helius'))
        selection = run_case(events, coverage, provider, config)
        self.assertGreaterEqual(len(selection['metrics']['automation']['signals']), 2)
        self.assertEqual(status(selection, 'observed_automation'), Status.FAIL)
        self.assertFalse(all_pass(selection))

    def test_one_contextual_tag_alone_does_not_exclude(self):
        _, _, provider, _ = full_observed_case()
        provider['discovery_labels'] = [{'evidence': {'tags': ['sniper']}}]
        selection = run_case(provider=provider)
        self.assertEqual(len(selection['metrics']['automation']['signals']), 1)
        self.assertEqual(status(selection, 'observed_automation'), Status.PASS)
        self.assertTrue(all_pass(selection))

    def test_fast_holds_fail_followability_even_with_only_one_bot_signal(self):
        events, coverage, provider, config = full_observed_case(hold_seconds=1)
        by_id = {event.id: event for event in events}
        events = [replace(event, timestamp=by_id['buy'+event.id[4:]].timestamp+timedelta(seconds=1))
                  if event.kind=='sell' else event for event in events]
        selection = run_case(events, coverage, provider, config)
        self.assertEqual(status(selection, 'observed_automation'), Status.PASS)
        self.assertEqual(status(selection, 'observed_followability'), Status.FAIL)

    def test_inactive_weeks_are_included_in_consistency(self):
        _, coverage, provider, _ = full_observed_case()
        coverage = replace(coverage, start=NOW-timedelta(days=90))
        provider['history_scope']['start'] = coverage.start.isoformat()
        selection = run_case(coverage=coverage, provider=provider)
        self.assertGreater(selection['metrics']['weeks_count'], 10)
        self.assertEqual(status(selection, 'observed_consistency'), Status.FAIL)
        self.assertFalse(all_pass(selection))

    def test_invalid_fraction_configuration_fails_closed(self):
        _, _, _, config = full_observed_case()
        for fraction in (0, 1.1, 'NaN'):
            with self.subTest(fraction=fraction), self.assertRaises(ValueError):
                config['selection']['min_closed_token_fraction'] = fraction
                run_case(config=config)


if __name__ == '__main__':
    unittest.main()
