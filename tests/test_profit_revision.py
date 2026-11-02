from copy import deepcopy
from datetime import timedelta
from decimal import Decimal as D
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from io import BytesIO
from test_sol_accounting import swap,WALLET,START,END,MINT
from test_helius_normalization import encoded
from wallets.helius_normalization import PUMP_PROGRAM,PUMP_FEE_PROGRAM,PUMP_GET_FEES
from wallets.sol_accounting import analyze_sol,normalize_sol_receipt
from wallets.storage import Store
from wallets.profit_evidence import persist,replay,schema
from wallets.profit_queue import enqueue,select,checked
from wallets.helius import HeliusClient,HeliusError,BudgetExceeded
from wallets.helius_parsed import parse_transactions
from wallets.profit_pipeline import _provider_order


class RevisionTests(unittest.TestCase):
    def fee_swap(self):
        row=swap()
        msg=row['transaction']['message'];meta=row['meta']
        for key in (PUMP_FEE_PROGRAM,'fee-config'):
            msg['accountKeys'].append(key);meta['preBalances'].append(0);meta['postBalances'].append(0)
        msg['instructions'][0]['accounts'] += [PUMP_FEE_PROGRAM,'fee-config']
        meta['innerInstructions']=[{'index':0,'instructions':[{'programId':PUMP_FEE_PROGRAM,
            'accounts':['fee-config',PUMP_PROGRAM],'data':encoded(PUMP_GET_FEES+b'\x01'+b'\0'*24+b'\x00')}]}]
        return row

    def test_official_fee_cpi_reconciles_but_arbitrary_fee_calls_do_not(self):
        row=self.fee_swap();self.assertEqual(normalize_sol_receipt(row,WALLET)['status'],'trade')
        bad=deepcopy(row);bad['meta']['innerInstructions'][0]['instructions'][0]['accounts'][1]='fee-config'
        self.assertNotEqual(normalize_sol_receipt(bad,WALLET)['status'],'trade')
        bad=deepcopy(row);bad['meta']['innerInstructions'][0]['instructions'][0]['data']=encoded(b'x'*34)
        self.assertNotEqual(normalize_sol_receipt(bad,WALLET)['status'],'trade')
        bad=deepcopy(row);ins=bad['meta']['innerInstructions'][0]['instructions'].pop();bad['transaction']['message']['instructions'].append(ins)
        self.assertNotEqual(normalize_sol_receipt(bad,WALLET)['status'],'trade')

    def test_parsed_swap_requires_matching_rpc_instruction_and_wallet(self):
        row=swap();msg=row['transaction']['message'];ins=msg['instructions'][0]
        program='fixture-router';ins['programId']=program
        msg['accountKeys']=[program if k==PUMP_PROGRAM else k for k in msg['accountKeys']]
        proof={'signature':row['transaction']['signatures'][0],'parserStatus':'OK','parsed':{
            'slot':row['slot'],'blockTime':row['blockTime'],'fee':row['meta']['fee'],'feePayer':WALLET,'transactionStatus':'OK',
            'summary':{'type':'swap','parsedData':{'type':'swap','input_mint':'So11111111111111111111111111111111111111112','output_mint':MINT}},
            'instructions':[{'instructionIndex':0,'innerInstructionIndex':None,'programId':program,
                'rawAccounts':ins['accounts'],'rawData':ins['data'],'summary':{'type':'swap'},
                'decoded':{'accounts':[{'name':'user','pubkey':WALLET}]}}]}}
        sig=proof['signature']
        self.assertNotEqual(normalize_sol_receipt(row,WALLET)['status'],'trade')
        result=normalize_sol_receipt(row,WALLET,parsed_events={sig:proof})
        self.assertEqual(result['status'],'trade');self.assertEqual(result['legs'][0]['value_sol'],D('.01'))
        for field,value in [('fee',100),('blockTime',row['blockTime']+1),('feePayer','another')]:
            bad=deepcopy(proof);bad['parsed'][field]=value
            self.assertNotEqual(normalize_sol_receipt(row,WALLET,parsed_events={sig:bad})['status'],'trade')
        bad=deepcopy(proof);bad['parsed']['instructions'][0]['rawData']='invalid'
        self.assertNotEqual(normalize_sol_receipt(row,WALLET,parsed_events={sig:bad})['status'],'trade')

    def test_opening_cost_reconciled_without_counting_old_profit(self):
        prior=[swap(quantity='2',sol='2',at=START-timedelta(days=2),signature='buy-old'),
               swap('sell',quantity='1',before='2',sol='5',at=START-timedelta(days=1),signature='sell-old')]
        current=[swap('sell',quantity='1',sol='2',at=END-timedelta(days=1),signature='sell-now')]
        without=analyze_sol(current,WALLET,START,END)
        self.assertIsNone(without['metrics']['realized_sol'])
        result=analyze_sol(current,WALLET,START,END,opening_transactions=prior,opening_start=START-timedelta(days=7))
        self.assertEqual(result['metrics']['realized_sol'],D(1))
        self.assertEqual(result['metrics']['trades_count'],1)
        self.assertEqual(result['metrics']['tokens_count'],1)

    def test_opening_unknown_or_future_does_not_create_free_inventory(self):
        current=[swap('sell',sol='2')]
        prior=[swap('sell',sol='1',at=START-timedelta(days=1))]
        result=analyze_sol(current,WALLET,START,END,opening_transactions=prior,opening_start=START-timedelta(days=2))
        self.assertEqual(result['metrics']['known_tokens_count'],0)
        with self.assertRaises(ValueError):analyze_sol(current,WALLET,START,END,opening_transactions=prior,opening_start=END)

    def test_replay_preserves_unknown_origin_and_versions_without_overwriting(self):
        with TemporaryDirectory() as d,Store(Path(d)/'s.db') as store:
            raw=swap();rec=normalize_sol_receipt(raw,WALLET)
            unknown=dict(rec,status='unknown',classification_complete=False)
            persist(store,WALLET,[unknown],[raw],START,END)
            persist(store,WALLET,[rec],[raw],START,END)
            self.assertEqual(replay(store,WALLET,START,END),[raw])
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM profit_interpretations').fetchone()[0],2)
            changed=deepcopy(raw);changed['meta']['fee']=9
            with self.assertRaises(ValueError):persist(store,WALLET,[normalize_sol_receipt(changed,WALLET)],[changed],START,END)
            self.assertEqual(replay(store,WALLET,START,END),[raw])
            store.db.execute("UPDATE profit_raw_evidence SET payload=?",(b'bad',));store.db.commit()
            with self.assertRaises(ValueError):replay(store,WALLET,START,END)

    def test_queue_rotates_new_candidates_and_honours_private_manual_request(self):
        with TemporaryDirectory() as d,Store(Path(d)/'s.db') as store:
            candidates=[{'wallet':c*44,'origins':[]} for c in 'ABCD']
            one=select(store,candidates,END,2)
            for row in one:checked(store,row,END)
            two=select(store,candidates,END+timedelta(hours=1),2)
            self.assertFalse({r['wallet'] for r in one}&{r['wallet'] for r in two})
            enqueue(store,[{'wallet':'E'*44,'origins':[]}],END,manual=True)
            self.assertEqual(select(store,[],END,1)[0]['wallet'],'E'*44)

    def test_deep_priority_prefers_sufficient_sample_over_single_token_origin(self):
        a={'wallet':'a','origins':['x','y'],'prefilter7':{'passed':True,'tokens_count':7,'pnl_usd':999}}
        b={'wallet':'b','origins':[],'prefilter7':{'passed':True,'tokens_count':45,'pnl_usd':1}}
        self.assertEqual(sorted([a,b],key=_provider_order)[0]['wallet'],'b')

    def test_parsed_client_budget_identity_and_safe_http_errors(self):
        with TemporaryDirectory() as d,Store(Path(d)/'s.db') as store:
            calls=[]
            def transport(req):
                calls.append(json.loads(req.data));return [{'signature':'sig','parserStatus':'ERROR','parserError':{'code':'UNKNOWN'}}]
            client=HeliusClient('private-key',store,{'max_credits_per_run':10},transport=transport,sleep=lambda x:None)
            self.assertEqual(parse_transactions(client,['sig'])[0]['parserStatus'],'ERROR')
            self.assertEqual(client.credits,10)
            self.assertEqual(calls[0]['rawTransaction'],'jsonParsed')
            with self.assertRaises(BudgetExceeded):parse_transactions(client,['sig'])
            def bad(req):return [{'signature':'wrong','parserStatus':'OK'}]
            client=HeliusClient('private-key',store,{},transport=bad)
            with self.assertRaises(HeliusError):parse_transactions(client,['sig'])
            def denied(req):raise HTTPError(req.full_url,403,'private-key',{},BytesIO(b'private-key'))
            client=HeliusClient('private-key',store,{},transport=denied)
            with self.assertRaises(HeliusError) as exc:parse_transactions(client,['sig'])
            self.assertNotIn('private-key',str(exc.exception));self.assertTrue(client._halted)

if __name__=='__main__':unittest.main()
