"""Perfil experimental de 7 dias; dados simulados nunca enviados ao Telegram."""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
import tomllib
import unittest

from wallets.models import Check, Coverage, Event, Evaluation, REQUIRED_CHECKS, Status, dumps
from wallets.observed_accounting import summarize_observed
from wallets.observed_policy import evaluate_observed
from wallets.telegram import eligible_for_delivery

NOW = datetime(2026, 10, 29, 17, 23, tzinfo=timezone.utc)
WALLET = 'A' * 44
CONFIG = tomllib.loads((Path(__file__).parents[1] / 'config.toml').read_text())


def case(count=20):
    config = deepcopy(CONFIG)
    config['recent'] = dict(enabled=True, window_days=7, min_activity_days=4,
                            min_profitable_days=3, require_activity_hours=24)
    # O perfil recente não usa os antigos mínimos temporais impossíveis.
    config['min_activity_days'] = 14
    config['min_weeks'] = 4
    config['min_profitable_week_fraction'] = 1
    start = NOW-timedelta(days=7)
    coverage = Coverage(start, NOW, NOW, source='birdeye+helius')
    provider = {'pages_complete': {'trades': True, 'transfers': True},
                'history_scope': {'start': start, 'end': NOW}}
    alphabet = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
    events = []
    for index in range(count):
        mint = 'B'*42 + alphabet[index // len(alphabet)] + alphabet[index % len(alphabet)]
        stamp = start+timedelta(days=(index % 4)*2, hours=2,
                              seconds=index*index*13)
        events += [
            Event(f'b{index}', WALLET, mint, stamp, 'buy', D(1), D(100),
                  tx_hash=f'buy-{index}', source='birdeye'),
            Event(f's{index}', WALLET, mint, stamp+timedelta(seconds=3600+index*37),
                  'sell', D(1), D(110), tx_hash=f'sell-{index}', source='birdeye'),
        ]
    return events, coverage, provider, config


def run(events, coverage, provider, config, now=NOW):
    return evaluate_observed(WALLET, events, coverage, provider, [], config, now)


def status(selection, name):
    return next(check['status'] for check in selection['checks'] if check['name'] == name)


class RecentPolicyTests(unittest.TestCase):
    def test_four_active_days_can_pass_without_four_weeks_or_fourteen_days(self):
        events, coverage, provider, config = case()
        before = asdict(coverage)
        selection = run(events, coverage, provider, config)
        self.assertTrue(all(c['status'] == Status.PASS for c in selection['checks']), selection['checks'])
        m = selection['metrics']
        self.assertEqual(m['profile'], 'recent-7d')
        self.assertEqual(m['tokens_count'], 20)
        self.assertEqual(m['activity_days'], 4)
        self.assertEqual(m['profitable_days_count'], 4)
        self.assertEqual(m['days_count'], 7)
        self.assertEqual(m['gross_result_before_fees'], D(200))
        self.assertEqual(len(m['daily']), 7)
        self.assertEqual(sum(m['daily'].values()), D(200))
        self.assertEqual(sum(value == 0 for value in m['daily'].values()), 3)
        self.assertEqual(asdict(coverage), before)
        self.assertFalse(coverage.all_tokens)
        self.assertFalse(coverage.opening_inventory_known)
        self.assertFalse(coverage.fees_complete)
        self.assertIn('7 dias', selection['scope'])
        self.assertEqual(len(selection['unverified']), 8)
        self.assertTrue(selection['checks'][0]['evidence']['activity_within_required_hours'])

    def test_intervals_are_anchored_at_cutoff_not_calendar_midnight(self):
        events, coverage, provider, config = case()
        selection = run(events, coverage, provider, config)
        periods = selection['metrics']['daily_periods']
        self.assertEqual(periods[0]['start'], NOW-timedelta(days=7))
        self.assertEqual(periods[-1]['end'], NOW)
        for index, row in enumerate(periods):
            self.assertEqual(row['end']-row['start'], timedelta(days=1))
            self.assertEqual(row['start'].hour, 17)
            self.assertEqual(row['start'].minute, 23)
            if index:
                self.assertEqual(row['start'], periods[index-1]['end'])

    def test_sale_result_belongs_to_sale_block_including_partial_sales(self):
        events, coverage, provider, config = case()
        buy = events[0]
        events = [event for event in events if event.id != 's0']
        events += [replace(buy, id='partial', kind='sell', quantity=D('.4'), usd_value=D(44),
                           timestamp=coverage.start+timedelta(days=1, hours=1), tx_hash='partial'),
                   replace(buy, id='final', kind='sell', quantity=D('.6'), usd_value=D(66),
                           timestamp=coverage.start+timedelta(days=3, hours=1), tx_hash='final')]
        selection = run(events, coverage, provider, config)
        periods = selection['metrics']['daily_periods']
        self.assertEqual(periods[1]['gross_result_before_fees'], D(4))
        self.assertEqual(periods[3]['gross_result_before_fees'], D(6))
        self.assertEqual(sum(row['gross_result_before_fees'] for row in periods), D(200))

    def test_only_old_activity_blocks_data_even_with_complete_pages(self):
        events, coverage, provider, config = case()
        # Mover os dias mais recentes para o bloco anterior: tudo acaba antes de T-24h.
        events = [replace(event, timestamp=event.timestamp-timedelta(days=1))
                  if event.timestamp >= NOW-timedelta(days=1) else event for event in events]
        selection = run(events, coverage, provider, config)
        self.assertEqual(status(selection, 'observed_data'), Status.FAIL)
        self.assertFalse(selection['checks'][0]['evidence']['activity_within_required_hours'])

    def test_zero_quantity_event_cannot_supply_recent_activity(self):
        events, coverage, provider, config = case()
        events = [replace(event, timestamp=event.timestamp-timedelta(days=1))
                  if event.timestamp >= NOW-timedelta(days=1) else event for event in events]
        events.append(Event('empty', WALLET, 'C'*44, NOW-timedelta(hours=1),
                            'buy', D(0), D(100), source='birdeye'))
        selection = run(events, coverage, provider, config)
        self.assertEqual(status(selection, 'observed_data'), Status.FAIL)
        self.assertFalse(selection['checks'][0]['evidence']['activity_within_required_hours'])

    def test_eight_day_old_future_and_exact_cutoff_events_are_rejected(self):
        events, coverage, provider, config = case()
        for stamp in (NOW-timedelta(days=8), NOW, NOW+timedelta(seconds=1)):
            extra = replace(events[0], id='out-of-window', timestamp=stamp)
            with self.subTest(stamp=stamp):
                selection = run(events+[extra], coverage, provider, config)
                self.assertEqual(status(selection, 'observed_data'), Status.FAIL)
                self.assertIsNone(selection['metrics']['gross_result_before_fees'])
                self.assertEqual(sum(selection['metrics']['daily'].values()), D(0))

    def test_exact_start_is_included_but_not_the_cutoff(self):
        events, coverage, provider, config = case()
        events[0] = replace(events[0], timestamp=coverage.start)
        selection = run(events, coverage, provider, config)
        self.assertEqual(status(selection, 'observed_data'), Status.PASS)
        summary = summarize_observed(events, coverage.start, NOW, end_exclusive=True)
        self.assertEqual(summary['closed_tokens_count'], 20)
        with self.assertRaises(ValueError):
            summarize_observed([replace(events[0], timestamp=NOW)], coverage.start, NOW,
                               end_exclusive=True)

    def test_non_seven_day_window_cannot_pass(self):
        events, coverage, provider, config = case()
        coverage = replace(coverage, start=NOW-timedelta(days=8))
        provider['history_scope']['start'] = coverage.start
        selection = run(events, coverage, provider, config)
        self.assertEqual(status(selection, 'observed_data'), Status.FAIL)
        self.assertFalse(selection['checks'][0]['evidence']['exact_window_valid'])
        self.assertIsNone(selection['metrics']['gross_result_before_fees'])

    def test_small_sample_and_single_profitable_day_cannot_pass(self):
        events, coverage, provider, config = case(count=3)
        events[0] = replace(events[0], timestamp=NOW-timedelta(hours=2))
        events[1] = replace(events[1], timestamp=NOW-timedelta(hours=1))
        selection = run(events, coverage, provider, config)
        self.assertEqual(status(selection, 'observed_data'), Status.PASS)
        self.assertEqual(status(selection, 'observed_sample'), Status.FAIL)
        events, coverage, provider, config = case()
        for index, event in enumerate(events):
            if event.kind == 'sell':
                events[index] = replace(event, timestamp=NOW-timedelta(hours=3)+timedelta(seconds=index*17))
        selection = run(events, coverage, provider, config)
        self.assertEqual(selection['metrics']['profitable_days_count'], 1)
        self.assertEqual(status(selection, 'observed_consistency'), Status.FAIL)

    def test_global_unknowns_and_strict_telegram_gate_are_preserved(self):
        import json
        events, coverage, provider, config = case()
        selection = run(events, coverage, provider, config)
        strict = [Check(name, Status.UNKNOWN, 'Prova integral por apurar.', {}, coverage.start,
                        coverage.end, asdict(coverage), NOW, config['criteria_version'])
                  for name in REQUIRED_CHECKS]
        evaluation = Evaluation(WALLET, NOW, config['criteria_version'], coverage, strict,
                                {'selection': json.loads(dumps(selection))}, source=coverage.source)
        self.assertTrue(evaluation.observed_eligible)
        self.assertFalse(evaluation.eligible)
        self.assertFalse(eligible_for_delivery(evaluation, config, NOW))

    def test_unknown_cost_and_open_loser_still_excluded_and_counted(self):
        events, coverage, provider, config = case()
        events += [Event('unknown-basis', WALLET, 'C'*44, NOW-timedelta(hours=5),
                         'transfer_in', D(1), source='helius'),
                   Event('sale-unknown', WALLET, 'C'*44, NOW-timedelta(hours=4),
                         'sell', D(1), D(10000), source='helius'),
                   Event('open', WALLET, 'D'*44, NOW-timedelta(hours=3),
                         'buy', D(1), D(5000), source='birdeye')]
        selection = run(events, coverage, provider, config)
        self.assertEqual(selection['metrics']['unknown_tokens_count'], 1)
        self.assertEqual(selection['metrics']['open_tokens_count'], 1)
        self.assertEqual(selection['metrics']['gross_result_before_fees'], D(200))
        self.assertEqual(sum(selection['metrics']['daily'].values()), D(200))
        self.assertIsNone(selection['metrics']['open_losses'])

    def test_recent_config_cannot_create_impossible_or_invalid_daily_limits(self):
        events, coverage, provider, config = case()
        for key, value in (('window_days', 8), ('min_activity_days', 8),
                           ('min_profitable_days', 8), ('require_activity_hours', 0),
                           ('enabled', 'true'), ('min_activity_days', True)):
            invalid = deepcopy(config)
            invalid['recent'][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                run(events, coverage, provider, invalid)


if __name__ == '__main__':
    unittest.main()
