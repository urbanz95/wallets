from decimal import Decimal as D
import unittest
from wallets.analysis import metrics
from wallets.models import TokenResult, SOL


def token(name, result):
    return TokenResult(name, realized=D(result), identified_realized=D(result), costs=D('100'),
                       realized_cost=D('100'), open_cost=D('0'), unrealized=D('0'), reliable=True)


class AnalysisTests(unittest.TestCase):
    def test_required_isolated_winner_examples(self):
        a = metrics([token('best', '12000'), token('rest', '-2000')])
        b = metrics([token(str(i), '800') for i in range(5)])
        self.assertEqual(a['total_result'], D('10000'))
        self.assertEqual(a['without_best'], D('-2000'))
        self.assertEqual(a['top1_share'], D('1'))
        self.assertEqual(b['total_result'], D('4000'))
        self.assertEqual(b['without_best'], D('3200'))
        self.assertEqual(b['top1_share'], D('.2'))

    def test_grouping_by_mint_and_excluding_origins(self):
        a = metrics([token('same', '20'), token('same', '30'), token('other', '-10')], ['same'])
        self.assertEqual(a['tokens_count'], 2)
        self.assertEqual(a['per_token']['same'], D('50'))
        self.assertEqual(a['without_discovery'], D('-10'))

    def test_unknown_is_not_zero(self):
        a = metrics([token('known', '20'), TokenResult('unknown')])
        self.assertIsNone(a['total_result'])
        self.assertIsNone(a['without_best'])
        self.assertIsNone(a['top1_share'])
        self.assertEqual(a['unknown_cost_fraction'], D('.5'))

    def test_open_loser_included_in_result_and_concentration(self):
        r = token('loser', '50')
        r.open_quantity = D('10')
        r.open_cost = D('200')
        r.unrealized = D('-150')
        a = metrics([r, token('winner', '200')])
        self.assertEqual(a['total_result'], D('100'))
        self.assertEqual(a['open_losses'], D('-150'))
        self.assertEqual(a['without_best'], D('-100'))
        self.assertEqual(a['top1_share'], D('1'))

    def test_no_liquidation_assumption(self):
        r = token('open', '5'); r.open_quantity = D('1'); r.unrealized = None
        a = metrics([r]); self.assertIsNone(a['total_result']); self.assertIsNone(a['open_losses'])

    def test_settlement_assets_do_not_inflate_sample(self):
        a = metrics([token(SOL, '500'), token('meme', '100')])
        self.assertEqual(a['tokens_count'], 1)
        self.assertEqual(a['settlement_result'], D('500'))

    def test_returns_use_fifo_sold_cost(self):
        r = token('x', '100'); r.costs = D('1000'); r.realized_cost = D('100')
        self.assertEqual(metrics([r])['median_return'], D('1'))

    def test_empty_and_zero_positive_results(self):
        self.assertIsNone(metrics([])['total_result'])
        self.assertIsNone(metrics([token('loss', '-1')])['top1_share'])
