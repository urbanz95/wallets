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
from wallets.profit_pipeline import run_pipeline, _provider_order
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
                    'complete':True,'tokens_count':40,'profit_factor':Decimal(2),
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
