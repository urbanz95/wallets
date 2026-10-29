"""Janela e fila recentes: mocks, SQLite local e nenhum Telegram/rede."""
from argparse import Namespace
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from wallets import cli, recent
from wallets.birdeye import BirdeyeError, BudgetExceeded
from wallets.checks import evaluate
from wallets.models import Coverage, Event, iso
from wallets.storage import Store

NOW = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
WALLET = 'A'*44
TOKEN = 'B'*44
ROOT = Path(__file__).parents[1]


def profile():
    config=deepcopy(cli.load_config(ROOT/'config.toml'))
    config['lookback_days']=7
    config['recent']={'enabled':True,'window_days':7,'require_activity_hours':24,
                      'min_activity_days':4,'min_profitable_days':3,
                      'discovery_credit_budget':160,'discovery_request_budget':5}
    config['discovery'].update(min_age_days=0,max_age_days=7,time_frame='7d',max_wallets=3,max_tokens=3)
    config['api']['collect_optional']=False
    return config


class RecentQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'state.sqlite');self.addCleanup(self.store.close)
        self.start=NOW-timedelta(days=7)

    def origin(self,wallet,*,when=NOW,listed=None,token=TOKEN,**extra):
        self.store.record_discovery(wallet,token,when,{
            'profile':'recent-7d','time_frame':'7d',
            'recent_listing_time':iso(listed or NOW-timedelta(days=2)),**extra})

    def test_only_recent_profile_origins_survive_not_legacy_expired_future_or_old_tokens(self):
        self.store.record_discovery('legacy',TOKEN,NOW,{'endpoint':'/defi/v2/tokens/top_traders'})
        self.origin('expired',when=NOW-timedelta(days=8))
        self.origin('future',when=NOW+timedelta(seconds=1))
        self.origin('old-token',listed=NOW-timedelta(days=8))
        self.origin('wrong-window',time_frame='30d')
        self.origin('eligible')
        selected,counts=recent.select_candidates(self.store,{},[],[],self.start,NOW,10)
        self.assertEqual(selected,['eligible'])
        self.assertEqual(counts['stored_without_recent_origin'],5)

    def test_helius_origin_revalidates_listing_age_from_saved_selection(self):
        self.store.record_discovery('helius-candidate',TOKEN,NOW,{
            'profile':'recent-7d','source':'helius','origin':'helius_first_observed'})
        self.store.record_token_selection(TOKEN,NOW,{
            'recent_listing_time':iso(NOW-timedelta(days=6))})
        selected,_=recent.select_candidates(self.store,{},[],[],self.start,NOW,3)
        self.assertEqual(selected,['helius-candidate'])
        later=NOW+timedelta(days=2)
        selected,counts=recent.select_candidates(self.store,{},[],[],later-timedelta(days=7),later,3)
        self.assertEqual(selected,[])
        self.assertEqual(counts['stored_without_recent_origin'],1)

    def test_unknown_or_future_listing_evidence_cannot_reuse_a_stored_helius_origin(self):
        self.store.record_discovery('unknown-listing',TOKEN,NOW,{
            'profile':'recent-7d','source':'helius','recent_listing_time':None})
        selected,_=recent.select_candidates(self.store,{},[],[],self.start,NOW,3)
        self.assertEqual(selected,[])
        self.store.record_token_selection(TOKEN,NOW+timedelta(seconds=1),{
            'recent_listing_time':iso(NOW-timedelta(days=1))})
        selected,_=recent.select_candidates(self.store,{},[],[],self.start,NOW,3)
        self.assertEqual(selected,[])
        # Uma origem fresca explicitamente validada nesta execução pode ser
        # recolhida, mas não recebe peso por uma listagem desconhecida.
        selected,counts=recent.select_candidates(self.store,{},['unknown-listing'],[],self.start,NOW,3)
        self.assertEqual(selected,['unknown-listing'])
        self.assertEqual(counts['selected_new_distinct_origins'],[0])

    def test_partial_discovery_recovery_uses_only_exact_cutoff_in_insertion_order(self):
        self.origin('old-five-hours',when=NOW-timedelta(hours=5))
        self.origin('new-z')
        self.origin('new-a')
        self.origin('new-z',token='C'*44)
        self.origin('future',when=NOW+timedelta(seconds=1))
        self.origin('wrong-profile',profile='legacy')
        self.origin('old-token-at-cutoff',listed=NOW-timedelta(days=8))
        recovered=recent.newly_observed_candidates(self.store,self.start,NOW)
        self.assertEqual(recovered,['new-z','new-a'])

    def test_two_pending_slots_and_one_new_slot_are_reserved(self):
        history={}
        for index in range(4):
            wallet=f'pending-{index}';self.origin(wallet)
            history[wallet]=SimpleNamespace(observed_eligible=False,evaluated_at=NOW-timedelta(hours=4-index))
        selected,counts=recent.select_candidates(self.store,history,['new-1','new-2','new-3'],[],self.start,NOW,3)
        self.assertEqual(selected,['pending-0','pending-1','new-1'])
        self.assertEqual((counts['selected_pending'],counts['selected_new']),(2,1))

    def test_multiple_recent_token_origins_rank_first_without_duplicate_or_old_inflation(self):
        history={}
        for index in range(2):
            wallet=f'pending-{index}';self.origin(wallet)
            history[wallet]=SimpleNamespace(observed_eligible=False,evaluated_at=NOW-timedelta(hours=2-index))
        for wallet in ('single-1','single-2','multi','duplicates'):self.origin(wallet)
        self.origin('multi',token='C'*44)
        # Repeated observations of one mint and an expired second mint add no weight.
        self.origin('duplicates',when=NOW-timedelta(seconds=1))
        self.origin('duplicates',when=NOW-timedelta(seconds=2))
        self.origin('duplicates',token='D'*44,when=NOW-timedelta(days=8))
        fresh=['single-2','single-1','duplicates','multi']
        selected,counts=recent.select_candidates(self.store,history,fresh,[],self.start,NOW,3)
        self.assertEqual(selected,['pending-0','pending-1','multi'])
        self.assertEqual((counts['selected_pending'],counts['selected_new']),(2,1))
        self.assertEqual(counts['selected_new_distinct_origins'],[2])
        # With no pending analyses, equal scores retain fresh discovery order.
        selected,_=recent.select_candidates(self.store,{},fresh,[],self.start,NOW,4)
        self.assertEqual(selected,['multi','single-2','single-1','duplicates'])

    def test_manual_first_deduplicated_and_one_slot_queue_still_advances_pending(self):
        self.origin('pending')
        history={'pending':SimpleNamespace(observed_eligible=False,evaluated_at=NOW-timedelta(hours=1))}
        selected,_=recent.select_candidates(self.store,history,['new','new'],['manual','manual'],self.start,NOW,3)
        self.assertEqual(selected,['manual','pending','new'])
        selected,_=recent.select_candidates(self.store,history,['new'],[],self.start,NOW,1)
        self.assertEqual(selected,['pending'])

    def test_incomplete_pending_precedes_already_selected_and_new_fills_free_slots(self):
        for wallet in ('selected','pending'):self.origin(wallet)
        history={'selected':SimpleNamespace(observed_eligible=True,evaluated_at=NOW-timedelta(days=2)),
                 'pending':SimpleNamespace(observed_eligible=False,evaluated_at=NOW-timedelta(hours=1))}
        selected,_=recent.select_candidates(self.store,history,['new'],[],self.start,NOW,3)
        self.assertEqual(selected,['pending','selected','new'])
        for wallet in ('n1','n2','n3'):self.origin(wallet)
        selected,_=recent.select_candidates(self.store,{},['n1','n2','n3'],[],self.start,NOW,3)
        self.assertEqual(selected,['n1','n2','n3'])


