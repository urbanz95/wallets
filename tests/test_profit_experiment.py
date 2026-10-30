"""Executor de ranking: estado fresco, quotas por estágio e saída pública privada."""
from copy import deepcopy
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from cryptography.fernet import Fernet

SCRIPTS=Path(__file__).parents[1]/'scripts'
if str(SCRIPTS) not in sys.path:sys.path.insert(0,str(SCRIPTS))
import profit_experiment as module
from test_recent_experiment import FakeGitHub
from wallets.storage import Store

NOW=datetime(2026,10,30,12,tzinfo=timezone.utc)
WALLET='A'*44
TOKEN='B'*44


class ProfitExperimentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.production=self.root/'production.sqlite';self.experiment=self.root/'profit.sqlite'
        self.output=self.root/'reports'
        self.pg,self.eg=FakeGitHub(),FakeGitHub();key=Fernet.generate_key()
        self.pv=module.Vault(key,self.pg);self.ev=module.Vault(key,self.eg,compress_snapshot=True)
        self.pv.init(self.production)
        with Store(self.production) as source:
            source.record_discovery(WALLET,TOKEN,NOW,{'source':'fixture'})
            source.set_cursor('production-only','preserve')
        self.pv.save(self.production)
        self.config={'profit':{'enabled':True},'selection':{'notifications':'strict'},
          'api':{'max_credits_per_run':800,'monthly_credit_ceiling':28000},
          'helius':{'enabled':True,'max_credits_per_run':300,'monthly_credit_ceiling':900000}}
        no_network=patch('socket.socket.connect',side_effect=AssertionError('Rede proibida'))
        no_network.start();self.addCleanup(no_network.stop)

    def call(self,pipeline,**kwargs):
        return module.run_experiment(self.production,self.experiment,self.output,self.config,
                                     self.pv,self.ev,now=NOW,pipeline=pipeline,**kwargs)

    def pipeline(self,store,config,budget,checkpoint,output,now=None):
        self.assertEqual(store.wallets(),[])
        self.assertFalse(store.reserve_budget('2026-10',1,28000))
        with budget.stage('discovery',birdeye=230,store=store):
            self.assertTrue(store.reserve_budget('2026-10',30,28000))
        with budget.stage('deep',birdeye=270,helius=200,store=store):
            self.assertTrue(store.reserve_budget('2026-10',12,28000))
            self.assertTrue(store.reserve_budget('2026-10',100,900000,namespace='helius'))
            store.record_discovery(WALLET,TOKEN,NOW,{'source':'fixture-private'})
        checkpoint()
        return {'counts':{'candidates':100,'prefilter':7,'deep':2,'top3':3},
                'rejection_histogram':{'over_frequency':5},
                'top3':[{'wallet':WALLET}],'tokens':[TOKEN]}

    def test_fresh_experiment_does_not_copy_raw_or_wallets_and_refunds_unused(self):
        summary=self.call(self.pipeline)
        self.assertEqual(summary['counts']['top3'],3)
        self.assertEqual(summary['telegram_sent'],0)
        self.assertNotIn(WALLET,json.dumps(summary));self.assertNotIn(TOKEN,json.dumps(summary))
        with Store(self.production) as source,Store(self.experiment) as target:
            self.assertEqual(source.db.execute('SELECT credits FROM budgets').fetchone()[0],42)
            self.assertEqual(source.db.execute('SELECT credits FROM provider_budgets').fetchone()[0],100)
            self.assertEqual(source.get_cursor('production-only'),'preserve')
            self.assertIsNone(target.get_cursor('production-only'))
            self.assertEqual(source.db.execute('SELECT COUNT(*) FROM evaluations').fetchone()[0],0)
            self.assertEqual(target.db.execute('SELECT COUNT(*) FROM notifications').fetchone()[0],0)
        self.assertEqual(self.experiment.stat().st_mode & 0o777,0o600)
        public=(self.output/'experiment-summary.json').read_text()
        self.assertNotIn(WALLET,public);self.assertNotIn(TOKEN,public)
        for body in self.eg.files.values():self.assertNotIn(WALLET.encode(),body)

    def test_existing_experiment_recovers_without_reset(self):
        self.call(self.pipeline)
        self.experiment.unlink()
        def resumed(store,config,budget,checkpoint,output,now=None):
            self.assertEqual(store.wallets(),[WALLET])
            return {'counts':{'deep':0},'rejection_histogram':{}}
        summary=self.call(resumed)
        self.assertEqual(summary['counts'],{'deep':0})
        with Store(self.production) as source:
            self.assertEqual(source.db.execute('SELECT credits FROM budgets').fetchone()[0],42)

    def test_space_failure_precedes_all_quota_reservations_and_runner(self):
        runner=Mock();guard=Mock(side_effect=module.ProfitBudgetError('Sem espaço'))
        with self.assertRaises(module.ProfitBudgetError):self.call(runner,space_check=guard)
        runner.assert_not_called()
        with Store(self.production) as source:
            self.assertEqual(source.db.execute('SELECT COUNT(*) FROM budgets').fetchone()[0],0)
        self.assertIsNone(self.eg.data)

    def test_pipeline_failure_preserves_partial_state_and_attempt_cost(self):
        def fail(store,config,budget,checkpoint,output,now=None):
            with budget.stage('discovery',birdeye=230,store=store):
                store.reserve_budget('2026-10',30,28000)
                store.set_cursor('partial','saved')
                raise TimeoutError('Resultado incerto')
        with self.assertRaises(TimeoutError):self.call(fail)
        with Store(self.production) as source:
            self.assertEqual(source.db.execute('SELECT credits FROM budgets').fetchone()[0],30)
        restored=self.root/'restored.sqlite';self.ev.restore(restored)
        with Store(restored) as recovered:self.assertEqual(recovered.get_cursor('partial'),'saved')

    def test_notifications_or_same_database_are_refused(self):
        with self.assertRaises(module.ExperimentError):
            module.run_experiment(self.production,self.production,self.output,self.config,self.pv,self.ev,pipeline=Mock())
        config=deepcopy(self.config);config['selection']['notifications']='observed'
        with self.assertRaises(module.ExperimentError):
            module.run_experiment(self.production,self.experiment,self.output,config,self.pv,self.ev,pipeline=Mock())

    def test_main_requires_only_market_and_encryption_secrets_no_telegram(self):
        with patch.dict('os.environ',{},clear=True),patch('builtins.print') as output:
            self.assertEqual(module.main([]),1)
        self.assertNotIn('TELEGRAM',str(output.call_args))

    def test_probe_option_selects_single_wallet_probe_pipeline(self):
        from wallets.profit_probe import run_probe
        with patch.dict('os.environ', {'STATE_ENCRYPTION_KEY': 'fixture-key',
                        'BIRDEYE_API_KEY': 'fixture-birdeye', 'HELIUS_API_KEY': 'fixture-helius'}, clear=True), \
             patch.object(module.cli, 'load_config', return_value=self.config), \
             patch.object(module, 'Vault'), patch.object(module, 'ExperimentGitHub'), \
             patch.object(module, 'run_experiment', return_value={'telegram_sent': 0}) as runner, \
             patch('builtins.print'):
            self.assertEqual(module.main(['--probe-wallet-access']), 0)
        self.assertIs(runner.call_args.kwargs['pipeline'], run_probe)

    def test_public_summary_only_copies_numeric_counts_histogram_and_costs(self):
        budget=SimpleNamespace(stats={'discovery':{'state':WALLET,
            'birdeye':{'reserved':230,'attempted':30,'refunded':200,'requests':1},'helius':{}}})
        result={'counts':{'candidates':100,'wallet':WALLET,WALLET:1},
                'rejection_histogram':{'high_frequency':3,'private_address':WALLET},
                'top3':[WALLET],'tokens':[TOKEN],'anything':{'wallet':WALLET}}
        summary=module.public_summary(result,budget,NOW)
        self.assertEqual(summary['counts'],{'candidates':100})
        self.assertEqual(summary['rejection_histogram'],{'high_frequency':3})
        self.assertNotIn(WALLET,json.dumps(summary));self.assertNotIn(TOKEN,json.dumps(summary))
