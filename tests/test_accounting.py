import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from wallets.accounting import account
from wallets.models import Coverage, Event, SOL

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
END = START + timedelta(days=30)


def coverage(**overrides):
    values = dict(start=START, end=END, fetched_at=END, complete=True,
                  all_tokens=True, opening_inventory_known=True, fees_complete=True)
    values.update(overrides)
    return Coverage(**values)


def event(identity, kind, quantity, value=None, fee='0', day=1, token='token', **kwargs):
    return Event(identity, 'wallet', token, START + timedelta(days=day), kind,
                 D(quantity), None if value is None else D(value),
                 None if fee is None else D(fee), **kwargs)


class AccountingTests(unittest.TestCase):
    def test_fifo_partial_sales_repurchase_and_fees(self):
        events = [event('a', 'buy', '10', '100', fee='1'),
                  event('b', 'sell', '4', '60', fee='2', day=2),
                  event('c', 'buy', '2', '30', fee='1', day=3),
                  event('d', 'sell', '7', '140', fee='3', day=4)]
        result, = account(events, coverage())
        # (60-2)-(101*4/10)=17.6; (140-3)-(60.6+15.5)=60.9
        self.assertEqual(result.realized, D('78.5'))
        self.assertEqual(result.costs, D('130'))
        self.assertEqual(result.realized_cost, D('116.5'))
        self.assertEqual(result.fees, D('7'))
        self.assertEqual(result.open_quantity, D('1'))
        self.assertEqual(result.open_cost, D('15.5'))
        self.assertTrue(result.reliable)

    def test_transfer_received_never_free_purchase(self):
        events = [event('a', 'transfer_in', '10', '100'),
                  event('b', 'sell', '10', '1000', day=2)]
        result, = account(events, coverage())
        self.assertIsNone(result.realized)
        self.assertEqual(result.identified_realized, D('0'))
        self.assertFalse(result.reliable)

    def test_airdrop_and_unknown_transfer_open_cost(self):
        result, = account([event('a', 'airdrop', '12', '50')], coverage(),
                         {'token': {'price_usd': D('20'), 'timestamp': END}})
        self.assertIsNone(result.open_cost)
        self.assertIsNone(result.unrealized)
        self.assertEqual(result.unknown_cost_quantity, D('12'))

    def test_mixed_unknown_lot_realizes_only_identified_portion(self):
        events = [event('a', 'buy', '5', '50'),
                  event('b', 'transfer_in', '5', day=2),
                  event('c', 'sell', '10', '200', day=3)]
        result, = account(events, coverage())
        self.assertEqual(result.identified_realized, D('50'))
        self.assertIsNone(result.realized)

    def test_unknown_fee_never_net_profit(self):
        events = [event('a', 'buy', '1', '10', fee=None),
                  event('b', 'sell', '1', '20', day=2)]
        result, = account(events, coverage())
        self.assertIsNone(result.realized)
        self.assertEqual(result.identified_realized, D('10'))
        self.assertFalse(result.reliable)

    def test_missing_purchase_does_not_invent_zero_cost(self):
        result, = account([event('a', 'sell', '2', '500')], coverage())
        self.assertIsNone(result.realized)
        self.assertEqual(result.identified_realized, D('0'))
        self.assertEqual(result.open_quantity, D('0'))

    def test_open_losing_position_kept_and_current_mark_not_retroactive(self):
        result, = account([event('a', 'buy', '10', '100')], coverage(),
                         {'token': {'price_usd': D('2'), 'timestamp': END}})
        self.assertEqual(result.open_cost, D('100'))
        self.assertEqual(result.unrealized, D('-80'))
        self.assertEqual(result.realized, D('0'))
        self.assertEqual(result.costs, D('100'))

    def test_unmarked_open_position_is_unknown(self):
        result, = account([event('a', 'buy', '1', '10')], coverage())
        self.assertIsNone(result.unrealized)

    def test_duplicate_event_does_not_duplicate_position(self):
        first = event('a', 'buy', '10', '100')
        result, = account([first, first], coverage())
        self.assertEqual(result.open_quantity, D('10'))
        with self.assertRaisesRegex(ValueError, 'contraditório'):
            account([first, event('a', 'buy', '11', '100')], coverage())

    def test_truncation_and_unknown_opening_inventory_block_reliable(self):
        events = [event('a', 'buy', '1', '10'), event('b', 'sell', '1', '20', day=2)]
        for changes in ({'truncated': True}, {'opening_inventory_known': False}, {'complete': False}, {'all_tokens': False}, {'fees_complete': False}):
            with self.subTest(changes=changes):
                result, = account(events, coverage(**changes))
                self.assertIsNone(result.realized)
                self.assertFalse(result.reliable)

    def test_information_future_rejected_in_trade_and_mark(self):
        with self.assertRaisesRegex(ValueError, 'futura'):
            account([event('a', 'buy', '1', '10', day=31)], coverage())
        with self.assertRaisesRegex(ValueError, 'futura'):
            account([event('a', 'buy', '1', '10')], coverage(),
                    {'token': {'price_usd': D('100'), 'timestamp': END + timedelta(seconds=1)}})

    def test_failed_transaction_not_inventory_but_fee_is_expense(self):
        result, = account([event('a', 'buy', '1', '10'),
                           event('b', 'failed', '10', '500', fee='1', day=2)], coverage())
        self.assertEqual(result.open_quantity, D('1'))
        self.assertEqual(result.realized, D('-1'))

    def test_wrap_does_not_create_profit_or_cost(self):
        result, = account([event('a', 'wrap', '10', '1000', token=SOL)], coverage())
        self.assertEqual(result.identified_realized, D('0'))
        self.assertEqual(result.costs, D('0'))
        self.assertEqual(result.open_quantity, D('0'))
        self.assertFalse(result.reliable)  # Apenas wraps não são amostra de trading.

    def test_multistep_and_liquidity_not_interpreted_as_simple_trades(self):
        for operation in [event('a', 'liquidity', '10', '100'),
                          event('a', 'buy', '10', '100', multi_step=True)]:
            result, = account([operation], coverage())
            self.assertIsNone(result.realized)
            self.assertIsNone(result.open_cost)
            self.assertFalse(result.reliable)

    def test_utc_weeks_non_overlapping_and_holding_duration(self):
        events = [event('a', 'buy', '2', '20', day=1),
                  event('b', 'sell', '1', '20', day=2),
                  event('c', 'sell', '1', '5', day=9)]
        result, = account(events, coverage())
        self.assertEqual(sorted(result.weekly.values()), [D('-5'), D('10')])
        self.assertEqual(sum(result.weekly.values()), result.realized)
        self.assertEqual(result.holding_seconds, [86400.0, 691200.0])

    def test_multiple_wallets_and_invalid_values_rejected(self):
        other = Event('b', 'other', 'token', START + timedelta(days=2), 'buy', D('1'), D('1'), D('0'))
        with self.assertRaisesRegex(ValueError, 'carteira de cada vez'):
            account([event('a', 'buy', '1', '1'), other], coverage())
        for quantity in ('-1', 'NaN', 'Infinity'):
            with self.subTest(quantity=quantity), self.assertRaises(ValueError):
                account([event('a', 'buy', quantity, '1')], coverage())

    def test_transfer_out_reduces_inventory_without_realizing_sale(self):
        result, = account([event('a', 'buy', '10', '100'),
                           event('b', 'transfer_out', '4', '500', day=2)], coverage())
        self.assertEqual(result.open_quantity, D('6'))
        self.assertEqual(result.open_cost, D('60'))
        self.assertEqual(result.identified_realized, D('0'))
        self.assertIsNone(result.realized)

    def test_zero_transfer_does_not_create_invalid_fifo_lot(self):
        result, = account([event('a', 'transfer_in', '0'),
                           event('b', 'buy', '1', '10', day=2),
                           event('c', 'sell', '1', '15', day=3)], coverage())
        self.assertEqual(result.identified_realized, D('5'))
        self.assertIsNone(result.realized)

    def test_tiny_decimal_units_not_rounded_away(self):
        result, = account([event('a', 'buy', '0.000000003', '3'),
                           event('b', 'sell', '0.000000001', '2', day=2)], coverage())
        self.assertEqual(result.open_quantity, D('0.000000002'))
        self.assertEqual(result.open_cost, D('2'))
        self.assertEqual(result.realized, D('1'))

    def test_two_same_second_buys_cannot_prove_fifo_cost_for_partial_sale(self):
        result, = account([event('a', 'buy', '1', '10'),
                           event('b', 'buy', '1', '100'),
                           event('c', 'sell', '1', '30', day=2)], coverage())
        self.assertFalse(result.reliable)
        self.assertIsNone(result.realized)

    def test_same_second_roundtrip_lacks_intrasecond_ordering(self):
        result, = account([event('a', 'buy', '1', '10'),
                           event('b', 'sell', '1', '20')], coverage())
        self.assertFalse(result.reliable)
        self.assertEqual(result.holding_seconds, [0.0])


if __name__ == '__main__':
    unittest.main()