class RecentConfigurationTests(unittest.TestCase):
    def test_valid_profile_and_legacy_are_accepted(self):
        recent.validate_recent(profile())
        recent.validate_recent(cli.load_config(ROOT/'config.toml'))

    def test_incompatible_windows_age_and_budgets_are_rejected(self):
        changes=[('recent','window_days',8),('recent','enabled','yes'),
                 ('recent','min_activity_days',14),('recent','min_profitable_days',8),
                 ('recent','require_activity_hours',True),('recent','require_activity_hours',25),
                 ('recent','discovery_credit_budget',801),('recent','discovery_request_budget',41),
                 ('discovery','time_frame','30d'),('discovery','max_age_days',8),
                 ('api','collect_optional','false'),('selection','mode','strict')]
        for group,name,value in changes:
            config=profile();config[group][name]=value
            with self.subTest(group=group,name=name,value=value),self.assertRaises(ValueError):
                recent.validate_recent(config)
        config=profile();config['lookback_days']=90
        with self.assertRaises(ValueError):recent.validate_recent(config)


class RecentRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.db=self.root/'state.sqlite';self.output=self.root/'reports'
        self.config=profile();self.config['helius']['enabled']=False
        self.args=Namespace(db=str(self.db),output=str(self.output),checkpoint_command=None,wallet=[],token=[],notify=False)
        for name in ('urllib.request.OpenerDirector.open','urllib.request.urlopen','socket.socket.connect'):
            blocker=patch(name,side_effect=AssertionError('Rede proibida'));blocker.start();self.addCleanup(blocker.stop)
        env=patch.dict(os.environ,{'BIRDEYE_API_KEY':'fixture-api-key','HELIUS_API_KEY':'',
                                  'TELEGRAM_BOT_TOKEN':'','TELEGRAM_CHAT_ID':'','GITHUB_ACTIONS':''})
        env.start();self.addCleanup(env.stop)

    def test_real_run_uses_seven_days_restores_budget_filters_cutoff_and_preserves_legacy_snapshot(self):
        with Store(self.db) as store:
            old=evaluate('C'*44,[],Coverage(NOW-timedelta(days=90),NOW-timedelta(hours=2),NOW-timedelta(hours=2)),
                         [],[],cli.load_config(ROOT/'config.toml'),NOW-timedelta(hours=2))
            old_id=store.save_evaluation(old)
        calls=[];checkpoint=Mock();client=SimpleNamespace(config=dict(self.config['api']),requests=0,credits=0)
        class RecordedCollector:
            def __init__(self,api,store,config):
                self.client,self.store=api,store;self.limitations=[];self.selected_tokens=[]
            def discover(self,now):
                calls.append(('discovery',dict(self.client.config)))
                self.store.record_discovery(WALLET,TOKEN,now,{'profile':'recent-7d','time_frame':'7d',
                    'recent_listing_time':iso(now-timedelta(days=2))})
                return [WALLET]
            def collect(self,wallet,start,end):
                calls.append(('collect',start,end,dict(self.client.config)))
                events=[Event('buy',wallet,TOKEN,end-timedelta(hours=2),'buy',Decimal(1),Decimal(10)),
                        Event('sell',wallet,TOKEN,end-timedelta(hours=1),'sell',Decimal(1),Decimal(12)),
                        Event('old',wallet,TOKEN,end-timedelta(days=8),'buy',Decimal(1),Decimal(1)),
                        Event('cutoff',wallet,TOKEN,end,'buy',Decimal(1),Decimal(1))]
                self.store.put_events(events)
                return events,Coverage(start,end,end,complete=True,source='birdeye'),{
                    'pages_complete':{'trades':True,'transfers':True},
                    'history_scope':{'start':iso(start),'end':iso(end)}}
        with patch('wallets.cli.utcnow',return_value=NOW),patch('wallets.cli.BirdeyeClient',return_value=client),\
             patch('wallets.cli.Collector',RecordedCollector),patch('wallets.cli.Notifier') as notifier,\
             redirect_stdout(StringIO()):
            self.assertEqual(cli.run(self.args,self.config,checkpoint_override=checkpoint),0)
        notifier.assert_not_called()
        self.assertEqual(calls[0][1]['max_credits_per_run'],160)
        self.assertEqual(calls[0][1]['max_requests_per_run'],5)
        self.assertEqual(calls[1][1:3],(NOW-timedelta(days=7),NOW))
        self.assertEqual(calls[1][3]['max_credits_per_run'],800)
        self.assertEqual(self.config['api']['max_credits_per_run'],800)
        self.assertEqual(checkpoint.call_count,2)
        report=json.loads((self.output/'report.json').read_text())
        self.assertEqual(len(report['evaluations']),1)
        current=report['evaluations'][0]
        self.assertEqual(current['wallet'],WALLET)
        self.assertEqual(current['metrics']['selection']['metrics']['gross_result_before_fees'],'2')
        self.assertFalse(any(c['status']=='APROVADA' for c in current['checks']))
        self.assertIn('recent-7d',current['metrics']['provider_metrics']['recent_profile']['name'])
        with Store(self.db) as store:
            self.assertIsNotNone(store.get_evaluation(old_id))
            self.assertEqual(len(store.latest_evaluations()),2)

    def test_quota_after_recording_new_origins_preserves_fresh_order_and_pending_slots(self):
        old_token='C'*44
        pending=['D'*44,'E'*44]
        old_unassessed='1'*44
        previous=NOW-timedelta(hours=5)
        evidence={'profile':'recent-7d','time_frame':'7d',
                  'recent_listing_time':iso(NOW-timedelta(days=2))}
        with Store(self.db) as store:
            for wallet in pending+[old_unassessed]:
                store.record_discovery(wallet,old_token,previous,evidence)
            for wallet in pending:
                old=evaluate(wallet,[],Coverage(NOW-timedelta(days=7),previous,previous),
                             [],[old_token],self.config,previous)
                store.save_evaluation(old)
        fresh=['z'*43+character for character in '123456789A']
        calls=[]
        client=SimpleNamespace(config=dict(self.config['api']),requests=0,credits=0)
        class PartialCollector:
            def __init__(self,api,store,config):
                self.client,self.store=api,store;self.limitations=[];self.selected_tokens=[]
            def discover(self,now):
                for wallet in fresh:
                    self.store.record_discovery(wallet,TOKEN,now,evidence)
                raise BudgetExceeded('Limite de descoberta atingido depois de guardar origens.')
            def collect(self,wallet,start,end):
                calls.append(wallet)
                return [],Coverage(start,end,end),{}
        with patch('wallets.cli.utcnow',return_value=NOW),patch('wallets.cli.BirdeyeClient',return_value=client),             patch('wallets.cli.Collector',PartialCollector),patch('wallets.cli.Notifier') as notifier,             redirect_stdout(StringIO()):
            self.assertEqual(cli.run(self.args,self.config,checkpoint_override=Mock()),0)
        self.assertEqual(calls,pending+[fresh[0]])
        self.assertNotIn(old_unassessed,calls)
        self.assertEqual(client.config['max_credits_per_run'],800)
        notifier.assert_not_called()
        report=json.loads((self.output/'report.json').read_text())
        queue_note=next(note for note in report['notes'] if note.startswith('Fila recente (contagens): '))
        queue=json.loads(queue_note.split(': ',1)[1])
        self.assertEqual((queue['selected_pending'],queue['selected_new']),(2,1))
        self.assertEqual(queue['new_candidates'],11)

    def test_internal_store_injection_requires_recent_profile_and_no_notify(self):
        for config,notify in ((cli.load_config(ROOT/'config.toml'),False),(self.config,True)):
            self.args.notify=notify
            with self.subTest(notify=notify),self.assertRaises(ValueError):
                cli.run(self.args,config,store_factory=Mock(),checkpoint_override=Mock())


if __name__=='__main__':unittest.main()
