"""Refinamento paginado limitado; fixtures sem rede, identidades ou credenciais reais."""
from copy import deepcopy
from decimal import Decimal
import unittest
from unittest.mock import patch

from test_profit_prefilter import Client, NOW, WALLET, response, token
from wallets.birdeye import BudgetExceeded
from wallets.models import SOL
from wallets.profit_prefilter import prefilter_wallet
from wallets.profit_refinement import refine_candidates, refinement_reservation


def initial(total=101, *, wallet=WALLET):
    return {'wallet': wallet, 'origins': [], 'prefilter7': {
        'wallet': wallet, 'days': 7, 'passed': True, 'complete': False,
        '_future_snapshot': False, 'pnl_usd': Decimal(total * 10),
        'tokens_count': total, 'profit_factor': None, 'top1_share': None,
        'raw_metrics': {'excluded_settlement_tokens_count': 0}}}


def pages(total):
    rows = [token(i, 10) for i in range(total)]
    return [response(rows[i:i+100], total=total, pnl=total*10)
            for i in range(0, total, 100)]


class ProfitRefinementTests(unittest.TestCase):
    def setUp(self):
        clock = patch('wallets.profit_prefilter.utcnow', return_value=NOW)
        clock.start(); self.addCleanup(clock.stop)
        network = patch('socket.socket.connect', side_effect=AssertionError('Rede proibida'))
        network.start(); self.addCleanup(network.stop)

    def test_plan_is_read_only_and_success_preserves_initial_headline(self):
        candidate = initial(); before = deepcopy(candidate); config = {'profit': {'prefilter_max_pages': 1}}
        self.assertEqual(refinement_reservation([candidate], config, 2), 2)
        self.assertEqual(candidate, before)
        client = Client(*pages(101))
        counts = refine_candidates(client, [candidate], config, NOW, 2)
        self.assertEqual(counts['attempted'], 1); self.assertEqual(counts['complete'], 1)
        self.assertEqual(counts['pages_allocated'], 2)
        self.assertTrue(candidate['prefilter7']['complete'])
        self.assertEqual(candidate['prefilter7']['top1_share'], Decimal(1)/101)
        self.assertEqual(candidate['prefilter7_initial'], before['prefilter7'])
        self.assertEqual(candidate['refinement7']['status'], 'complete')
        self.assertEqual(config, {'profit': {'prefilter_max_pages': 1}})
        self.assertEqual([call[2]['offset'] for call in client.calls], [0, 100])

    def test_large_universe_is_deferred_automatically_but_fits_manual_budget(self):
        candidate = initial(837); client = Client(*pages(837))
        self.assertEqual(refinement_reservation([candidate], {}, 4), 0)
        deferred = refine_candidates(client, [candidate], {}, NOW, 4)
        self.assertEqual(deferred['deferred'], 1); self.assertEqual(client.calls, [])
        self.assertEqual(candidate['refinement7']['pages_required'], 9)
        self.assertFalse(candidate['prefilter7']['complete'])
        self.assertEqual(refinement_reservation([candidate], {}, 10), 9)
        completed = refine_candidates(client, [candidate], {}, NOW, 10)
        self.assertEqual(completed['complete'], 1)
        self.assertEqual(completed['pages_allocated'], 9)
        self.assertEqual(len(client.calls), 9)
        self.assertEqual(len(candidate['prefilter7']['tokens']), 837)

    def test_budget_does_not_skip_a_small_wallet_after_deferring_a_large_one(self):
        big, small = initial(837), initial(101, wallet='C'*44)
        client = Client(*pages(101))
        self.assertEqual(refinement_reservation([big, small], {}, 4), 2)
        result = refine_candidates(client, [big, small], {}, NOW, 4)
        self.assertEqual(result['deferred'], 1); self.assertEqual(result['complete'], 1)
        self.assertEqual(result['pages_allocated'], 2)
        self.assertTrue(all(call[2]['wallet'] == 'C'*44 for call in client.calls))

    def test_quota_failure_keeps_original_headline_and_separate_incomplete_proof(self):
        candidate = initial(); before = deepcopy(candidate['prefilter7'])
        client = Client(pages(101)[0], BudgetExceeded('secret or address must not be published'))
        result = refine_candidates(client, [candidate], {}, NOW, 2)
        self.assertEqual(result['complete'], 0); self.assertEqual(result['attempted'], 1)
        self.assertEqual(candidate['prefilter7'], before)
        self.assertFalse(candidate['refinement7']['result']['complete'])
        self.assertNotIn('secret', str(result))
        self.assertIsNone(candidate['prefilter7']['profit_factor'])

    def test_changed_summary_cannot_erase_or_replace_the_first_triage(self):
        candidate = initial(); before = deepcopy(candidate['prefilter7'])
        first, second = pages(101)
        second['data']['summary']['pnl']['total_usd'] = '1011'
        result = refine_candidates(Client(first, second), [candidate], {}, NOW, 2)
        self.assertEqual(result['reasons'], {'refinement_pagination_changed': 1})
        self.assertEqual(candidate['prefilter7'], before)
        self.assertFalse(candidate['refinement7']['result']['passed'])

    def test_new_complete_loss_replaces_positive_triage_without_erasing_history(self):
        candidate=initial(); before=deepcopy(candidate['prefilter7'])
        responses=pages(101)
        for response_row in responses:
            response_row['data']['summary']['pnl']['total_usd']='-50'
        result=refine_candidates(Client(*responses),[candidate],{},NOW,2)
        self.assertEqual(result['complete'],1)
        self.assertTrue(candidate['prefilter7']['complete'])
        self.assertFalse(candidate['prefilter7']['passed'])
        self.assertEqual(candidate['prefilter7']['pnl_usd'],Decimal(-50))
        self.assertEqual(candidate['prefilter7_initial'],before)
        self.assertIn('provider_pnl_not_positive',candidate['prefilter7']['reasons'])

    def test_cached_page_still_consumes_planning_capacity(self):
        one, two = initial(), initial(wallet='C'*44)
        calls = []
        def cached(client, wallet, days, config, now):
            calls.append(wallet)
            return prefilter_wallet(Client(*pages(101)), wallet, days, config, now)
        with patch('wallets.profit_refinement.prefilter_wallet', side_effect=cached):
            result = refine_candidates(object(), [one, two], {}, NOW, 3)
        self.assertEqual(calls, [WALLET])
        self.assertEqual(result['pages_allocated'], 2)
        self.assertEqual(result['deferred'], 1)
        self.assertIn('refinement_budget_deferred', result['reasons'])

    def test_settlement_rows_are_included_in_estimated_pages(self):
        candidate = initial(200)
        candidate['prefilter7']['raw_metrics']['excluded_settlement_tokens_count'] = 1
        self.assertEqual(refinement_reservation([candidate], {}, 3), 3)
        rows = [token(i, 10) for i in range(200)]
        settlement = token(200, 0); settlement['address'] = SOL
        rows.insert(0, settlement)
        client = Client(*(response(rows[i:i+100], total=200, pnl=2000)
                          for i in range(0, len(rows), 100)))
        result = refine_candidates(client, [candidate], {}, NOW, 3)
        self.assertEqual(result['complete'], 1); self.assertEqual(len(client.calls), 3)

    def test_negative_complete_or_unknown_headlines_do_not_consume_requests(self):
        for changes in ({'passed':False}, {'pnl_usd':0}, {'pnl_usd':None}, {'complete':True}):
            with self.subTest(changes=changes):
                candidate = initial(); candidate['prefilter7'].update(changes); client = Client()
                self.assertEqual(refinement_reservation([candidate], {}, 10), 0)
                result = refine_candidates(client, [candidate], {}, NOW, 10)
                self.assertEqual(result['attempted'], 0); self.assertEqual(client.calls, [])

    def test_result_identity_future_and_method_are_checked_before_replacement(self):
        good = prefilter_wallet(Client(*pages(101)), WALLET, 7, {'prefilter_max_pages':2}, NOW)
        for changes in ({'wallet':'C'*44}, {'days':30}, {'_future_snapshot':True},
                        {'raw_metrics':dict(good['raw_metrics'], method='fifo')},
                        {'raw_metrics':dict(good['raw_metrics'], position_scope='all')},
                        {'complete':False}, {'passed':False}):
            with self.subTest(changes=changes):
                candidate = initial(); before = deepcopy(candidate['prefilter7'])
                bad = dict(good, **changes)
                with patch('wallets.profit_refinement.prefilter_wallet', return_value=bad):
                    result = refine_candidates(object(), [candidate], {}, NOW, 2)
                self.assertEqual(result['complete'], 0)
                self.assertEqual(candidate['prefilter7'], before)

    def test_hard_page_ceiling_wallet_limit_and_bad_counts_defer_without_network(self):
        large = initial(1001); unknown = initial(); unknown['prefilter7']['tokens_count'] = None
        one, two = initial(), initial(wallet='C'*44)
        config = {'refinement_max_wallets':1, 'refinement_max_pages_per_wallet':100}
        client = Client(*pages(101))
        result = refine_candidates(client, [large, unknown, one, two], config, NOW, 20)
        self.assertEqual(result['attempted'], 1); self.assertEqual(result['deferred'], 3)
        self.assertEqual(set(result['reasons']), {'refinement_page_limit','refinement_count_unknown','refinement_wallet_limit'})
        self.assertEqual(len(client.calls), 2)
        for limit in (-1, True, 2.5):
            with self.assertRaises(ValueError):refinement_reservation([initial()], {}, limit)


if __name__ == '__main__':
    unittest.main()
