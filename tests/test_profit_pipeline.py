"""Teste do fluxo integral sem rede nem credenciais: três estágios e privacidade."""
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import tomllib
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
        candidates=[{'wallet':wallet,'origins':[],'discovery':{}} for wallet in ('A'*44,'B'*44,'C'*44,'D'*44)]
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

    def _run_refinement_with_real_ledger(self, *, negative_refinement=False):
        from wallets.profit_budget import ProfitBudget
        from wallets.models import iso
        config=tomllib.loads((Path(__file__).resolve().parents[1]/'config.profit.toml').read_text())
        month=END.strftime('%Y-%m'); testcase=self; calls=[]
        wallets=[letter*44 for letter in ('A' if negative_refinement else 'ABCDEFGHJ')]
        candidates=[{'wallet':wallet,'origins':[],'warnings':['activity_over_daily_limit'],
                     'discovery':{'observed_at':iso(END),'provider_pnl_usd':100}} for wallet in wallets]

        class ChargedClient:
            def __init__(self,key,store,settings,namespace='birdeye'):
                self.store=store;self.settings=settings;self.namespace=namespace
                self.requests=0;self.credits=0;self._halted=False
            def charge(self,cost):
                testcase.assertLessEqual(self.credits+cost,self.settings['max_credits_per_run'])
                testcase.assertLess(self.requests,self.settings['max_requests_per_run'])
                testcase.assertTrue(self.store.reserve_budget(month,cost,
                    self.settings['monthly_credit_ceiling'],namespace=self.namespace))
                self.requests+=1;self.credits+=cost

        def discover(client,*args):
            for cost in (30,50,30,30,30,30,30):client.charge(cost)
            return {'candidates':candidates,'errors':[],'rejections':[]}

        def prefilter(client,wallet,days,settings,now):
            pages=settings['profit']['prefilter_max_pages']
            calls.append((wallet,days,pages))
            for _ in range(pages):client.charge(30)
            refined=days==7 and pages>1
            positive=not (negative_refinement and refined)
            return {'wallet':wallet,'days':days,'passed':positive,'pnl_usd':Decimal(100 if positive else -100),
                    'complete':refined,'response_received':True,'_future_snapshot':False,
                    'tokens_count':101,'trade_count':3500,'trades_per_day':Decimal(500),
                    'warnings':['activity_over_daily_limit'],
                    'reasons':[] if positive else ['provider_pnl_not_positive'],
                    'profit_factor':Decimal(2) if refined and positive else None,
                    'top3_share':Decimal('.3') if refined and positive else None,
                    'raw_metrics':{'method':'wac','position_scope':'duration_only','duration':str(days)+'d',
                        'snapshot_at':iso(END-timedelta(minutes=1)),'fetched_at':iso(END),
                        'excluded_settlement_tokens_count':0}}

        def trade_evidence(client,wallet,start,end,pages):
            for _ in range(pages):client.charge(12)
            return [],{}

        def evaluate(store,client,*args,**kwargs):
            client.charge(100)
            return ({'metrics':{'currency':'SOL','tokens_count':101,'known_tokens_count':0,
                               'unknown_tokens_count':101,'unknown_cost_fraction':Decimal(1)},
                     'tokens':[],'coverage':{},'limitations':['Interpretação por concluir.']},
                    {'pages_complete':False,'truncated':True,'failures':[]},{},[])

        with TemporaryDirectory() as directory, Store(Path(directory)/'central.sqlite') as central, \
             Store(Path(directory)/'experiment.sqlite') as store:
            budget=ProfitBudget(central,lambda:None,config,now=END,space_check=lambda *a,**kw:None)
            budget.bind_store(store)
            with patch('wallets.profit_pipeline.BirdeyeClient',side_effect=ChargedClient), \
                 patch('wallets.profit_pipeline.HeliusClient',side_effect=lambda key,db,settings: ChargedClient(key,db,settings,'helius')), \
                 patch('wallets.profit_pipeline.discover',side_effect=discover), \
                 patch('wallets.profit_pipeline.prefilter_wallet',side_effect=prefilter), \
                 patch('wallets.profit_refinement.prefilter_wallet',side_effect=prefilter), \
                 patch('wallets.profit_pipeline._birdeye_trade_evidence',side_effect=trade_evidence), \
                 patch('wallets.profit_pipeline.evaluate_deep',side_effect=evaluate) as deep, \
                 patch('wallets.profit_pipeline.utcnow',return_value=END), \
                 patch('socket.socket.connect',side_effect=AssertionError('Rede proibida')):
                summary=run_pipeline(store,config,budget,lambda:None,Path(directory)/'reports',now=END)
            snapshot=latest_run(store)
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM notifications').fetchone()[0],0)
            self.assertTrue(all(not row['telegram_eligible'] and row['evidence_level']!='A' for row in snapshot['ranking']))
            ledger=central.db.execute('SELECT credits FROM budgets WHERE month=?',(month,)).fetchone()[0]
            reserved=sum(stage['birdeye']['reserved'] for stage in budget.stats.values())
            attempted=sum(stage['birdeye']['attempted'] for stage in budget.stats.values())
            self.assertEqual(ledger,attempted)
            self.assertLessEqual(reserved,config['api']['max_credits_per_run'])
            self.assertFalse(store.reserve_budget(month,1,28000))
            return summary,snapshot,budget.stats,calls,deep.call_count,ledger

    def test_high_frequency_refinement_reaches_30d_and_deep_with_real_stage_budgets(self):
        summary,snapshot,budgets,calls,deep_count,ledger=self._run_refinement_with_real_ledger()
        self.assertEqual(ledger,782)
        self.assertEqual(budgets['refinement']['birdeye']['reserved'],120)
        self.assertEqual(budgets['refinement']['birdeye']['attempted'],120)
        self.assertEqual(summary['stages']['prefilter_passed'],9)
        self.assertEqual(summary['stages']['refinement_complete'],2)
        self.assertEqual(sum(days==30 for _,days,_ in calls),3)
        self.assertEqual(deep_count,2)
        self.assertEqual(summary['warnings']['activity_over_daily_limit'],9)
        self.assertEqual(summary['telegram_sent'],0)
        self.assertTrue(all(row['prefilter7']['passed'] for row in snapshot['candidates']))

    def test_complete_negative_refinement_preserves_initial_but_stops_later_stages(self):
        summary,snapshot,budgets,calls,deep_count,ledger=self._run_refinement_with_real_ledger(negative_refinement=True)
        self.assertEqual(ledger,320)
        self.assertEqual(summary['stages']['refinement_complete'],1)
        self.assertFalse(any(days==30 for _,days,_ in calls))
        self.assertEqual(deep_count,0)
        self.assertEqual(budgets['persistence']['birdeye']['reserved'],0)
        self.assertEqual(budgets['deep']['birdeye']['reserved'],0)
        row=snapshot['candidates'][0]
        self.assertTrue(row['prefilter7_initial']['passed'])
        self.assertFalse(row['prefilter7']['passed'])
        self.assertEqual(Decimal(row['prefilter7']['pnl_usd']),Decimal(-100))
        self.assertEqual(summary['telegram_sent'],0)

    def test_cheap_order_prefers_repeat_and_quality_before_largest_isolated_pnl(self):
        a={'wallet':'a','origins':[],'prefilter7':{'passed':True,'pnl_usd':999999,'tokens_count':1,'profit_factor':.5,'top3_share':1}}
        b={'wallet':'b','origins':[],'prefilter7':{'passed':True,'pnl_usd':100,'tokens_count':40,'profit_factor':2,'top3_share':.4}}
        c={'wallet':'c','origins':['x','y'],'prefilter7':{'passed':True,'pnl_usd':50}}
        self.assertEqual([r['wallet'] for r in sorted([a,b,c],key=_provider_order)],['b','c','a'])

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
        self.assertTrue(_deep_allowed({'passed':True,'pnl_usd':100,'warnings':['activity_over_daily_limit'],'reasons':[]}))
