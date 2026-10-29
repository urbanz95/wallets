"""Percursos de utilização offline: nenhuma credencial ou chamada real de rede."""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import StringIO, BytesIO
import csv
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit
from urllib.error import HTTPError

from wallets import cli
from wallets.birdeye import BirdeyeClient, BudgetExceeded, BirdeyeError
from wallets.checks import evaluate
from wallets.collection import Collector
from wallets.models import SOL
from wallets.reporting import write_reports
from wallets.storage import Store
from test_checks import full_case

NOW = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
ROOT = Path(__file__).parents[1]
CONFIG_PATH = ROOT/'config.toml'
MEME = 'B'*44
USDC = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'


class CLITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.db = self.directory/'state.sqlite'
        self.output = self.directory/'reports'
        # Mesmo se um mock estiver incompleto, nenhuma rede é permitida.
        for target in ('urllib.request.OpenerDirector.open', 'urllib.request.urlopen', 'socket.socket.connect'):
            no_network = patch(target, side_effect=AssertionError('Rede proibida neste teste'))
            no_network.start(); self.addCleanup(no_network.stop)
        clean = patch.dict(os.environ, {'BIRDEYE_API_KEY':'', 'TELEGRAM_BOT_TOKEN':'',
            'TELEGRAM_CHAT_ID':'', 'GITHUB_ACTIONS':''})
        clean.start(); self.addCleanup(clean.stop)
        clock = patch('wallets.cli.utcnow', return_value=NOW)
        clock.start(); self.addCleanup(clock.stop)

    def call(self, command, *extra, config=CONFIG_PATH):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main([command, '--config', str(config), '--db', str(self.db),
                             '--output', str(self.output), *extra])
        return code, out.getvalue(), err.getvalue()

    def report(self):
        return json.loads((self.output/'report.json').read_text(encoding='utf-8'))

    def test_demo_isolated_from_production_state_network_and_notifications(self):
        with patch.dict(os.environ, {'BIRDEYE_API_KEY':'mock-secret-never-publish',
             'TELEGRAM_BOT_TOKEN':'mock-telegram-never-publish', 'TELEGRAM_CHAT_ID':'mock-private-id'}), \
             patch('wallets.cli.BirdeyeClient') as client, patch('wallets.cli.Notifier') as notifier:
            code, out, err = self.call('demo')
        self.assertEqual((code, err), (0, ''))
        self.assertFalse(self.db.exists())
        client.assert_not_called(); notifier.assert_not_called()
        report = self.report()
        self.assertEqual(len(report['evaluations']), 2)
        self.assertTrue(all(e['source']=='fixture' for e in report['evaluations']))
        a, b = report['evaluations']
        self.assertEqual(a['metrics']['without_best'], '-2000')
        self.assertEqual(b['metrics']['without_best'], '3200')
        self.assertIn('fictícia', out)
        text = ''.join(p.read_text(encoding='utf-8') for p in self.output.iterdir())
        for secret in ('mock-secret-never-publish', 'mock-telegram-never-publish', 'mock-private-id'):
            self.assertNotIn(secret, text+out+err)

    def test_run_without_key_produces_unknown_manual_wallet_and_three_reports(self):
        with patch('wallets.cli.BirdeyeClient') as client, patch('wallets.cli.Notifier') as notifier:
            code, out, err = self.call('run', '--wallet', SOL)
        self.assertEqual((code, err), (0, ''))
        client.assert_not_called(); notifier.assert_not_called()
        self.assertTrue(all((self.output/name).is_file() for name in ('report.html','report.csv','report.json')))
        report = self.report(); self.assertEqual(len(report['evaluations']), 1)
        result = report['evaluations'][0]
        self.assertEqual(result['wallet'], SOL)
        self.assertFalse(result['coverage']['complete'])
        self.assertTrue(result['coverage']['failures'])
        self.assertTrue(all(c['status']!='APROVADA' for c in result['checks']))
        self.assertIn('Sem consultas', out)
        with (self.output/'report.csv').open(newline='',encoding='utf-8') as handle:
            row = list(csv.DictReader(handle))[0]
        self.assertEqual(row['wallet'], SOL)
        self.assertEqual(row['eligible_checks'], 'False')
        self.assertEqual(report['timezone_display'], 'Europe/Lisbon')
        with Store(self.db) as store:self.assertFalse(store.latest_evaluations()[0].eligible)

    def test_repeated_run_reopens_state_keeps_snapshots_and_updates_latest(self):
        self.assertEqual(self.call('run','--wallet',SOL)[0], 0)
        later = NOW+timedelta(days=1)
        with patch('wallets.cli.utcnow', return_value=later):
            self.assertEqual(self.call('run')[0], 0)
        with Store(self.db) as store:
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM evaluations').fetchone()[0], 2)
            latest = store.latest_evaluations()[0]
            self.assertEqual(latest.evaluated_at, later)
            self.assertFalse(latest.eligible)

    def test_empty_notify_never_attempts_transport(self):
        with patch('wallets.cli.Notifier') as factory:
            code, out, err = self.call('notify')
        self.assertEqual((code,err),(0,''))
        factory.return_value.send.assert_not_called()
        self.assertIn('Envios elegíveis concluídos: 0',out)

    def test_fixture_snapshot_cannot_be_sent_via_notify_command(self):
        events, coverage, results = full_case()
        events = [replace(e,wallet=SOL) for e in events]
        evaluation = evaluate(SOL,events,coverage,results,[],cli.load_config(CONFIG_PATH),now=NOW)
        self.assertTrue(evaluation.eligible)
        with Store(self.db) as store:
            store.put_events(events); store.save_evaluation(evaluation)
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN':'mock-token', 'TELEGRAM_CHAT_ID':'mock-chat'}), \
             patch('wallets.cli.Notifier') as notifier:
            code, out, err = self.call('notify')
        self.assertEqual((code,err),(0,''))
        notifier.return_value.send.assert_not_called()
        self.assertIn('concluídos: 0',out)

    def test_run_notify_without_data_never_sends_errors_or_empty_results(self):
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN':'mock-token', 'TELEGRAM_CHAT_ID':'mock-chat'}), \
             patch('wallets.cli.Notifier') as notifier:
            code, out, err = self.call('run','--wallet',SOL,'--notify')
        self.assertEqual((code,err),(0,''))
        notifier.return_value.send.assert_not_called()
        self.assertIn('concluídos: 0',out)

    def test_missing_key_probe_does_not_even_create_state_or_client(self):
        with patch('wallets.cli.BirdeyeClient') as client:
            code, out, err = self.call('probe')
        self.assertEqual((code,err),(0,''))
        client.assert_not_called(); self.assertFalse(self.db.exists())
        self.assertIn('Nenhum pedido efetuado',out)

    def test_probe_makes_only_one_mocked_free_endpoint_request(self):
        with patch.dict(os.environ, {'BIRDEYE_API_KEY':'mock-private-key'}), \
             patch('wallets.cli.BirdeyeClient') as factory:
            factory.return_value.request.return_value = {'success':True,'data':{'items':[]},'pagination':{'limit':1,'offset':0,'total':0}}
            code, out, err = self.call('probe','--wallet',SOL)
        self.assertEqual((code,err),(0,''))
        factory.return_value.request.assert_called_once_with('GET','/wallet/v2/current-net-worth',
            {'wallet':SOL,'flags':'include_low_liquidity','sort_type':'desc','limit':1,'offset':0},cache=False)
        config = factory.call_args.args[2]
        self.assertEqual((config['max_requests_per_run'],config['max_retries'],config['max_credits_per_run']),(1,0,30))
        self.assertNotIn('mock-private-key',out+err)

    def test_probe_quota_error_is_safe_and_does_not_send(self):
        with patch.dict(os.environ, {'BIRDEYE_API_KEY':'mock-private-key'}), \
             patch('wallets.cli.BirdeyeClient') as factory, patch('wallets.cli.Notifier') as notifier:
            factory.return_value.request.side_effect = BudgetExceeded('Quota local esgotada')
            code, out, err = self.call('probe','--wallet',SOL)
        self.assertEqual(code,2)
        self.assertIn('Quota local esgotada',err)
        self.assertNotIn('mock-private-key',out+err)
        notifier.assert_not_called()

    def test_invalid_wallet_is_rejected_before_state_or_api(self):
        with patch('wallets.cli.BirdeyeClient') as client:
            code, out, err = self.call('run','--wallet','not-an-address')
        self.assertEqual(code,2); self.assertIn('Operação interrompida',err)
        self.assertFalse(self.db.exists()); client.assert_not_called()
        self.assertNotIn('Traceback',out+err)

    def test_invalid_config_types_or_paid_sized_quota_fail_safely(self):
        original = CONFIG_PATH.read_text(encoding='utf-8')
        cases = [original.replace('min_tokens = 20','min_tokens = "20"'),
                 original.replace('monthly_credit_ceiling = 28000','monthly_credit_ceiling = 40000')]
        for index, text in enumerate(cases):
            config = self.directory/f'bad-{index}.toml'; config.write_text(text,encoding='utf-8')
            with self.subTest(index=index), patch('wallets.cli.BirdeyeClient') as client:
                code, out, err = self.call('run','--wallet',SOL,config=config)
                self.assertEqual(code,2); client.assert_not_called()
                self.assertNotIn('Traceback',out+err)

    def test_corrupted_state_is_safe_failure_without_traceback(self):
        self.db.write_bytes(b'this is not a SQLite database')
        code, out, err = self.call('notify')
        self.assertEqual(code,2)
        self.assertIn('Operação interrompida',err)
        self.assertNotIn('Traceback',out+err)

    def test_current_marks_never_uses_future_unknown_or_negative_prices(self):
        provider = {'holdings_time':int(NOW.timestamp()),'holdings':[
            {'address':MEME,'price':0}, {'address':USDC,'price':None,'priceUsd':'1.25'},
            {'address':SOL,'price':-1}, {'address':'invalid','price':3}]}
        marks = cli.current_marks(provider,NOW)
        self.assertEqual(marks[MEME]['price_usd'],Decimal(0))
        self.assertEqual(marks[USDC]['price_usd'],Decimal('1.25'))
        self.assertNotIn(SOL,marks)
        self.assertEqual(cli.current_marks(dict(provider,holdings_time=int((NOW+timedelta(seconds=1)).timestamp())),NOW),{})
        self.assertEqual(cli.current_marks(dict(provider,holdings_time=None),NOW),{})

    def test_malformed_holdings_rows_stay_unknown_instead_of_crashing(self):
        for rows in ([None], ['malformed'], None):
            with self.subTest(rows=rows):
                self.assertEqual(cli.current_marks({'holdings_time':int(NOW.timestamp()),'holdings':rows},NOW),{})

    def test_report_escapes_untrusted_values_and_does_not_label_fixture_real(self):
        events, coverage, results = full_case()
        evaluation = evaluate('<script>alert(1)</script>',events,coverage,results,[],
                              cli.load_config(CONFIG_PATH),now=NOW)
        write_reports([evaluation],self.output,notes=['<img src=x onerror=alert(1)>'])
        html = (self.output/'report.html').read_text(encoding='utf-8')
        self.assertNotIn('<script>alert(1)</script>',html)
        self.assertNotIn('<img src=x',html)
        self.assertIn('&lt;script&gt;',html)
        self.assertIn('DADOS FICTÍCIOS',html)

    def test_actions_run_requires_durable_checkpoint_before_api_or_state(self):
        with patch.dict(os.environ, {'GITHUB_ACTIONS':'true','BIRDEYE_API_KEY':'mock-key'}), \
             patch('wallets.cli.BirdeyeClient') as client:
            code, out, err = self.call('run','--wallet',SOL)
        self.assertEqual(code,2)
        client.assert_not_called(); self.assertFalse(self.db.exists())
        self.assertNotIn('mock-key',out+err)

    def test_collection_error_replaces_prior_pass_with_indeterminate_snapshot(self):
        events, coverage, results = full_case()
        events = [replace(e,wallet=SOL) for e in events]
        initial = evaluate(SOL,events,coverage,results,[],cli.load_config(CONFIG_PATH),now=NOW)
        self.assertTrue(initial.eligible)
        with Store(self.db) as store:
            store.put_events(events); store.save_evaluation(initial)
        fake_client = Mock(requests=0,credits=0)
        fake_collector = Mock(client=fake_client,limitations=[])
        fake_collector.discover.return_value=[]
        fake_collector.collect.side_effect=BirdeyeError('Falha de recolha simulada')
        with patch.dict(os.environ, {'BIRDEYE_API_KEY':'mock-key'}), \
             patch('wallets.cli.BirdeyeClient',return_value=fake_client), \
             patch('wallets.cli.Collector',return_value=fake_collector):
            code, out, err = self.call('run','--wallet',SOL)
        self.assertEqual((code,err),(0,''))
        with Store(self.db) as store:
            latest=store.latest_evaluations()[0]
            self.assertFalse(latest.eligible)
            self.assertNotEqual(latest.id,initial.id)
            self.assertTrue(store.get_evaluation(initial.id).eligible)
            self.assertTrue(latest.coverage.failures)

    def test_mocked_full_birdeye_flow_records_partial_data_and_never_notifies(self):
        calls = []
        trade_time = int((NOW-timedelta(days=1)).timestamp())
        def transport(request):
            parsed = urlsplit(request.full_url); calls.append(parsed.path)
            query = parse_qs(parsed.query)
            if parsed.path=='/defi/v3/token/list':
                return {'success':True,'data':{'items':[{'address':MEME,'recent_listing_time':int((NOW-timedelta(days=2)).timestamp()),
                    'liquidity':200000,'volume_24h_usd':500000,'price_change_24h_percent':20}],'hasNext':False}}
            if parsed.path=='/defi/v2/tokens/top_traders':
                return {'success':True,'data':{'items':[{'owner':SOL,'tags':['sniper']}]}}
            if parsed.path=='/trader/txs/seek_by_time':
                return {'success':True,'data':{'items':[{'tx_hash':'mock-tx','owner':SOL,'ins_index':0,'inner_ins_index':0,
                    'block_unix_time':trade_time,'block_number':10,'tx_type':'swap','volume_usd':'100',
                    'base':{'address':USDC,'amount':'100000000','decimals':6,'type_swap':'from','price':'1'},
                    'quote':{'address':MEME,'amount':'2000000','decimals':6,'type_swap':'to','price':'50'}}],'hasNext':False}}
            if parsed.path=='/wallet/v2/transfer':return {'success':True,'data':[]}
            if parsed.path=='/wallet/v2/pnl/details':return {'success':True,'data':{'tokens':[],'summary':{'unique_tokens':0}}}
            if parsed.path=='/wallet/v2/current-net-worth':
                return {'success':True,'data':{'items':[{'address':MEME,'price':40,'amount':2}], 'current_timestamp':int(NOW.timestamp())},'pagination':{'limit':100,'offset':0,'total':1}}
            if parsed.path=='/blockchain/v1/transaction/detail':
                self.assertEqual(query['signature'],['mock-tx'])
                return {'success':True,'data':{'block_time':trade_time,'fee':5000,'priority_fee':1000,'status':{}}}
            raise AssertionError('Endpoint inesperado: '+parsed.path)
        clients = []
        def client_factory(key,store,config):
            client = BirdeyeClient(key,store,config,transport=transport,clock=lambda:NOW,sleep=lambda _:None)
            clients.append(client); return client
        with patch.dict(os.environ, {'BIRDEYE_API_KEY':'mock-key-never-publish','TELEGRAM_BOT_TOKEN':'mock-token','TELEGRAM_CHAT_ID':'mock-chat'}), \
             patch('wallets.cli.BirdeyeClient',side_effect=client_factory), \
             patch('wallets.cli.Collector', side_effect=lambda client,store,config:Collector(client,store,config,clock=lambda:NOW)), \
             patch('wallets.cli.Notifier') as notifier:
            code, out, err = self.call('run','--notify')
        self.assertEqual((code,err),(0,''))
        self.assertIn('/defi/v3/token/list',calls)
        self.assertIn('/trader/txs/seek_by_time',calls)
        self.assertGreater(clients[0].requests,0)
        notifier.return_value.send.assert_not_called()
        report=self.report(); self.assertEqual(len(report['evaluations']),1)
        result=report['evaluations'][0]
        self.assertFalse(result['coverage']['complete'])
        self.assertFalse(result['coverage']['fees_complete'])
        self.assertTrue(all(c['status']!='APROVADA' for c in result['checks']))
        self.assertEqual(result['discovery_tokens'],[MEME])
        self.assertNotIn('mock-key-never-publish',json.dumps(report)+out+err)

    def test_contract_probe_reserves_all_quota_before_network_and_only_reports_structure(self):
        order=[];calls=[]
        def checkpoint():
            order.append('save')
            with Store(self.db) as state:
                self.assertEqual(state.db.execute('SELECT credits FROM budgets').fetchone()[0],92)
                self.assertEqual(state.latest_evaluations(),[])
        def transport(request):
            self.assertTrue(order)
            order.append('request');calls.append(request)
            path=urlsplit(request.full_url).path
            if path=='/wallet/v2/transfer':return {'success':True,'data':[{'token_address':'PRIVATE-TOKEN','tx_hash':'PRIVATE-TX'}]}
            if path=='/wallet/v2/pnl/details':return {'data':{'tokens':[{'address':'PRIVATE-TOKEN','pnl':{'secret':'PRIVATE-PNL'}}]}}
            return {'success':True,'data':{'items':[{'owner':'PRIVATE-WALLET','address':'PRIVATE-TOKEN'}]},
                    'pagination':{'limit':1,'offset':0,'total':1},'PRIVATE-FIELD':'PRIVATE-VALUE'}
        clients=[]
        def factory(key,store,config):
            client=BirdeyeClient(key,store,config,transport=transport,clock=lambda:NOW,sleep=lambda _:None)
            clients.append(client);return client
        with patch.dict(os.environ,{'BIRDEYE_API_KEY':'PRIVATE-KEY'}), \
             patch('wallets.cli.BirdeyeClient',side_effect=factory), \
             patch('wallets.cli.checkpoint_for',return_value=checkpoint), \
             patch('wallets.cli.Notifier') as notifier:
            code,out,err=self.call('probe','--contracts','--wallet',SOL,'--checkpoint-command','mock-save')
        self.assertEqual((code,err),(0,''))
        self.assertEqual(order,['save','request','save','request','save','request','save','request','save'])
        self.assertEqual((clients[0].requests,clients[0].credits),(4,82))
        self.assertNotIn('PRIVATE-',out+err)
        self.assertNotIn(SOL,out+err)
        self.assertIn('pagination_fields',out)
        self.assertIn('tokens_count',out)
        self.assertIn('data_count',out)
        self.assertFalse(self.output.exists())
        notifier.assert_not_called()
        self.assertTrue(all(client.config['max_retries']==0 for client in clients))
        for request in calls:
            body=json.loads(request.data) if request.data else parse_qs(urlsplit(request.full_url).query)
            self.assertIn(body['limit'],(1,['1']))
            if urlsplit(request.full_url).path=='/trader/txs/seek_by_time':
                self.assertIn('before_time',body)
                self.assertNotIn('after_time',body)
            if urlsplit(request.full_url).path=='/wallet/v2/pnl/details':
                self.assertEqual((body['pnl_method'],body['position_scope']),('wac','duration_only'))
        with Store(self.db) as state:
            self.assertEqual(state.db.execute('SELECT COUNT(*) FROM cache').fetchone()[0],0)
            self.assertEqual(state.db.execute('SELECT COUNT(*) FROM notifications').fetchone()[0],0)

    def test_contract_probe_continues_after_parameter_failure_without_approving_or_sending(self):
        requests=[]
        def transport(request):
            requests.append(request)
            path=urlsplit(request.full_url).path
            if path=='/trader/txs/seek_by_time':
                raise HTTPError(request.full_url,422,'PRIVATE',{},BytesIO(b'{"detail":[{"loc":["query","limit"],"type":"less_than_equal","ctx":{"le":50},"input":"PRIVATE"}]}'))
            if path=='/wallet/v2/transfer':return {'success':True,'data':[]}
            if path=='/wallet/v2/pnl/details':return {'data':{'tokens':[]}}
            return {'success':True,'data':{'items':[]},'pagination':{'total':0}}
        def factory(key,store,config):return BirdeyeClient(key,store,config,transport=transport,clock=lambda:NOW,sleep=lambda _:None)
        with patch.dict(os.environ,{'BIRDEYE_API_KEY':'PRIVATE-KEY'}), \
             patch('wallets.cli.BirdeyeClient',side_effect=factory),patch('wallets.cli.Notifier') as notifier:
            code,out,err=self.call('probe','--contracts','--wallet',SOL)
        self.assertEqual(code,2)
        self.assertEqual(len(requests),4)
        self.assertIn('less_than_equal',err)
        self.assertNotIn('PRIVATE',out+err)
        notifier.assert_not_called()
        with Store(self.db) as state:self.assertEqual(state.latest_evaluations(),[])

    def test_contract_probe_requires_known_shapes_instead_of_empty_success(self):
        results=[{'success':True,'data':{'PRIVATE-FIELD':'PRIVATE-VALUE'}},
                 {'success':True,'data':None},
                 {'data':{'tokens':None}},
                 {'success':True,'data':{'items':[]}}]
        with patch.dict(os.environ,{'BIRDEYE_API_KEY':'PRIVATE-KEY'}), \
             patch('wallets.cli.BirdeyeClient') as factory:
            factory.return_value.request.side_effect=results
            code,out,err=self.call('probe','--contracts','--wallet',SOL)
        self.assertEqual(code,2)
        self.assertEqual(factory.return_value.request.call_count,4)
        self.assertEqual(out.count('Estrutura obrigatória'),4)
        self.assertNotIn('PRIVATE-',out+err)

    def test_contract_probe_rejects_other_providers_and_missing_key_before_state(self):
        for provider in ('helius','both'):
            with self.subTest(provider=provider),patch('wallets.cli.BirdeyeClient') as birdeye,patch('wallets.cli.HeliusClient') as helius:
                code,out,err=self.call('probe','--contracts','--provider',provider,'--wallet',SOL)
                self.assertEqual(code,2);birdeye.assert_not_called();helius.assert_not_called()
                self.assertFalse(self.db.exists())
        with patch('wallets.cli.BirdeyeClient') as client:
            code,out,err=self.call('probe','--contracts','--wallet',SOL)
        self.assertEqual(code,2);client.assert_not_called();self.assertFalse(self.db.exists())

    def test_contract_probe_budget_failure_preserves_quota_and_never_calls_api(self):
        with Store(self.db) as state:state.reserve_budget('2026-10',27950,28000)
        with patch.dict(os.environ,{'BIRDEYE_API_KEY':'PRIVATE-KEY'}), \
             patch('wallets.cli.BirdeyeClient') as client, \
             patch('wallets.cli.checkpoint_for',return_value=Mock()) as checkpoint:
            code,out,err=self.call('probe','--contracts','--wallet',SOL,'--checkpoint-command','mock-save')
        self.assertEqual(code,2);client.assert_not_called();checkpoint.return_value.assert_not_called()
        with Store(self.db) as state:
            self.assertEqual(state.db.execute('SELECT credits FROM budgets').fetchone()[0],27950)
            self.assertEqual(state.latest_evaluations(),[])

    def test_contract_probe_checkpoint_failure_blocks_network(self):
        import subprocess
        with patch.dict(os.environ,{'BIRDEYE_API_KEY':'PRIVATE-KEY'}), \
             patch('wallets.cli.BirdeyeClient') as client, \
             patch('wallets.cli.checkpoint_for',return_value=Mock(side_effect=subprocess.CalledProcessError(1,['mock-save']))):
            code,out,err=self.call('probe','--contracts','--wallet',SOL,'--checkpoint-command','mock-save')
        self.assertEqual(code,2);client.assert_not_called()
        self.assertNotIn('PRIVATE-',out+err)


