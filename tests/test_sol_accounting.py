"""Fixtures de swaps, FIFO e cenários SOL; nenhuma rede ou transação real."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import json
import unittest

from test_helius_normalization import case, balance, WALLET, MINT, BASE_ATA, QUOTE_ATA, STAMP, USDC
from wallets.helius_normalization import PUMP_AMM_PROGRAM, SYSTEM_PROGRAM, TOKEN_PROGRAM, ATA_PROGRAM
from wallets.models import SOL, dumps
from wallets.sol_accounting import analyze_sol, normalize_sol_receipt

D=Decimal
END=STAMP+timedelta(days=2)
START=END-timedelta(days=7)


def swap(kind='buy',*,token=MINT,quantity='1',sol='.01',at=None,signature='one',before=None,fee=0,decimals=6):
    row=case(kind=kind)
    row['transaction']['signatures']=[signature]
    row['blockTime']=int((at or END-timedelta(days=1)).timestamp())
    row['slot']=12345+int((at or END-timedelta(days=1)).timestamp()-START.timestamp())
    keys=row['transaction']['message']['accountKeys']
    keys[:]=[token if key==MINT else key for key in keys]
    ins=row['transaction']['message']['instructions'][0]
    ins['accounts']=[token if key==MINT else key for key in ins['accounts']]
    raw=int(D(quantity)*(10**decimals))
    old=int(D(before)*(10**decimals)) if before is not None else (0 if kind=='buy' else raw)
    new=old+raw if kind=='buy' else old-raw
    for name,amount in [('preTokenBalances',old),('postTokenBalances',new)]:
        row['meta'][name]=[balance(keys.index(BASE_ATA),token,WALLET,amount,decimals)]
    cost=int(D(sol)*10**9);index=keys.index(WALLET)
    row['meta']['fee']=fee
    row['meta']['preBalances'][index]=10**17
    row['meta']['postBalances'][index]=10**17+(-cost if kind=='buy' else cost)-fee
    return row


def pair(*,token=MINT,paid='1',received='2',qty='1',at=None,offset=0):
    at=at or END-timedelta(days=4,seconds=offset)
    return [swap(token=token,quantity=qty,sol=paid,at=at,signature=token+'-buy'),
            swap('sell',token=token,quantity=qty,sol=received,at=at+timedelta(hours=1),signature=token+'-sell')]


class SolAccountingTests(unittest.TestCase):
    def analyze(self,rows,**kwargs):return analyze_sol(rows,WALLET,START,END,**kwargs)

    def test_clean_native_swap_and_fees_are_counted_once(self):
        buy=swap(sol='1',signature='b',fee=5000,at=END-timedelta(days=2))
        sell=swap('sell',sol='2',signature='s',fee=5000,at=END-timedelta(days=1))
        result=self.analyze([buy,sell,buy])
        self.assertEqual(result['metrics']['known_fees_sol'],D('.00001'))
        self.assertEqual(result['metrics']['worst_case_sol'],D('.99999'))
        self.assertEqual(result['metrics']['closed_tokens_count'],1)
        self.assertEqual(result['coverage']['duplicate_count'],1)
        self.assertEqual(result['metrics']['unknown_cost_fraction'],0)
        self.assertEqual(result['receipts'][0]['legs'][0]['value_sol'],1)
        self.assertEqual(result['currency'],'SOL')
        self.assertFalse(result['metrics']['usd_conversion_verified'])

    def test_partial_sales_rebuy_and_fifo(self):
        rows=[swap(quantity='10',sol='1',signature='a',at=END-timedelta(days=4)),
              swap('sell',quantity='4',before='10',sol='.6',signature='b',at=END-timedelta(days=3)),
              swap(quantity='2',before='6',sol='.4',signature='c',at=END-timedelta(days=2)),
              swap('sell',quantity='8',before='8',sol='.8',signature='d',at=END-timedelta(days=1))]
        result=self.analyze(rows)
        self.assertEqual(result['metrics']['worst_case_sol'],0)
        self.assertEqual(result['tokens'][0]['open_quantity'],0)
        self.assertEqual(result['tokens'][0]['acquisition_cost_sol'],D('1.4'))
        self.assertTrue(result['tokens'][0]['known_cost'])

    def test_open_position_is_full_cost_loss_even_when_marked(self):
        row=swap(quantity='2',sol='4')
        result=self.analyze([row],marks_sol={MINT:{'price_sol':'3','timestamp':END}})
        token=result['tokens'][0]
        self.assertTrue(token['known_cost']);self.assertFalse(token['closed'])
        self.assertEqual(token['remaining_cost_sol'],4)
        self.assertEqual(token['worst_case_result_sol'],-4)
        self.assertEqual(token['marked_value_sol'],6)
        self.assertEqual(token['marked_result_sol'],2)
        self.assertEqual(result['metrics']['win_rate'],0)
        self.assertEqual(result['metrics']['unknown_cost_fraction'],0)
        self.assertEqual(sum(result['metrics']['daily'].values()),-4)
        self.assertFalse(token['mark_executable_verified'])

    def test_partial_open_realized_and_mark_are_separate(self):
        rows=[swap(quantity='10',sol='1',signature='a',at=END-timedelta(days=4)),
              swap('sell',quantity='4',before='10',sol='.6',signature='b',at=END-timedelta(days=3))]
        result=self.analyze(rows)
        self.assertEqual(result['tokens'][0]['realized_sol'],D('.2'))
        self.assertEqual(result['tokens'][0]['remaining_cost_sol'],D('.6'))
        self.assertEqual(result['tokens'][0]['worst_case_result_sol'],D('-.4'))

    def test_unknown_initial_inventory_and_transfers_are_not_free_buys(self):
        old=swap('sell',sol='2')
        result=self.analyze([old])
        self.assertIsNone(result['tokens'][0]['worst_case_result_sol'])
        self.assertEqual(result['metrics']['unknown_cost_fraction'],1)
        transfer=swap();transfer['transaction']['message']['instructions']=[{
            'programId':TOKEN_PROGRAM,'parsed':{'type':'transferChecked','info':{
                'source':'external','destination':BASE_ATA,'mint':MINT}}}]
        result=self.analyze([transfer])
        self.assertFalse(result['tokens'][0]['known_cost'])
        self.assertIn('Transferência',result['tokens'][0]['unknown_reason'])

    def test_missing_intermediate_inventory_is_unknown(self):
        rows=pair();rows[1]['meta']['preTokenBalances'][0]['uiTokenAmount']['amount']='2000000'
        rows[1]['meta']['postTokenBalances'][0]['uiTokenAmount']['amount']='1000000'
        result=self.analyze(rows)
        self.assertFalse(result['tokens'][0]['known_cost'])
        self.assertIn('Inventário',result['tokens'][0]['unknown_reason'])

    def test_wrapped_and_native_principal_not_double_counted(self):
        row=swap(sol='1')
        keys=row['transaction']['message']['accountKeys'];keys.append(QUOTE_ATA)
        # Existing WSOL account loses0.4; wallet native loses0.6: totalpaid1.
        index=keys.index(WALLET);row['meta']['postBalances'][index]=row['meta']['preBalances'][index]-600000000
        row['meta']['preBalances'].append(1402039280);row['meta']['postBalances'].append(1002039280)
        row['meta']['preTokenBalances'].append(balance(len(keys)-1,SOL,WALLET,1400000000,9))
        row['meta']['postTokenBalances'].append(balance(len(keys)-1,SOL,WALLET,1000000000,9))
        receipt=normalize_sol_receipt(row,WALLET)
        self.assertEqual(receipt['legs'][0]['value_sol'],1)
        self.assertEqual(receipt['components']['wsol_delta_sol'],D('-.4'))

    def test_wallet_funded_ata_rent_is_not_swap_price(self):
        row=swap(sol='1');keys=row['transaction']['message']['accountKeys'];i=keys.index(BASE_ATA);rent=2039280
        row['meta']['preBalances'][i]=0;row['meta']['postBalances'][i]=rent
        row['meta']['postBalances'][keys.index(WALLET)]-=rent
        row['transaction']['message']['instructions'].append({'programId':ATA_PROGRAM,
            'parsed':{'type':'create','info':{'source':WALLET,'account':BASE_ATA,'wallet':WALLET,'mint':MINT}}})
        receipt=normalize_sol_receipt(row,WALLET)
        self.assertEqual(receipt['legs'][0]['value_sol'],1)
        self.assertEqual(receipt['components']['wallet_rent_adjustment_sol'],D('.00203928'))

    def test_sponsored_rent_does_not_reduce_the_wallets_price(self):
        row=swap(sol='1');keys=row['transaction']['message']['accountKeys'];i=keys.index(BASE_ATA);rent=2039280
        row['meta']['preBalances'][i]=0;row['meta']['postBalances'][i]=rent
        row['transaction']['message']['instructions'].append({'programId':ATA_PROGRAM,
            'parsed':{'type':'create','info':{'source':'external-sponsor','account':BASE_ATA,'wallet':WALLET,'mint':MINT}}})
        receipt=normalize_sol_receipt(row,WALLET)
        self.assertEqual(receipt['legs'][0]['value_sol'],1)
        self.assertEqual(receipt['components']['wallet_rent_adjustment_sol'],0)
        self.assertEqual(receipt['components']['sponsored_rent_sol'],D('.00203928'))

    def test_unknown_rent_or_external_tip_keeps_cost_unknown(self):
        rent=swap();keys=rent['transaction']['message']['accountKeys'];i=keys.index(BASE_ATA)
        rent['meta']['preBalances'][i]=0;rent['meta']['postBalances'][i]=2039280
        tip=swap();tip['transaction']['message']['instructions'].append({'programId':SYSTEM_PROGRAM,
            'parsed':{'type':'transfer','info':{'source':WALLET,'destination':'unrelated-tip','lamports':100000}}})
        for row in (rent,tip):
            with self.subTest():
                result=self.analyze([row]);self.assertEqual(result['metrics']['unknown_tokens_count'],1)
                self.assertIsNone(result['tokens'][0]['worst_case_result_sol'])

    def test_usdc_quote_and_multistep_are_not_priced_by_network_fee(self):
        stable=case(program=PUMP_AMM_PROGRAM)
        stable['blockTime']=int((END-timedelta(days=1)).timestamp())
        route=swap();route['transaction']['message']['instructions']*=2
        for row in (stable,route):
            with self.subTest():
                result=self.analyze([row]);self.assertEqual(result['metrics']['known_tokens_count'],0)
                self.assertIsNone(result['metrics']['worst_case_sol'])
                self.assertIsNone(result['metrics']['trades_per_day'])
                self.assertEqual(result['metrics']['recognized_trades_count'],0)

    def test_failed_transaction_fee_and_wrap_do_not_create_tokens(self):
        row=swap();row['meta']['err']={'InstructionError':[0,'failure']};row['meta']['fee']=5000
        result=self.analyze([row])
        self.assertEqual(result['metrics']['tokens_count'],0)
        self.assertEqual(result['metrics']['known_fees_sol'],D('.000005'))
        self.assertEqual(result['metrics']['trades_count'],0)

    def test_no_current_sol_conversion_and_future_mark_not_used(self):
        row=swap(sol='2')
        result=self.analyze([row],marks_sol={MINT:{'price_sol':'100','timestamp':END+timedelta(seconds=1)}})
        self.assertEqual(result['tokens'][0]['worst_case_result_sol'],-2)
        self.assertIsNone(result['tokens'][0]['marked_result_sol'])
        future=swap(at=END,signature='future')
        result=self.analyze([future])
        self.assertEqual(result['metrics']['tokens_count'],0)
        self.assertEqual(result['coverage']['invalid_count'],1)
        self.assertEqual(result['coverage']['outside_window_count'],1)

    def test_receipts_survive_json_replay_and_preserve_decimals(self):
        rows=pair(qty='9007199.254740993')
        # Use9decimal precision to preserve values above IEEE754integerlimit.
        rows=[swap(quantity='9007199.254740993',sol='1',signature='b',at=END-timedelta(days=2),decimals=9),
              swap('sell',quantity='9007199.254740993',sol='2',signature='s',at=END-timedelta(days=1),decimals=9)]
        result=self.analyze(rows)
        replay=self.analyze(json.loads(dumps(result['receipts'])))
        self.assertEqual(replay['metrics']['worst_case_sol'],1)
        self.assertEqual(dumps(result['receipts']),dumps(replay['receipts']))
        self.assertEqual(result['receipts'][0]['legs'][0]['quantity'],D('9007199.254740993'))

    def test_concentration_uses_positive_sum_and_distinguishes_required_examples(self):
        a=pair(token='A',paid='1',received='12001')+pair(token='loss',paid='2001',received='1')
        b=[]
        for i in range(5):b+=pair(token='B'+str(i),paid='1',received='801',offset=i*100)
        ma=self.analyze(a)['metrics'];mb=self.analyze(b)['metrics']
        self.assertEqual((ma['worst_case_sol'],ma['without_best_sol'],ma['top1_share']),(10000,-2000,1))
        self.assertEqual((mb['worst_case_sol'],mb['without_best_sol'],mb['top1_share']),(4000,3200,D('.2')))
        self.assertTrue(mb['no_observed_losses']);self.assertIsNone(mb['profit_factor'])

    def test_open_loser_is_included_in_concentration_and_win_rate(self):
        rows=pair(token='winner',paid='1',received='3')+[swap(token='open-loser',sol='5',signature='open')]
        result=self.analyze(rows)['metrics']
        self.assertEqual(result['worst_case_sol'],-3)
        self.assertEqual(result['win_rate'],D('.5'))
        self.assertEqual(result['profit_factor'],D('.4'))
        self.assertEqual(result['top1_share'],1)
        self.assertEqual(result['without_best_sol'],-5)

    def test_divergent_duplicate_never_becomes_two_trades_or_known_profit(self):
        a=swap(signature='same');b=deepcopy(a);b['meta']['postBalances'][0]-=1000
        result=self.analyze([a,b])
        self.assertGreater(result['coverage']['invalid_count'],0)
        self.assertEqual(result['metrics']['known_tokens_count'],0)
        self.assertFalse(result['coverage']['all_transactions_classified'])

    def test_other_dex_requires_exact_birdeye_rpc_single_swap_corroboration(self):
        from wallets.models import Event
        from dataclasses import replace
        row=swap(sol='1',signature='correlated')
        message=row['transaction']['message'];old=message['instructions'][0]['programId']
        other='OtherProtocol111111111111111111111111111111'
        message['accountKeys']=[other if k==old else k for k in message['accountKeys']]
        message['instructions'][0]['programId']=other
        stamp=END-timedelta(days=1)
        token=Event('correlated:0:0:'+MINT+':buy',WALLET,MINT,stamp,'buy',D(1),tx_hash='correlated',source='birdeye')
        quote=Event('correlated:0:0:'+SOL+':sell',WALLET,SOL,stamp,'sell',D(1),tx_hash='correlated',source='birdeye')
        self.assertEqual(self.analyze([row])['metrics']['known_tokens_count'],0)
        good=self.analyze([row],birdeye_events=[token,quote])
        self.assertEqual(good['metrics']['worst_case_sol'],-1)
        self.assertEqual(good['receipts'][0]['classification'],'birdeye_rpc_correlated_single_swap')
        self.assertEqual(len(good['receipts'][0]['birdeye_proof']),2)
        for bad in ([token,replace(quote,quantity=D('2'))],
                    [replace(token,wallet='external'),quote],
                    [replace(token,timestamp=stamp+timedelta(seconds=1)),quote],
                    [replace(token,multi_step=True),quote], [token,quote,token]):
            with self.subTest():
                self.assertEqual(self.analyze([row],birdeye_events=bad)['metrics']['known_tokens_count'],0)
        tipped=deepcopy(row);tipped['transaction']['message']['instructions'].append({'programId':SYSTEM_PROGRAM,
            'parsed':{'type':'transfer','info':{'source':WALLET,'destination':'unrelated-tip','lamports':100000}}})
        self.assertEqual(self.analyze([tipped],birdeye_events=[token,quote])['metrics']['known_tokens_count'],0)
