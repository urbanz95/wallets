"""Ranking por evidência: score não compensa falhas e não envia Telegram."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import unittest
from unittest.mock import patch

from wallets.profit_ranking import rank_candidates

NOW=datetime(2026,10,30,12,tzinfo=timezone.utc)
CONFIG={'profit':{'min_tokens':40,'min_profit_factor':1.3,'min_win_rate':.4,
                 'max_top1_share':.35,'max_top3_share':.65,'max_unknown_cost_fraction':.2,
                 'min_followable_hold_seconds':300,'max_rapid_close_fraction':.2,
                 'max_trades_per_day':100,'max_data_age_hours':24}}


def candidate(wallet='A'*44):
    return {'wallet':wallet,'origins':['token-one','token-two'],
            'prefilter7':{'pnl_usd':Decimal('1000'),'complete':True,'passed':True,
                          'raw_metrics':{'snapshot_at':NOW,'fetched_at':NOW}},
            'prefilter30':{'pnl_usd':Decimal('2000'),'complete':True,'passed':True,
                           'raw_metrics':{'snapshot_at':NOW,'fetched_at':NOW}},
            'deep':{'complete':True,'period_start':NOW-timedelta(days=7),
                    'period_end':NOW,'fetched_at':NOW,'coverage':{'invalid_count':0},
                    'metrics':{'currency':'SOL','known_tokens_count':40,'tokens_count':42,'fees_complete':True,
                        'unknown_tokens_count':2,'unknown_cost_fraction':Decimal('.05'),
                        'worst_case_sol':Decimal('20'),'realized_sol':Decimal('25'),
                        'open_cost_sol':Decimal('5'),'profit_factor':Decimal('2'),
                        'win_rate':Decimal('.4'),'median_return':Decimal('-.2'),
                        'positive_results_sum':Decimal('40'),'no_observed_losses':False,
                        'without_best_sol':Decimal('15'),'without_top3_sol':Decimal('8'),
                        'top1_share':Decimal('.125'),'top3_share':Decimal('.3'),
                        'activity_days':5,'last_activity_at':NOW-timedelta(hours=1),'median_holding_seconds':900,
                        'trades_per_day':20,'rapid_close_fraction':Decimal('.05')}}}


class ProfitRankingTests(unittest.TestCase):
    def setUp(self):
        for target in ('socket.socket.connect','urllib.request.urlopen'):
            block=patch(target,side_effect=AssertionError('Nenhuma rede no ranking'))
            block.start();self.addCleanup(block.stop)

    def test_a_is_only_verifiable_scope_and_keeps_open_cost_and_negative_median(self):
        item=candidate();original=deepcopy(item)
        result=rank_candidates([item],CONFIG,NOW)[0]
        self.assertEqual(result['evidence_level'],'A')
        self.assertTrue(result['assessment_passed'])
        self.assertFalse(result['telegram_eligible'])
        self.assertEqual(result['median_return_informational'],Decimal('-.2'))
        self.assertEqual(result['deep']['metrics']['open_cost_sol'],Decimal('5'))
        self.assertEqual(item,original)
        self.assertAlmostEqual(result['score'],sum(c['points'] for c in result['score_components']))
        self.assertGreater(result['score'],90)
        self.assertLessEqual(result['score'],100)
        self.assertIn('independent_persistence',{row['name'] for row in result['unverified']})
        self.assertIn('usd_conversion',{row['name'] for row in result['unverified']})

    def test_partial_history_and_missing_period_are_b_not_a(self):
        for change in ('partial','missing-period','stale','future','wrong-window'):
            item=candidate()
            if change=='partial':item['deep']['complete']=False
            elif change=='missing-period':del item['deep']['period_start']
            elif change=='stale':
                item['deep']['period_start']-=timedelta(days=2)
                item['deep']['period_end']-=timedelta(days=2)
                item['deep']['fetched_at']-=timedelta(days=2)
            elif change=='future':item['deep']['fetched_at']+=timedelta(seconds=1)
            elif change=='wrong-window':item['deep']['period_start']-=timedelta(days=1)
            with self.subTest(change=change):
                result=rank_candidates([item],CONFIG,NOW)[0]
                self.assertEqual(result['tier'],'B')
                self.assertFalse(result['assessment_passed'])
                check=next(c for c in result['ranking_checks'] if c['name']=='deep_coverage')
                self.assertEqual(check['status'],'INDETERMINADA')

    def test_a_failed_concentration_cannot_be_compensated_by_a_high_score(self):
        rejected=candidate('B'*44)
        rejected['deep']['metrics'].update(top1_share=Decimal('.9'),top3_share=Decimal('.95'))
        provider_only=candidate('C'*44);del provider_only['deep']
        result=rank_candidates([rejected,provider_only],CONFIG,NOW)
        self.assertEqual(result[0]['wallet'],'C'*44)
        self.assertEqual(result[0]['tier'],'C')
        self.assertGreater(result[1]['score'],result[0]['score'])
        self.assertTrue(result[1]['hard_rejected'])
        self.assertIn('concentration',result[1]['reason_codes'])

    def test_financial_and_execution_failures_each_block_a(self):
        failures={'worst_case_sol':Decimal('-1'),'profit_factor':Decimal('1.2'),
                  'without_best_sol':Decimal('0'),'without_top3_sol':Decimal('-2'),
                  'trades_per_day':Decimal('101'),'rapid_close_fraction':Decimal('.21'),
                  'median_holding_seconds':Decimal('299')}
        for field,value in failures.items():
            item=candidate();item['deep']['metrics'][field]=value
            with self.subTest(field=field):
                row=rank_candidates([item],CONFIG,NOW)[0]
                self.assertNotEqual(row['tier'],'A')
                self.assertTrue(row['hard_rejected'])
                self.assertFalse(row['assessment_passed'])

    def test_provider_pnl_never_replaces_sol_metrics(self):
        item=candidate();item['prefilter7']['pnl_usd']=Decimal('999999999')
        item['deep']['metrics']['currency']='USD'
        result=rank_candidates([item],CONFIG,NOW)[0]
        self.assertEqual(result['tier'],'B')
        self.assertFalse(result['assessment_passed'])
        self.assertEqual(next(c for c in result['ranking_checks'] if c['name']=='sol_worst_case')['status'],'INDETERMINADA')

    def test_seven_and_thirty_day_pnl_are_separate_required_proxies_for_a(self):
        for duration in ('prefilter7','prefilter30'):
            for value in (Decimal('0'),Decimal('-1'),None):
                item=candidate();item[duration]['pnl_usd']=value
                with self.subTest(duration=duration,value=value):
                    result=rank_candidates([item],CONFIG,NOW)[0]
                    self.assertFalse(result['assessment_passed'])
                    self.assertNotEqual(result['tier'],'A')
        item=candidate();item['prefilter30']['complete']=False
        result=rank_candidates([item],CONFIG,NOW)[0]
        self.assertEqual(result['tier'],'B')
        self.assertEqual(next(c for c in result['ranking_checks'] if c['name']=='provider_30d')['status'],'INDETERMINADA')

    def test_global_headline_can_be_verified_while_provider_token_pages_are_partial(self):
        item=candidate()
        for name in ('prefilter7','prefilter30'):
            item[name].update(complete=False,trade_count=200,provisional_passed=True)
            item[name]['passed']=False
        row=rank_candidates([item],CONFIG,NOW)[0]
        self.assertEqual(row['tier'],'A')
        self.assertIn('provider_partial_tokens',{r['name'] for r in row['unverified']})
        # Um headline sem contagens, ou uma carteira sem operações, não passa.
        item['prefilter7'].pop('trade_count')
        self.assertNotEqual(rank_candidates([item],CONFIG,NOW)[0]['tier'],'A')
        item['prefilter7']['trade_count']=0
        self.assertNotEqual(rank_candidates([item],CONFIG,NOW)[0]['tier'],'A')

    def test_only_unknown_cost_fraction_blocks_not_the_number_of_open_tokens(self):
        good=candidate();good['deep']['metrics']['closed_tokens_count']=1
        good['deep']['metrics']['open_tokens_count']=39
        self.assertEqual(rank_candidates([good],CONFIG,NOW)[0]['tier'],'A')
        good['deep']['metrics']['unknown_cost_fraction']=Decimal('.21')
        result=rank_candidates([good],CONFIG,NOW)[0]
        self.assertNotEqual(result['tier'],'A')
        self.assertIn('known_cost_scope',result['reason_codes'])

    def test_minimum_sample_counts_known_tokens_not_unknown_total(self):
        item=candidate();item['deep']['metrics'].update(known_tokens_count=39,tokens_count=100)
        result=rank_candidates([item],CONFIG,NOW)[0]
        self.assertEqual(result['tier'],'B')
        self.assertIn('sol_sample',result['reason_codes'])
        del item['deep']['metrics']['known_tokens_count']
        item['deep']['metrics'].update(tokens_count=42,unknown_tokens_count=2)
        self.assertEqual(rank_candidates([item],CONFIG,NOW)[0]['tier'],'A')

    def test_no_losses_is_explicit_never_a_fabricated_infinite_profit_factor(self):
        item=candidate();item['deep']['metrics'].update(profit_factor=None,no_observed_losses=True)
        result=rank_candidates([item],CONFIG,NOW)[0]
        self.assertEqual(result['tier'],'A')
        self.assertIsNone(result['deep']['metrics']['profit_factor'])
        item['deep']['metrics']['no_observed_losses']=False
        self.assertNotEqual(rank_candidates([item],CONFIG,NOW)[0]['tier'],'A')

    def test_win_rate_is_secondary_and_does_not_hide_a_negative_median(self):
        low=candidate();low['deep']['metrics']['win_rate']=Decimal('.3')
        row=rank_candidates([low],CONFIG,NOW)[0]
        self.assertEqual(row['tier'],'A')
        self.assertTrue(row['secondary_notes'])
        self.assertLess(row['score'],rank_candidates([candidate()],CONFIG,NOW)[0]['score'])
        self.assertEqual(row['median_return_informational'],Decimal('-.2'))

    def test_nan_bool_and_missing_values_stay_unverified(self):
        for value in (Decimal('NaN'),Decimal('Infinity'),True,None,'not-a-number'):
            item=candidate();item['deep']['metrics']['worst_case_sol']=value
            with self.subTest(value=value):
                row=rank_candidates([item],CONFIG,NOW)[0]
                self.assertNotEqual(row['tier'],'A')
                self.assertEqual(next(c for c in row['ranking_checks'] if c['name']=='sol_worst_case')['status'],'INDETERMINADA')

    def test_top_three_even_when_everything_is_partial_and_stable_deduplication(self):
        items=[]
        for wallet in ('A'*44,'B'*44,'C'*44,'D'*44):
            item=candidate(wallet);item.pop('deep');items.append(item)
        output=rank_candidates(items+[deepcopy(items[0])],CONFIG,NOW)
        self.assertEqual([v['wallet'] for v in output],['A'*44,'B'*44,'C'*44])
        self.assertEqual([v['rank'] for v in output],[1,2,3])
        self.assertTrue(all(v['tier']=='C' and not v['telegram_eligible'] for v in output))
        self.assertEqual(rank_candidates([],CONFIG,NOW),[])

    def test_excluded_candidates_are_labelled_and_never_promoted_by_profit(self):
        item=candidate();item['excluded']=True;item['reasons']=['Etiqueta sniper fora do universo.']
        row=rank_candidates([item],CONFIG,NOW)[0]
        self.assertNotEqual(row['tier'],'A')
        self.assertTrue(row['hard_rejected'])
        self.assertIn('Etiqueta sniper fora do universo.',row['reasons'])

    def test_invalid_config_fails_before_any_ranking(self):
        for changes in ({'min_tokens':0},{'min_tokens':True},{'max_unknown_cost_fraction':1.1},{'max_data_age_hours':'nan'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                rank_candidates([candidate()],{'profit':changes},NOW)

    def test_activity_must_be_in_last_24_hours_not_just_fresh_fetch(self):
        for value in (None,NOW-timedelta(hours=25),NOW+timedelta(seconds=1)):
            item=candidate();item['deep']['metrics']['last_activity_at']=value
            result=rank_candidates([item],CONFIG,NOW)[0]
            self.assertNotEqual(result['tier'],'A')
            self.assertIn('recent_activity',result['reason_codes'])

    def test_provider_snapshot_and_actual_receipt_dates_are_mandatory_for_a(self):
        changes=(
            ({},'INDETERMINADA'),
            ({'snapshot_at':NOW},'INDETERMINADA'),
            ({'snapshot_at':'invalid','fetched_at':NOW},'INDETERMINADA'),
            ({'snapshot_at':True,'fetched_at':NOW},'INDETERMINADA'),
            ({'snapshot_at':NOW+timedelta(seconds=1),'fetched_at':NOW},'REPROVADA'),
            ({'snapshot_at':NOW,'fetched_at':NOW+timedelta(seconds=1)},'REPROVADA'),
            ({'snapshot_at':NOW-timedelta(hours=24,seconds=1),'fetched_at':NOW},'REPROVADA'),
            ({'snapshot_at':NOW-timedelta(hours=25),'fetched_at':NOW-timedelta(hours=25)},'REPROVADA'),
        )
        for duration in ('prefilter7','prefilter30'):
            for raw,status in changes:
                item=candidate();item[duration]['raw_metrics']=raw
                with self.subTest(duration=duration,raw=raw):
                    row=rank_candidates([item],CONFIG,NOW)[0]
                    self.assertNotEqual(row['tier'],'A')
                    self.assertFalse(row['assessment_passed'])
                    check=next(x for x in row['ranking_checks'] if x['name']=='provider_dates')
                    self.assertEqual(check['status'],status)
                    self.assertIn('provider_dates',{x['name'] for x in row['unverified']})

    def test_every_provider_page_is_checked_not_only_the_last_snapshot(self):
        item=candidate()
        raw=item['prefilter7']['raw_metrics']
        raw['responses']=[{'snapshot_at':NOW+timedelta(seconds=1),'fetched_at':NOW},
                          {'snapshot_at':NOW,'fetched_at':NOW}]
        row=rank_candidates([item],CONFIG,NOW)[0]
        self.assertNotEqual(row['tier'],'A')
        self.assertIn('provider_dates',row['reason_codes'])
        # O resumo também tem de ser coerente; páginas válidas não o substituem.
        raw['responses']=[{'snapshot_at':NOW,'fetched_at':NOW}]
        raw['snapshot_at']=NOW+timedelta(seconds=1)
        self.assertNotEqual(rank_candidates([item],CONFIG,NOW)[0]['tier'],'A')

    def test_exact_24_hour_provider_boundary_and_explicit_future_flag(self):
        item=candidate()
        for duration in ('prefilter7','prefilter30'):
            item[duration]['raw_metrics']={'snapshot_at':NOW-timedelta(hours=24),'fetched_at':NOW}
        self.assertEqual(rank_candidates([item],CONFIG,NOW)[0]['tier'],'A')
        item['prefilter7']['_future_snapshot']=True
        self.assertNotEqual(rank_candidates([item],CONFIG,NOW)[0]['tier'],'A')
