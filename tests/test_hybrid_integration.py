"""Integração das fontes e gate com dados sintéticos; todos os sockets bloqueados."""
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

from test_checks import NOW, full_case
from wallets import cli
from wallets.birdeye import BirdeyeClient
from wallets.checks import evaluate
from wallets.collection import Collector
from wallets.helius import HeliusClient
from wallets.helius_collection import HeliusHistory
from wallets.models import Event, SOL, Status
from wallets.pipeline import augment_with_helius
from wallets.reconciliation import reconcile
from wallets.storage import Store
from wallets.telegram import Notifier, eligible_for_delivery

ROOT=Path(__file__).parents[1]
CONFIG=ROOT/'config.toml'
MEME='B'*44
USDC='EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
AT=NOW-timedelta(days=1)


def event(source='birdeye',*,kind='buy',quantity='1',token=MEME,tx_hash='same',usd='100',timestamp=AT,id=None):
    return Event(id or source+':'+tx_hash+':'+token+':'+kind,SOL,token,timestamp,kind,D(quantity),
                 D(usd) if usd is not None else None,None,tx_hash,100,source)


def raw_tx():
    return {'slot':100,'transactionIndex':0,'blockTime':int(AT.timestamp()),
      'transaction':{'signatures':['same'],'message':{'accountKeys':[SOL,MEME,USDC], 'instructions':[]}},
      'meta':{'err':None,'fee':5000,'preBalances':[1000000,0,0],'postBalances':[995000,0,0],
        'preTokenBalances':[
          {'accountIndex':1,'mint':MEME,'owner':SOL,'uiTokenAmount':{'amount':'0','decimals':6}},
          {'accountIndex':2,'mint':USDC,'owner':SOL,'uiTokenAmount':{'amount':'5000000','decimals':6}}],
        'postTokenBalances':[
          {'accountIndex':1,'mint':MEME,'owner':SOL,'uiTokenAmount':{'amount':'1000000','decimals':6}},
          {'accountIndex':2,'mint':USDC,'owner':SOL,'uiTokenAmount':{'amount':'3000000','decimals':6}}],
        'innerInstructions':[]}}


class HybridIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name);self.path=self.root/'state.sqlite';self.output=self.root/'reports'
        self.store=Store(self.path);self.addCleanup(lambda:self.store.close())
        self.config=cli.load_config(CONFIG)
        for target in ('socket.socket.connect','urllib.request.OpenerDirector.open','urllib.request.urlopen'):
            blocker=patch(target,side_effect=AssertionError('Rede proibida em teste híbrido'))
            blocker.start();self.addCleanup(blocker.stop)
        env=patch.dict(os.environ,{'BIRDEYE_API_KEY':'','HELIUS_API_KEY':'','TELEGRAM_BOT_TOKEN':'',
            'TELEGRAM_CHAT_ID':'','GITHUB_ACTIONS':''})
        env.start();self.addCleanup(env.stop)

    def call(self,command,*args):
        out,err=StringIO(),StringIO()
        with patch('wallets.cli.utcnow',return_value=NOW),redirect_stdout(out),redirect_stderr(err):
            code=cli.main([command,'--config',str(CONFIG),'--db',str(self.path),'--output',str(self.output),*args])
        return code,out.getvalue(),err.getvalue()

    def test_provider_quotas_are_separate_and_persist_after_reopen(self):
        self.assertTrue(self.store.reserve_budget('2026-10',10,10))
        self.assertTrue(self.store.reserve_budget('2026-10',20,20,namespace='helius'))
        self.store.close();self.store=Store(self.path)
        self.assertFalse(self.store.reserve_budget('2026-10',1,10))
        self.assertFalse(self.store.reserve_budget('2026-10',1,20,namespace='helius'))
        self.assertTrue(self.store.reserve_budget('2026-11',20,20,namespace='helius'))

    def test_both_prepaid_reservations_are_durable_before_http(self):
        order=[]
        def checkpoint():
            with Store(self.path) as other:
                order.append((other.db.execute('SELECT credits FROM budgets WHERE month=?',('2026-10',)).fetchone(),
                  other.db.execute('SELECT credits FROM provider_budgets WHERE namespace=? AND month=?',('helius','2026-10')).fetchone()))
        self.assertTrue(self.store.prepay_run('2026-10',30,100,checkpoint))
        self.assertTrue(self.store.prepay_run('2026-10',10,100,checkpoint,namespace='helius'))
        self.assertEqual(order,[((30,),None),((30,),(10,))])
        response={'jsonrpc':'2.0','id':1,'result':{'data':[],'paginationToken':None}}
        def transport(_):
            self.assertEqual(len(order),2)
            return response
        client=HeliusClient('only-a-mock',self.store,dict(self.config['helius'],max_retries=0),transport=transport,clock=lambda:NOW,sleep=lambda _:None)
        client.request('getTransactionsForAddress',[SOL,{'transactionDetails':'signatures','limit':1}])
        self.assertEqual(client.credits,10)
        self.assertEqual(self.store.db.execute('SELECT credits FROM provider_budgets').fetchone()[0],10)

    def test_failed_helius_checkpoint_never_produces_prepaid_budget(self):
        with self.assertRaises(RuntimeError):
            self.store.prepay_run('2026-10',10,100,Mock(side_effect=RuntimeError('checkpoint mock')),namespace='helius')
        self.assertNotIn('helius',self.store._provider_prepaid)
        self.assertEqual(self.store.db.execute('SELECT credits FROM provider_budgets').fetchone()[0],10)

    def test_same_swap_not_counted_twice_and_usd_only_exact_historical_match(self):
        birdeye=[event(),event(kind='sell',token=USDC,quantity='2')]
        helius=[event('helius',usd=None),event('helius',kind='sell',token=USDC,quantity='2',usd=None)]
        combined,proof=reconcile(birdeye,helius)
        self.assertEqual(len(combined),2)
        self.assertEqual(proof['overlapping_transactions'],1)
        self.assertEqual(proof['historical_values_matched'],2)
        self.assertTrue(all(e.source=='birdeye+helius' and e.usd_value==D('100') for e in combined))
        self.assertTrue(all(e.fee_usd is None for e in combined))
        outside,proof=reconcile(birdeye,[replace(helius[0],timestamp=AT-timedelta(seconds=1))])
        self.assertIsNone(outside[0].usd_value)
        self.assertEqual(proof['historical_values_matched'],0)

    def test_quantity_conflict_is_explicit_and_never_keeps_usd(self):
        combined,proof=reconcile([event()],[event('helius',quantity='2',usd=None)])
        self.assertEqual(len(combined),1)
        self.assertEqual(proof['conflicting_transactions'],['same'])
        self.assertIsNone(combined[0].usd_value)
        self.assertTrue(combined[0].multi_step)

    def test_ambiguous_birdeye_legs_never_supply_cost(self):
        combined,proof=reconcile([event(id='one'),event(id='two')],[event('helius',usd=None)])
        self.assertEqual(len(combined),1)
        self.assertIsNone(combined[0].usd_value)
        self.assertEqual(proof['historical_values_matched'],0)

    def test_raw_and_source_events_survive_restart_without_float_loss(self):
        row=raw_tx();row['meta']['postTokenBalances'][0]['uiTokenAmount']['amount']='9007199254740993'
        self.store.put_source_transactions('helius',SOL,[row,row])
        self.store.put_source_events('helius',[event('helius',quantity='9007199254.740993',usd=None)])
        self.store.close();self.store=Store(self.path)
        rows=self.store.get_source_transactions('helius',SOL,AT,NOW)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['meta']['postTokenBalances'][0]['uiTokenAmount']['amount'],'9007199254740993')
        self.assertEqual(self.store.get_source_events('helius',SOL,AT,NOW)[0].quantity,D('9007199254.740993'))
        changed=deepcopy(row);changed['meta']['fee']=10000
        with self.assertRaises(ValueError):self.store.put_source_transactions('helius',SOL,[changed])

    def test_simultaneous_origins_preserved_without_future_evidence(self):
        self.store.record_discovery(SOL,MEME,NOW,{'origin':'birdeye_top_traders'})
        self.store.record_discovery(SOL,MEME,NOW,{'origin':'helius_first_observed'})
        self.store.record_discovery(SOL,MEME,NOW+timedelta(days=1),{'origin':'future'})
        self.store.close();self.store=Store(self.path)
        observations=self.store.discovery_evidence(SOL,NOW)
        self.assertEqual({r['evidence']['origin'] for r in observations},{'birdeye_top_traders','helius_first_observed'})
        self.assertEqual(self.store.discovery_tokens(SOL),[MEME])

    def synthetic_combined_approval(self):
        # Dados unicamente de teste; o único transporte permitido é um Mock.
        events,coverage,tokens=full_case()
        events=[replace(e,wallet=SOL,source='helius',id='helius:'+e.id) for e in events]
        coverage.source='birdeye+helius'
        result=evaluate(SOL,events,coverage,tokens,[],self.config,NOW)
        self.store.put_source_events('helius',events)
        self.store.save_evaluation(result)
        self.assertTrue(result.eligible)
        return result,events

    def test_combined_gate_uses_canonical_events_and_all_eight_checks(self):
        result,events=self.synthetic_combined_approval()
        self.assertEqual(len(result.checks),8)
        transport=Mock(return_value='mock-message-id')
        notifier=Notifier(self.store,self.config,'mock-token','mock-chat',checkpoint=Mock(),transport=transport,clock=lambda:NOW)
        self.assertTrue(eligible_for_delivery(result,self.config,NOW))
        self.assertTrue(notifier.send(result))
        transport.assert_called_once()
        self.assertEqual(transport.call_args.args[:2],('mock-token','mock-chat'))

    def test_combined_approval_fails_if_source_event_changes_after_snapshot(self):
        result,events=self.synthetic_combined_approval()
        extra=replace(events[0],id='new-unknown',tx_hash='new-unknown',kind='unknown',quantity=D('0'))
        self.store.put_source_events('helius',[extra])
        transport=Mock()
        notifier=Notifier(self.store,self.config,'mock-token','mock-chat',checkpoint=Mock(),transport=transport,clock=lambda:NOW)
        self.assertFalse(notifier.send(result));transport.assert_not_called()

    def test_fixture_cannot_hide_inside_combined_source_and_missing_check_blocks(self):
        events,coverage,tokens=full_case();coverage.source='birdeye+helius'
        events=[replace(e,wallet=SOL,source='helius') for e in events]
        events[0]=replace(events[0],source='fixture')
        result=evaluate(SOL,events,coverage,tokens,[],self.config,NOW)
        self.assertFalse(result.eligible)
        self.assertEqual(result.checks[0].status,Status.UNKNOWN)
        result,_=self.synthetic_combined_approval()
        result.checks[-1].status=Status.UNKNOWN
        self.assertFalse(eligible_for_delivery(result,self.config,NOW))

    def test_helius_probe_one_read_no_telegram_and_no_secret_in_output(self):
        requests=[]
        def transport(request):
            requests.append(json.loads(request.data))
            return {'jsonrpc':'2.0','id':1,'result':{'data':[]}}
        def factory(key,store,config):
            return HeliusClient(key,store,config,transport=transport,clock=lambda:NOW,sleep=lambda _:None)
        with patch.dict(os.environ,{'HELIUS_API_KEY':'mock-helius-secret'}),patch('wallets.cli.HeliusClient',side_effect=factory),patch('wallets.cli.Notifier') as notifier:
            code,out,err=self.call('probe','--provider','helius','--wallet',SOL)
        self.assertEqual((code,err),(0,''));self.assertEqual(len(requests),1)
        self.assertEqual(requests[0]['method'],'getTransactionsForAddress')
        self.assertEqual(requests[0]['params'][1]['limit'],1)
        self.assertEqual(requests[0]['params'][1]['transactionDetails'],'signatures')
        self.assertEqual(self.store.db.execute('SELECT credits FROM provider_budgets').fetchone()[0],10)
        notifier.assert_not_called();self.assertNotIn('mock-helius-secret',out+err)

    def test_both_probes_at_most_two_reads_and_independent_quotas(self):
        requests=[]
        def h_transport(request):
            requests.append('helius');return {'jsonrpc':'2.0','id':1,'result':{'data':[]}}
        def b_transport(request):
            requests.append('birdeye');return {'success':True,'data':{'items':[]}}
        h=lambda key,store,config:HeliusClient(key,store,config,transport=h_transport,clock=lambda:NOW,sleep=lambda _:None)
        b=lambda key,store,config:BirdeyeClient(key,store,config,transport=b_transport,clock=lambda:NOW,sleep=lambda _:None)
        with patch.dict(os.environ,{'HELIUS_API_KEY':'mock-h','BIRDEYE_API_KEY':'mock-b'}),patch('wallets.cli.HeliusClient',side_effect=h),patch('wallets.cli.BirdeyeClient',side_effect=b),patch('wallets.cli.Notifier') as notifier:
            code,out,err=self.call('probe','--provider','both','--wallet',SOL)
        self.assertEqual((code,err),(0,''));self.assertEqual(requests,['birdeye','helius'])
        self.assertEqual(self.store.db.execute('SELECT credits FROM budgets').fetchone()[0],30)
        self.assertEqual(self.store.db.execute('SELECT credits FROM provider_budgets').fetchone()[0],10)
        notifier.assert_not_called()

    def test_missing_helius_key_run_is_unknown_and_never_sends(self):
        with patch('wallets.cli.HeliusClient') as client,patch('wallets.cli.Notifier') as notifier:
            code,out,err=self.call('run','--wallet',SOL,'--notify')
        self.assertEqual((code,err),(0,''));client.assert_not_called()
        notifier.return_value.send.assert_not_called()
        report=json.loads((self.output/'report.json').read_text())
        result=report['evaluations'][0]
        self.assertFalse(result['coverage']['complete'])
        self.assertTrue(all(c['status']!='APROVADA' for c in result['checks']))
        self.assertIn('HELIUS_API_KEY',out)

    def test_token_argument_is_not_added_to_manual_wallets(self):
        code,out,err=self.call('run','--token',MEME)
        self.assertEqual((code,err),(0,''))
        report=json.loads((self.output/'report.json').read_text())
        self.assertEqual(report['evaluations'],[])

    def test_partial_pipeline_does_not_promote_any_coverage_flag(self):
        events,coverage,_=full_case();coverage.source='birdeye';coverage.complete=False;coverage.all_tokens=False
        coverage.opening_inventory_known=False;coverage.fees_complete=False
        base=[event()];self.store.put_events(base)
        h_event=event('helius',usd=None)
        history=Mock();history.history.return_value={'transactions':[raw_tx()],'pages_complete':False,'truncated':True,'failures':['quota']}
        normalizer=Mock(return_value={'events':[h_event],'evidence':{'fee_lamports':5000},'limitations':['USD desconhecido']})
        with patch('wallets.helius_normalization.normalize_transaction',normalizer),patch('wallets.pipeline.utcnow',return_value=NOW):
            combined,cov,provider=augment_with_helius(SOL,coverage.start,NOW,base,coverage,{},history,self.store)
        self.assertEqual(combined,self.store.get_analysis_events(SOL,coverage.start,NOW))
        self.assertEqual(cov.source,'birdeye+helius');self.assertFalse(cov.complete)
        self.assertFalse(cov.all_tokens);self.assertFalse(cov.opening_inventory_known);self.assertFalse(cov.fees_complete)
        self.assertTrue(cov.failures);self.assertFalse(provider['helius']['pages_complete'])
        self.assertIsNone(combined[0].fee_usd)

    def test_dual_real_clients_history_and_collector_with_only_mock_transports(self):
        calls=[];row=raw_tx()
        def b_transport(request):
            path=urlsplit(request.full_url).path;calls.append(('birdeye',path))
            if path=='/defi/v3/token/list':return {'success':True,'data':{'items':[{'address':MEME,'recent_listing_time':int((NOW-timedelta(days=2)).timestamp()),'liquidity':200000,'volume_24h_usd':500000,'price_change_24h_percent':20}],'hasNext':False}}
            if path=='/defi/v2/tokens/top_traders':return {'success':True,'data':{'items':[{'owner':SOL,'tags':[]}]}}
            if path=='/trader/txs/seek_by_time':return {'success':True,'data':{'items':[{'tx_hash':'same','owner':SOL,'ins_index':0,'inner_ins_index':0,'block_unix_time':int(AT.timestamp()),'block_number':100,'tx_type':'swap','volume_usd':'2','base':{'address':USDC,'amount':'2000000','decimals':6,'type_swap':'from','price':'1'},'quote':{'address':MEME,'amount':'1000000','decimals':6,'type_swap':'to','price':'2'}}],'hasNext':False}}
            if path=='/wallet/v2/transfer':return {'success':True,'data':[]}
            if path=='/wallet/v2/pnl/details':return {'success':True,'data':{'tokens':[],'summary':{'unique_tokens':0}}}
            if path=='/wallet/v2/current-net-worth':return {'success':True,'data':{'items':[],'total':0,'current_timestamp':int(NOW.timestamp())}}
            if path=='/blockchain/v1/transaction/detail':return {'success':True,'data':{'block_time':int(AT.timestamp()),'fee':5000,'priority_fee':0,'status':{}}}
            raise AssertionError(path)
        def h_transport(request):
            query=json.loads(request.data);calls.append(('helius',query['method']))
            return {'jsonrpc':'2.0','id':1,'result':{'data':[row],'paginationToken':None}}
        h=lambda key,store,config:HeliusClient(key,store,config,transport=h_transport,clock=lambda:NOW,sleep=lambda _:None)
        b=lambda key,store,config:BirdeyeClient(key,store,config,transport=b_transport,clock=lambda:NOW,sleep=lambda _:None)
        with patch.dict(os.environ,{'HELIUS_API_KEY':'mock-h','BIRDEYE_API_KEY':'mock-b'}),patch('wallets.cli.HeliusClient',side_effect=h),patch('wallets.cli.BirdeyeClient',side_effect=b),patch('wallets.cli.Collector',side_effect=lambda client,store,config:Collector(client,store,config,clock=lambda:NOW)),patch('wallets.cli.HeliusHistory',side_effect=lambda client,store,config:HeliusHistory(client,store,config,clock=lambda:NOW)),patch('wallets.pipeline.utcnow',return_value=NOW),patch('wallets.cli.Notifier') as notifier:
            code,out,err=self.call('run','--notify')
        self.assertEqual((code,err),(0,''));self.assertTrue(any(source=='helius' for source,_ in calls))
        self.assertTrue(any(source=='birdeye' for source,_ in calls));notifier.return_value.send.assert_not_called()
        report=json.loads((self.output/'report.json').read_text());self.assertEqual(len(report['evaluations']),1)
        result=report['evaluations'][0];self.assertEqual(result['source'],'birdeye+helius')
        self.assertFalse(result['coverage']['complete']);self.assertFalse(result['coverage']['fees_complete'])
        self.assertFalse(any(c['status']=='APROVADA' for c in result['checks']))
        saved=self.store.latest_evaluations()[0]
        replay=cli.reevaluate(saved,self.store,self.config,NOW)
        self.assertEqual(replay.metrics['_events_hash'],saved.metrics['_events_hash'])
        self.assertFalse(replay.eligible)
        self.assertNotIn('mock-h',json.dumps(report));self.assertNotIn('mock-b',json.dumps(report))
