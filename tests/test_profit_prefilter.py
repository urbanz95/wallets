"""PnL barato: cobertura explícita, paginação e resultados do fornecedor."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import unittest
from unittest.mock import patch

from wallets.birdeye import BudgetExceeded, AccessDenied
from wallets.profit_prefilter import prefilter_wallet
from wallets.models import iso

NOW=datetime(2026,10,30,12,tzinfo=timezone.utc)
WALLET='A'*44
ALPHABET='123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'


def token(index,pnl):
    return {'address':'B'*42+ALPHABET[index//58]+ALPHABET[index%58],
            'pnl':{'total_usd':str(pnl),'realized_profit_usd':str(pnl),'unrealized_usd':'0'},
            'counts':{'total_trade':2},'quantity':{'holding':'0'},
            'cashflow_usd':{'total_invested':'100','cost_of_quantity_sold':'100','current_value':'0'}}


def response(rows,*,total=None,pnl=None,trades=None):
    total=len(rows) if total is None else total
    pnl=sum(Decimal(row['pnl']['total_usd']) for row in rows) if pnl is None else pnl
    return {'data':{'meta':{'time':iso(NOW),'currency':'usd'},'tokens':rows,
      'summary':{'unique_tokens':total,'counts':{'total_trade':total*2 if trades is None else trades,'win_rate':'0.999'},
                 'pnl':{'total_usd':str(pnl),'realized_profit_usd':str(pnl),'unrealized_usd':'0'},
                 'cashflow_usd':{'total_invested':'100'}}}}


class Client:
    def __init__(self,*responses):self.responses=list(responses);self.calls=[]
    def request(self,method,path,params=None,body=None,**kwargs):
        self.calls.append((method,path,deepcopy(body)))
        r=self.responses.pop(0)
        if isinstance(r,Exception):raise r
        return deepcopy(r)


class ProfitPrefilterTests(unittest.TestCase):
    def setUp(self):
        clock=patch('wallets.profit_prefilter.utcnow',return_value=NOW)
        clock.start();self.addCleanup(clock.stop)
        blocker=patch('socket.socket.connect',side_effect=AssertionError('Rede proibida'))
        blocker.start();self.addCleanup(blocker.stop)

    def test_7d_and_30d_requests_follow_the_current_wac_contract(self):
        for days in (7,30):
            with self.subTest(days=days):
                client=Client(response([token(0,10)]));r=prefilter_wallet(client,WALLET,days,{},NOW)
                body=client.calls[0][2]
                self.assertEqual(body,{'wallet':WALLET,'duration':str(days)+'d','pnl_method':'wac',
                    'position_scope':'duration_only','sort_by':'last_trade','sort_type':'desc','offset':0,'limit':100})
                self.assertNotIn('token_addresses',body)
                self.assertTrue(r['passed']);self.assertTrue(r['complete'])

    def test_full_token_metrics_use_positive_sum_not_net_profit(self):
        r=prefilter_wallet(Client(response([token(0,12000),token(1,-2000)])),WALLET,7,{},NOW)
        self.assertEqual(r['pnl_usd'],Decimal('10000'))
        self.assertEqual(r['profit_factor'],Decimal('6'))
        self.assertEqual(r['top1_share'],Decimal('1'))
        self.assertEqual(r['win_rate'],Decimal('0.5'))
        self.assertTrue(r['passed']) # pré-filtro, não aprovação contabilística

    def test_secondary_provider_win_rate_does_not_overwrite_recomputed_token_rate(self):
        r=prefilter_wallet(Client(response([token(0,100),token(1,-10),token(2,0)])),WALLET,7,{},NOW)
        self.assertEqual(r['win_rate'],Decimal(1)/3)
        self.assertEqual(r['raw_metrics']['counts']['win_rate'],Decimal('0.999'))

    def test_a_small_page_is_not_the_full_universe_when_summary_says_more(self):
        r=prefilter_wallet(Client(response([token(0,10)],total=101,pnl=1000)),WALLET,7,{},NOW)
        self.assertFalse(r['complete']);self.assertTrue(r['passed'])
        for key in ('win_rate','profit_factor','top1_share','top3_share'):self.assertIsNone(r[key],key)
        self.assertEqual(r['tokens_count'],101)
        self.assertIn('Paginação parcial',' '.join(r['limitations']))

    def test_pagination_computes_metrics_only_after_all_rows(self):
        rows=[token(i,10) for i in range(101)]
        client=Client(response(rows[:100],total=101,pnl=1010),response(rows[100:],total=101,pnl=1010))
        r=prefilter_wallet(client,WALLET,7,{'prefilter_max_pages':2},NOW)
        self.assertTrue(r['complete']);self.assertEqual(len(r['tokens']),101)
        self.assertEqual(client.calls[1][2]['offset'],100)
        self.assertEqual(r['top1_share'],Decimal(1)/101)

    def test_mutating_pagination_does_not_appear_complete(self):
        first=response([token(0,10)],total=2,pnl=20)
        second=response([token(1,10)],total=2,pnl=21)
        r=prefilter_wallet(Client(first,second),WALLET,7,{'prefilter_max_pages':2},NOW)
        self.assertFalse(r['complete']);self.assertFalse(r['passed'])
        self.assertIn('pnl_pagination_changed',r['reasons'])

    def test_unavailable_token_values_stay_unknown_not_zero(self):
        row=token(0,10);row['pnl'].pop('total_usd');row['cashflow_usd']={}
        r=prefilter_wallet(Client(response([row],pnl=10)),WALLET,7,{},NOW)
        self.assertFalse(r['complete']);self.assertIsNone(r['profit_factor'])
        self.assertIsNone(r['tokens'][0]['pnl_usd']);self.assertIsNone(r['tokens'][0]['cost_of_quantity_sold_usd'])
        self.assertTrue(r['passed'])

    def test_zero_or_negative_headline_does_not_pass(self):
        for pnl in (0,-1):
            with self.subTest(pnl=pnl):
                r=prefilter_wallet(Client(response([token(0,pnl)])),WALLET,7,{},NOW)
                self.assertFalse(r['passed']);self.assertIn('provider_pnl_not_positive',r['reasons'])

    def test_unknown_headline_does_not_pass(self):
        value=response([token(0,10)]);value['data']['summary']['pnl']['total_usd']='NaN'
        r=prefilter_wallet(Client(value),WALLET,7,{},NOW)
        self.assertIsNone(r['pnl_usd']);self.assertFalse(r['passed'])

    def test_too_many_trades_and_absent_activity_are_rejected_before_deep(self):
        for days,trades in ((7,701),(30,3001),(7,0)):
            with self.subTest(days=days,trades=trades):
                r=prefilter_wallet(Client(response([token(0,10)],trades=trades)),WALLET,days,{},NOW)
                self.assertFalse(r['passed'])
        accepted=prefilter_wallet(Client(response([token(0,10)],trades=700)),WALLET,7,{},NOW)
        self.assertTrue(accepted['passed']);self.assertEqual(accepted['trades_per_day'],100)

    def test_duplicate_rows_cannot_inflate_the_sample(self):
        r=prefilter_wallet(Client(response([token(0,10),token(0,10)])),WALLET,7,{},NOW)
        self.assertFalse(r['passed']);self.assertFalse(r['complete'])
        self.assertIn('pnl_duplicate_token',r['reasons'])

    def test_quota_and_malformed_responses_are_not_empty_approvals(self):
        for value in (BudgetExceeded('limite'),{'data':{}},{'data':{'tokens':[],'summary':{}}}):
            with self.subTest(value=type(value).__name__):
                r=prefilter_wallet(Client(value),WALLET,7,{},NOW)
                self.assertFalse(r['passed']);self.assertFalse(r['complete']);self.assertTrue(r['reasons'])

    def test_no_losses_is_explicit_and_not_infinite_profit_factor(self):
        r=prefilter_wallet(Client(response([token(i,800) for i in range(5)])),WALLET,7,{},NOW)
        self.assertIsNone(r['profit_factor']);self.assertTrue(r['raw_metrics']['no_provider_token_losses'])
        self.assertEqual(r['top1_share'],Decimal('0.2'));self.assertEqual(r['top3_share'],Decimal('0.6'))

    def test_wallet_and_period_are_checked_before_network(self):
        client=Client()
        for wallet,days in (('not-wallet',7),(WALLET,0),(WALLET,90)):
            with self.assertRaises(ValueError):prefilter_wallet(client,wallet,days,{},NOW)
        self.assertEqual(client.calls,[])

    def test_settlement_rows_are_not_speculative_tokens_or_pagination_errors(self):
        from wallets.models import SOL
        from wallets.analysis import SETTLEMENT_TOKENS
        rows=[token(0,10),token(1,-1)]
        from wallets.collection import valid_address
        settlement_addresses=[address for address in SETTLEMENT_TOKENS if valid_address(address)]
        for address in settlement_addresses:
            row=token(2,1000);row['address']=address;rows.append(row)
        r=prefilter_wallet(Client(response(rows,total=2,pnl=9)),WALLET,7,{},NOW)
        self.assertTrue(r['passed']);self.assertTrue(r['complete'])
        self.assertEqual(len(r['tokens']),2)
        self.assertEqual(r['profit_factor'],10)
        self.assertEqual(r['raw_metrics']['excluded_settlement_tokens_count'],len(settlement_addresses))

    def test_inconsistent_token_count_does_not_fabricate_ratios_or_erase_headline(self):
        r=prefilter_wallet(Client(response([token(0,10),token(1,-1)],total=1,pnl=9)),WALLET,7,{},NOW)
        self.assertTrue(r['passed']);self.assertFalse(r['complete'])
        self.assertEqual(r['pnl_usd'],9)
        self.assertIsNone(r['profit_factor']);self.assertIsNone(r['top1_share'])
        self.assertIn('NÃO VERIFICADAS',' '.join(r['limitations']))

    def test_response_wallet_identity_must_match_when_provided(self):
        value=response([token(0,10)]);value['data']['meta']['address']='B'*44
        r=prefilter_wallet(Client(value),WALLET,7,{},NOW)
        self.assertFalse(r['passed']);self.assertIn('pnl_wallet_mismatch',r['reasons'])

    def test_restriction_status_is_preserved_without_response_body(self):
        client=Client(AccessDenied('Cliente interrompido.'))
        client.last_http_status=429
        result=prefilter_wallet(client,WALLET,7,{},NOW)
        self.assertEqual(result['raw_metrics']['failure_http_status'],429)
        self.assertFalse(result['passed'])


    def test_receipt_times_use_the_injected_actual_clock_per_response(self):
        first=response([token(0,10)],total=2,pnl=20)
        second=response([token(1,10)],total=2,pnl=20)
        received=iter((NOW+timedelta(minutes=1),NOW+timedelta(minutes=2)))
        r=prefilter_wallet(Client(first,second),WALLET,7,{'prefilter_max_pages':2},
                           NOW-timedelta(hours=1),clock=lambda:next(received))
        self.assertEqual(r['raw_metrics']['observed_at'],iso(NOW-timedelta(hours=1)))
        self.assertEqual(r['raw_metrics']['fetched_at'],iso(NOW+timedelta(minutes=2)))
        self.assertEqual([x['fetched_at'] for x in r['raw_metrics']['responses']],
                         [iso(NOW+timedelta(minutes=1)),iso(NOW+timedelta(minutes=2))])
        self.assertEqual([x['snapshot_status'] for x in r['raw_metrics']['responses']],['current','current'])
        self.assertTrue(r['passed'])

    def test_future_snapshot_blocks_prefilter_even_with_positive_pnl(self):
        value=response([token(0,10)])
        value['data']['meta']['time']=iso(NOW+timedelta(seconds=1))
        r=prefilter_wallet(Client(value),WALLET,7,{},NOW)
        self.assertFalse(r['passed']);self.assertTrue(r['_future_snapshot'])
        self.assertIn('provider_future_snapshot',r['reasons'])
        self.assertEqual(r['pnl_usd'],10)
        self.assertEqual(r['raw_metrics']['responses'][0]['snapshot_status'],'future')

    def test_a_later_page_cannot_erase_an_earlier_future_snapshot(self):
        first=response([token(0,10)],total=2,pnl=20)
        first['data']['meta']['time']=iso(NOW+timedelta(seconds=1))
        second=response([token(1,10)],total=2,pnl=20)
        r=prefilter_wallet(Client(first,second),WALLET,7,{'prefilter_max_pages':2},NOW)
        self.assertFalse(r['passed']);self.assertTrue(r['_future_snapshot'])
        self.assertEqual(r['raw_metrics']['snapshot_at'],iso(NOW))
        self.assertEqual([x['snapshot_status'] for x in r['raw_metrics']['responses']],['future','current'])

    def test_missing_stale_and_invalid_metadata_preserve_only_unverified_triage(self):
        for metadata in (None,[],False,'invalid',{}, {'time':None},{'time':True},
                         {'time':'bad'},{'time':'2026-10-30T12:00:00'},
                         {'time':iso(NOW-timedelta(hours=24,seconds=1))}):
            value=response([token(0,10)]);value['data']['meta']=metadata
            with self.subTest(metadata=metadata):
                r=prefilter_wallet(Client(value),WALLET,7,{},NOW)
                self.assertTrue(r['passed']) # apenas triagem, nunca prova temporal
                self.assertFalse(r['_future_snapshot'])
                self.assertIn('NÃO VERIFICADA',' '.join(r['limitations']))
                self.assertIn(r['raw_metrics']['responses'][0]['snapshot_status'],('unknown','stale'))

    def test_exactly_24_hours_old_snapshot_is_not_stale_at_receipt(self):
        value=response([token(0,10)])
        value['data']['meta']['time']=iso(NOW-timedelta(hours=24))
        r=prefilter_wallet(Client(value),WALLET,7,{},NOW)
        self.assertEqual(r['raw_metrics']['responses'][0]['snapshot_status'],'current')
        self.assertTrue(r['passed'])


if __name__=='__main__':unittest.main()
