"""Teste do fluxo integral sem rede nem credenciais: três estágios e privacidade."""
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch

from test_sol_accounting import END, WALLET, pair
from wallets.storage import Store
from wallets.profit_pipeline import run_pipeline, _provider_order, _family_client, _deep_allowed
from wallets.profit_persistence import latest_run


class Budget:
    def __init__(self):self.stats={};self.names=[]
    @contextmanager
    def stage(self,name,**kwargs):
        self.names.append(name)
        self.stats[name]={'birdeye':{'reserved':kwargs.get('birdeye',0),'attempted':0,'refunded':0},
                          'helius':{'reserved':kwargs.get('helius',0),'attempted':0,'refunded':0}}
        yield


class ProfitPipelineTests(unittest.TestCase):
    def test_prefilter_then_persistence_then_only_best_deep_and_private_top3(self):
        candidates=[{'wallet':wallet,'origins':[],'discovery':{}} for wallet in (WALLET,'B'*44,'C'*44,'D'*44)]
        calls=[]
        def prefilter(client,wallet,days,config,now):
            calls.append((wallet,days))
            return {'wallet':wallet,'days':days,'passed':True,'pnl_usd':Decimal(100),
                    'complete':True,'response_received':True,'tokens_count':40,'profit_factor':Decimal(2),
                    'top3_share':Decimal('.4'),'trade_count':100,'reasons':[]}
        class History:
            def __init__(self,*args):pass
            def history(self,wallet,*args,**kwargs):
                return {'transactions':pair(at=END-timedelta(hours=3)),
                        'pages_complete':True,'truncated':False,'failures':[]}
        config={'criteria_version':'test','api':{},'helius':{},'profit':{
            'max_prefilters':4,'max_persistence':3,'max_deep':1}}
        with TemporaryDirectory() as directory, Store(Path(directory)/'state.sqlite') as store:
            checkpoints=[];budget=Budget();output=Path(directory)/'reports'
            with patch('wallets.profit_pipeline.BirdeyeClient'),patch('wallets.profit_pipeline.HeliusClient'), \
                 patch('wallets.profit_pipeline.discover',return_value={'candidates':candidates,'errors':[],'rejections':[]}), \
                 patch('wallets.profit_pipeline.prefilter_wallet',side_effect=prefilter), \
                 patch('wallets.profit_pipeline.HeliusHistory',History), \
                 patch('wallets.profit_pipeline._birdeye_trade_evidence',return_value=([],{})), \
                 patch('wallets.profit_pipeline.utcnow',return_value=END), \
                 patch('urllib.request.urlopen',side_effect=AssertionError('Sem rede')):
                summary=run_pipeline(store,config,budget,lambda:checkpoints.append(1),output,now=END)
            self.assertEqual(budget.names,['discovery','prefilter','persistence','deep'])
            self.assertEqual(sum(days==7 for _,days in calls),4)
            self.assertEqual(sum(days==30 for _,days in calls),3)
            self.assertEqual(summary['stages']['pnl_responses'],4)
            self.assertEqual(summary['stages']['prefilter_passed'],4)
            self.assertEqual(summary['stages']['deep'],1)
            self.assertEqual(summary['stages']['top3'],3)
            saved=latest_run(store)
            self.assertEqual(len(saved['ranking']),3)
            self.assertTrue(any(row.get('deep',{}).get('metrics') for row in saved['candidates']))
            self.assertTrue(all(not row['telegram_eligible'] for row in saved['ranking']))
            self.assertGreaterEqual(len(checkpoints),2)
            self.assertTrue((output/'ranking_carteiras.xlsx').is_file())
            for path in output.iterdir():
                if path.suffix!='.xlsx':
                    text=path.read_text()
                    for candidate in candidates:self.assertNotIn(candidate['wallet'],text)
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM notifications').fetchone()[0],0)

    def test_cheap_order_prefers_repeat_and_quality_before_largest_isolated_pnl(self):
        a={'wallet':'a','origins':[],'prefilter7':{'passed':True,'pnl_usd':999999,'tokens_count':1,'profit_factor':.5,'top3_share':1}}
        b={'wallet':'b','origins':[],'prefilter7':{'passed':True,'pnl_usd':100,'tokens_count':40,'profit_factor':2,'top3_share':.4}}
        c={'wallet':'c','origins':['x','y'],'prefilter7':{'passed':True,'pnl_usd':50}}
        self.assertEqual([r['wallet'] for r in sorted([a,b,c],key=_provider_order)],['c','b','a'])

    def test_family_circuits_propagate_auth_but_preserve_global_budgets_after_429(self):
        from types import SimpleNamespace
        api={'max_requests_per_run':60,'max_credits_per_run':800}
        blocked=SimpleNamespace(_halted=True,last_http_status=429,requests=2,credits=80,
                                _last_call=END,not_before=END+timedelta(seconds=120))
        with patch('wallets.profit_pipeline.BirdeyeClient') as factory:
            _family_client(object(),api,[blocked])
            settings=factory.call_args.args[2]
            self.assertEqual(settings['max_requests_per_run'],58)
            self.assertEqual(settings['max_credits_per_run'],720)
            factory.assert_called_once()
            self.assertEqual(factory.return_value._last_call,END)
            self.assertEqual(factory.return_value.not_before,END+timedelta(seconds=120))
        for status in (401,402,403):
            blocked.last_http_status=status
            with patch('wallets.profit_pipeline.BirdeyeClient') as factory:
                self.assertIs(_family_client(object(),api,[blocked]),blocked)
                factory.assert_not_called()

    def test_wallet_family_429_does_not_retry_on_another_wallet(self):
        from io import BytesIO
        from urllib.error import HTTPError
        from wallets.birdeye import BirdeyeClient
        from wallets.profit_prefilter import prefilter_wallet
        calls=[]
        def transport(request):
            calls.append(1)
            raise HTTPError(request.full_url,429,'quota',{},BytesIO(b'{"message":"Rate limited"}'))
        with TemporaryDirectory() as directory,Store(Path(directory)/'private.sqlite') as store:
            client=BirdeyeClient('fixture',store,{'max_retries':0,'max_credits_per_run':210},transport=transport)
            a=prefilter_wallet(client,'A'*44,7,{},END)
            b=prefilter_wallet(client,'B'*44,7,{},END)
            self.assertEqual(len(calls),1)
            self.assertFalse(a['passed']);self.assertFalse(b['passed'])
            self.assertEqual(a['raw_metrics']['failure_http_status'],429)

    def test_current_provider_profile_cannot_be_used_as_a_backtest(self):
        with patch('wallets.profit_pipeline.utcnow',return_value=END),self.assertRaises(ValueError):
            run_pipeline(None,{'profit':{}},None,None,None,now=END-timedelta(hours=1))

    def test_missing_30d_can_produce_partial_deep_evidence_but_known_failures_cannot(self):
        self.assertTrue(_deep_allowed({'passed':True,'pnl_usd':1}))
        self.assertTrue(_deep_allowed({'passed':False,'pnl_usd':None,'reasons':['pnl_unavailable:AccessDenied']}))
        self.assertFalse(_deep_allowed(None))
        self.assertFalse(_deep_allowed({'passed':False,'pnl_usd':-1,'reasons':['provider_pnl_not_positive']}))
        self.assertFalse(_deep_allowed({'passed':False,'pnl_usd':None,'reasons':['provider_future_snapshot']}))
        self.assertFalse(_deep_allowed({'passed':False,'pnl_usd':None,'reasons':['activity_over_daily_limit']}))
