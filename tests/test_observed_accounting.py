"""Amostra observada por token, sem dependência de flags globais fictícias."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import unittest

from wallets.models import Event, SOL
from wallets.observed_accounting import summarize_observed

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
END = START + timedelta(days=90)


def event(identity, kind, quantity, value=None, day=1, token='token', fee=None, **kwargs):
    return Event(identity, 'wallet', token, START + timedelta(days=day), kind,
                 D(quantity), None if value is None else D(value),
                 None if fee is None else D(fee), **kwargs)


class ObservedAccountingTests(unittest.TestCase):
    def summarize(self, rows):
        return summarize_observed(rows, START, END)

    def test_missing_fees_still_allows_explicit_gross_closed_result(self):
        result = self.summarize([event('b', 'buy', '10', '100'), event('s', 'sell', '10', '150', day=2)])
        token, = result['token_results']
        self.assertEqual(token.realized, D('50'))
        self.assertEqual(result['gross_result_before_fees'], D('50'))
        self.assertTrue(token.reliable)
        self.assertFalse(result['fees_complete'])
        self.assertEqual(result['known_fees_usd'], D('0'))
        self.assertIn('Não é lucro líquido', result['result_definition'])

    def test_known_fees_are_separate_from_gross_result(self):
        result = self.summarize([event('b', 'buy', '1', '100', fee='10'), event('s', 'sell', '1', '105', fee='10', day=2)])
        self.assertEqual(result['gross_result_before_fees'], D('5'))
        self.assertEqual(result['known_fees_usd'], D('20'))
        self.assertEqual(result['token_results'][0].fees, D('20'))
        self.assertTrue(result['fees_complete'])

    def test_partial_sales_repurchase_fifo_and_weekly_results(self):
        rows = [event('b1', 'buy', '10', '100'), event('s1', 'sell', '4', '60', day=3),
                event('b2', 'buy', '2', '30', day=10), event('s2', 'sell', '8', '160', day=15)]
        result = self.summarize(rows)
        token, = result['token_results']
        self.assertEqual(token.realized, D('90'))
        self.assertEqual(token.realized_cost, D('130'))
        self.assertEqual(sum(token.weekly.values()), D('90'))
        self.assertEqual(len(token.weekly), 2)
        self.assertEqual(token.entries, [D('100'), D('30')])
        self.assertEqual(len(token.holding_seconds), 3)
        self.assertEqual(result['closed_fraction'], D('1'))

    def test_many_roundtrips_same_token_are_one_sample(self):
        rows = [event('b1', 'buy', '1', '10'), event('s1', 'sell', '1', '20', day=2),
                event('b2', 'buy', '1', '20', day=3), event('s2', 'sell', '1', '30', day=4)]
        result = self.summarize(rows)
        self.assertEqual(result['closed_tokens_count'], 1)
        self.assertEqual(len(result['token_results']), 1)
        self.assertEqual(result['gross_result_before_fees'], D('20'))

    def test_open_repurchase_excludes_prior_gain_on_that_token(self):
        rows = [event('b1', 'buy', '1', '10'), event('s1', 'sell', '1', '20', day=2),
                event('b2', 'buy', '1', '100', day=3)]
        result = self.summarize(rows)
        self.assertEqual(result['token_results'], [])
        self.assertIsNone(result['gross_result_before_fees'])
        self.assertEqual(result['open_tokens_count'], 1)
        row, = result['per_token']
        self.assertEqual(row['state'], 'OPEN')
        self.assertEqual(row['gross_result_before_fees'], D('10'))
        self.assertEqual((row['open_quantity'], row['open_cost_usd']), (D('1'), D('100')))
        self.assertEqual(result['excluded_tokens'][0]['state'], 'OPEN')

    def test_losing_closed_and_open_tokens_both_remain_in_universe(self):
        rows = [event('b1', 'buy', '1', '10'), event('s1', 'sell', '1', '5', day=2),
                event('b2', 'buy', '10', '100', token='open')]
        result = self.summarize(rows)
        self.assertEqual(result['observed_tokens_count'], 2)
        self.assertEqual(result['closed_tokens_count'], 1)
        self.assertEqual(result['open_tokens_count'], 1)
        self.assertEqual(result['closed_fraction'], D('0.5'))
        self.assertEqual(result['gross_result_before_fees'], D('-5'))

    def test_transfer_airdrop_and_transfer_out_never_become_free_basis(self):
        for kind in ('transfer_in', 'airdrop'):
            with self.subTest(kind=kind):
                result = self.summarize([event('in', kind, '10', '0'), event('sell', 'sell', '10', '1000', day=2)])
                self.assertEqual(result['unknown_tokens_count'], 1)
                self.assertEqual(result['closed_tokens_count'], 0)
                self.assertIsNone(result['per_token'][0]['gross_result_before_fees'])
        result = self.summarize([event('buy', 'buy', '1', '10'), event('out', 'transfer_out', '1', day=2)])
        self.assertEqual(result['unknown_tokens_count'], 1)

    def test_sale_before_buy_and_insufficient_lots_remain_unknown(self):
        for rows in ([event('s', 'sell', '1', '100'), event('b', 'buy', '1', '10', day=2)],
                     [event('b', 'buy', '1', '10'), event('s', 'sell', '2', '100', day=2)]):
            result = self.summarize(rows)
            self.assertEqual(result['unknown_tokens_count'], 1)
            self.assertIsNone(result['gross_result_before_fees'])

    def test_missing_or_zero_buy_cost_and_missing_sell_value_are_not_profit(self):
        for purchase in (None, '0'):
            result = self.summarize([event('b', 'buy', '1', purchase), event('s', 'sell', '1', '100', day=2)])
            self.assertEqual(result['unknown_tokens_count'], 1)
        result = self.summarize([event('b', 'buy', '1', '10'), event('s', 'sell', '1', day=2)])
        self.assertIsNone(result['gross_result_before_fees'])

    def test_zero_sale_value_is_a_known_full_loss(self):
        result = self.summarize([event('b', 'buy', '1', '10'), event('s', 'sell', '1', '0', day=2)])
        self.assertEqual(result['gross_result_before_fees'], D('-10'))

    def test_multistep_liquidity_and_ambiguous_same_second_do_not_pass(self):
        for first in (event('b', 'buy', '1', '10', multi_step=True),
                      event('b', 'liquidity', '1', '10'), event('b', 'unknown', '1')):
            result = self.summarize([first, event('s', 'sell', '1', '20', day=2)])
            self.assertEqual(result['unknown_tokens_count'], 1)
            self.assertEqual(result['unclassified_transactions_count'], 1)
        result = self.summarize([event('b', 'buy', '1', '10'), event('s', 'sell', '1', '20')])
        self.assertEqual(result['unknown_tokens_count'], 1)

    def test_identical_duplicates_do_not_double_count_but_changed_duplicate_fails(self):
        buy = event('b', 'buy', '1', '10', fee='1')
        sell = event('s', 'sell', '1', '20', day=2, fee='1')
        result = self.summarize([buy, buy, sell, sell])
        self.assertEqual(result['gross_result_before_fees'], D('10'))
        self.assertEqual(result['known_fees_usd'], D('2'))
        with self.assertRaises(ValueError):
            self.summarize([buy, replace(buy, usd_value=D('12'))])

    def test_future_before_window_mixed_wallets_and_invalid_numbers_fail_closed(self):
        valid = event('b', 'buy', '1', '10')
        invalid = [replace(valid, timestamp=END+timedelta(seconds=1)),
                   replace(valid, timestamp=START-timedelta(seconds=1)),
                   replace(valid, quantity=D('NaN')), replace(valid, quantity=1.1),
                   replace(valid, usd_value=D('-1')), replace(valid, fee_usd=D('Infinity'))]
        for row in invalid:
            with self.subTest(row=row), self.assertRaises(ValueError):
                self.summarize([row])
        with self.assertRaises(ValueError):
            self.summarize([valid, replace(valid, wallet='another')])
        with self.assertRaises(ValueError):
            summarize_observed([valid], END, START)

    def test_settlement_failed_and_wrap_are_not_independent_tokens(self):
        rows = [event('solb', 'buy', '1', '100', token=SOL),
                event('sols', 'sell', '1', '120', day=2, token=SOL),
                event('f', 'failed', '0', token='failed', fee='1'),
                event('w', 'wrap', '1', token='wrapped', fee='2')]
        result = self.summarize(rows)
        self.assertEqual(result['observed_tokens_count'], 0)
        self.assertEqual(result['token_results'], [])
        self.assertIsNone(result['closed_fraction'])
        self.assertIsNone(result['gross_result_before_fees'])
        self.assertEqual(result['known_fees_usd'], D('3'))

    def test_decimal_units_above_float_precision_are_exact(self):
        quantity = '900719925474099312345'
        result = self.summarize([event('b', 'buy', quantity, '100000000000000000000.01'),
                                 event('s', 'sell', quantity, '100000000000000000000.02', day=2)])
        self.assertEqual(result['gross_result_before_fees'], D('0.01'))