class TransferContinuationProbeTests(unittest.TestCase):
    setUp = CLITests.setUp
    call = CLITests.call
    def test_probe_checks_cursor_wallet_without_reusing_time_filters(self):
        bodies=[]
        def transport(request):
            path=urlsplit(request.full_url).path
            if path=='/wallet/v2/transfer':
                body=json.loads(request.data);bodies.append(body)
                if 'cursor' not in body:
                    return {'success':True,'data':[{}],'next_cursor':'OPAQUE-PRIVATE-CURSOR'}
                self.assertEqual(body,{'wallet':SOL,'cursor':'OPAQUE-PRIVATE-CURSOR'})
                return {'success':True,'data':[{}],'next_cursor':'ANOTHER-PRIVATE-CURSOR'}
            if path=='/wallet/v2/pnl/details':return {'data':{'tokens':[]}}
            return {'success':True,'data':{'items':[]},'pagination':{'total':0}}
        clients=[]
        def factory(key,store,config):
            client=BirdeyeClient(key,store,config,transport=transport,clock=lambda:NOW,sleep=lambda _:None)
            clients.append(client);return client
        with patch.dict(os.environ,{'BIRDEYE_API_KEY':'PRIVATE-KEY'}),patch('wallets.cli.BirdeyeClient',side_effect=factory),patch('wallets.cli.Notifier') as notifier:
            code,out,err=self.call('probe','--contracts','--wallet',SOL)
        self.assertEqual((code,err),(0,''))
        self.assertEqual((clients[0].requests,clients[0].credits),(5,92))
        self.assertEqual(len(bodies),2)
        self.assertNotIn('PRIVATE-',out+err)
        notifier.assert_not_called()

    def test_invalid_false_cursor_is_not_reported_as_confirmed_contract(self):
        def transport(request):
            path=urlsplit(request.full_url).path
            if path=='/wallet/v2/transfer':return {'success':True,'data':[],'next_cursor':False}
            if path=='/wallet/v2/pnl/details':return {'data':{'tokens':[]}}
            return {'success':True,'data':{'items':[]},'pagination':{'total':0}}
        def factory(key,store,config):return BirdeyeClient(key,store,config,transport=transport,clock=lambda:NOW,sleep=lambda _:None)
        with patch.dict(os.environ,{'BIRDEYE_API_KEY':'PRIVATE-KEY'}),patch('wallets.cli.BirdeyeClient',side_effect=factory):
            code,out,err=self.call('probe','--contracts','--wallet',SOL)
        self.assertEqual(code,2)
        self.assertIn('Cursor de transferências inválido',out)
