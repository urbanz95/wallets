"""Contratos e seleção de fontes por lucro; nenhuma rede nem estado operacional."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wallets.birdeye import BudgetExceeded,ENDPOINTS
from wallets.models import iso
from wallets.profit_discovery import discover,GAINERS_ENDPOINT,TOP_ENDPOINT
from wallets.storage import Store

NOW=datetime(2026,10,30,12,tzinfo=timezone.utc)
A,B,C='A'*44,'B'*44,'C'*44
T1,T2,T3='D'*44,'E'*44,'F'*44


def market(token,days=1,**changes):
    return dict(address=token,recent_listing_time=int((NOW-timedelta(days=days)).timestamp()),
                liquidity='100000',volume_24h_usd='100000',price_change_24h_percent='-30',**changes)


def top(wallet,pnl='2000',tags=None,**changes):
    row={'owner':wallet,'realizedPnl':pnl,'totalPnl':pnl,'unrealizedPnl':'0',
         'volumeBuyUSD':'300','volumeSellUSD':'2300','trade':20,'tradeBuy':10,'tradeSell':10,
         'firstTradeUnixTime':int((NOW-timedelta(days=2)).timestamp()),
         'lastTradeUnixTime':int((NOW-timedelta(hours=1)).timestamp())}
    if tags is not None:row['tags']=tags
    row.update(changes);return row


def gainer(wallet,pnl=2000,trades=100):
    return {'network':'solana','address':wallet,'pnl':pnl,'realized_pnl':pnl,
            'unrealized_pnl':0,'volume':10000,'trade_count':trades}


class Client:
    def __init__(self,gainers=None,tokens=None,traders=None):
        self.gainers=gainers if gainers is not None else [gainer(A)]
        self.tokens=tokens if tokens is not None else [market(T1)]
        self.traders=traders if traders is not None else {T1:[top(A)]}
        self.calls=[]
    def request(self,method,path,params=None,body=None,**kwargs):
        self.calls.append((method,path,deepcopy(params),deepcopy(body)))
        if path==GAINERS_ENDPOINT:rows=self.gainers
        elif path=='/defi/v3/token/list':rows=self.tokens
        else:rows=self.traders.get(params['address'],[])
        if isinstance(rows,Exception):raise rows
        return {'success':True,'data':{'items':deepcopy(rows)}}


class ProfitDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'state.sqlite');self.addCleanup(self.store.close)
        blocker=patch('socket.socket.connect',side_effect=AssertionError('Rede proibida'))
        blocker.start();self.addCleanup(blocker.stop)

    def test_standard_gainers_is_primary_and_uses_its_exact_enum(self):
        client=Client();r=discover(client,self.store,{},NOW)
        method,path,params,_=client.calls[0]
        self.assertEqual((method,path),('GET',GAINERS_ENDPOINT))
        self.assertEqual(params,{'type':'1W','sort_by':'PnL','max_trade':700,'offset':0,'limit':100})
        self.assertEqual(ENDPOINTS[('GET',GAINERS_ENDPOINT)],30)
        self.assertEqual(r['candidates'][0]['wallet'],A)
        self.assertEqual(r['candidates'][0]['discovery']['provider_pnl_usd'],Decimal(2000))
        self.assertFalse(r['candidates'][0]['discovery']['labels_verified'])

    def test_top_traders_uses_realized_pnl_and_retains_profit_flow_counts_times(self):
        client=Client();r=discover(client,self.store,{},NOW)
        top_call=[call for call in client.calls if call[1]==TOP_ENDPOINT][0]
        self.assertEqual(top_call[2]['sort_by'],'realized_pnl')
        self.assertEqual(top_call[2]['time_frame'],'7d')
        saved=self.store.discovery_evidence(A)[0]['evidence']
        self.assertEqual(saved['metrics']['volumeBuyUSD'],'300')
        self.assertEqual(saved['metrics']['tradeSell'],'10')
        self.assertEqual(saved['metrics']['realizedPnl'],'2000')
        self.assertIsNotNone(saved['metrics']['lastTradeUnixTime'])

    def test_tags_remove_a_wallet_from_all_sources_and_do_not_reintroduce_it(self):
        client=Client(gainers=[gainer(A)],tokens=[market(T1),market(T2)],
                      traders={T1:[top(A,tags=['SNIPER'])],T2:[top(A,tags=[]),top(B)]})
        r=discover(client,self.store,{},NOW)
        self.assertEqual([row['wallet'] for row in r['candidates']],[B])
        self.assertIn('excluded_tag:sniper',r['rejections'][0]['reasons'])

    def test_every_explicit_unsuitable_tag_is_excluded(self):
        for tag in ('bundler','insider','sniper'):
            with self.subTest(tag=tag):
                r=discover(Client(gainers=[],traders={T1:[top(A,tags=[tag])]}),self.store,{},NOW)
                self.assertEqual(r['candidates'],[])
                self.assertEqual(r['rejections'][0]['reasons'],['excluded_tag:'+tag])

    def test_two_token_presence_precedes_a_single_isolated_winner(self):
        client=Client(gainers=[gainer(A,1000000)],tokens=[market(T1),market(T2)],
                      traders={T1:[top(B,'10')],T2:[top(B,'20')]})
        r=discover(client,self.store,{},NOW)
        self.assertEqual(r['candidates'][0]['wallet'],B)
        self.assertEqual(r['candidates'][0]['origins'],[T1,T2])
        self.assertEqual(r['candidates'][0]['discovery']['distinct_token_origins'],2)

    def test_default_appreciation_filter_is_absent_and_optional(self):
        client=Client();r=discover(client,self.store,{},NOW)
        params=client.calls[1][2]
        self.assertNotIn('min_price_change_24h_percent',params)
        self.assertEqual(len(r['selected_tokens']),1)
        second=Client();r=discover(second,self.store,{'discovery_min_price_change_24h_percent':10},NOW)
        self.assertEqual(second.calls[1][2]['min_price_change_24h_percent'],10)
        self.assertEqual(r['selected_tokens'],[])

    def test_old_or_future_tokens_are_not_discovery_origins(self):
        client=Client(gainers=[],tokens=[market(T1,days=8),market(T2,days=-1),market(T3,days=0)])
        r=discover(client,self.store,{},NOW)
        self.assertEqual([row['token'] for row in r['selected_tokens']],[T3])
        self.assertEqual([call[2]['address'] for call in client.calls if call[1]==TOP_ENDPOINT],[T3])

    def test_overactive_wallet_is_excluded_before_prefilter_or_deep(self):
        client=Client(gainers=[gainer(A,trades=701)],traders={T1:[top(A)]})
        r=discover(client,self.store,{},NOW)
        self.assertEqual(r['candidates'],[])
        self.assertIn('activity_over_daily_limit',r['rejections'][0]['reasons'])

    def test_zero_trade_gains_are_not_current_trading_activity(self):
        r=discover(Client(gainers=[gainer(A,trades=0)],tokens=[]),self.store,{},NOW)
        self.assertEqual(r['candidates'],[])
        self.assertIn('discovery_activity_absent',r['rejections'][0]['reasons'])

    def test_quota_interruption_keeps_already_found_wallets(self):
        client=Client(tokens=[market(T1),market(T2)],traders={T1:[top(B)],T2:BudgetExceeded('limite')})
        r=discover(client,self.store,{},NOW)
        self.assertEqual({row['wallet'] for row in r['candidates']},{A,B})
        self.assertEqual(r['errors'],[{'stage':'discovery_token_traders','reason':'BudgetExceeded'}])

    def test_global_network_and_metrics_are_validated_locally(self):
        bad=gainer(B);bad['network']='ethereum'
        missing=gainer(C);missing['trade_count']=None
        r=discover(Client(gainers=[bad,missing],tokens=[]),self.store,{},NOW)
        self.assertEqual(r['candidates'],[])
        self.assertEqual({reason for row in r['rejections'] for reason in row['reasons']},{'unsupported_network','discovery_metrics_unknown'})

    def test_five_token_discovery_cost_fits_230_cu_without_pagination(self):
        tokens=['D'*42+str(i)+'A' for i in range(1,6)]
        client=Client(tokens=[market(token) for token in tokens],traders={token:[top(A)] for token in tokens})
        r=discover(client,self.store,{'discovery_max_tokens':5},NOW)
        self.assertEqual(len(r['selected_tokens']),5)
        self.assertEqual(len(client.calls),7)
        self.assertEqual(sum(ENDPOINTS[(method,path)] for method,path,_,_ in client.calls),230)


if __name__=='__main__':unittest.main()
