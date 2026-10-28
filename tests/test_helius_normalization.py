"""Dados fictícios; nenhum teste consulta Helius nem envia transações."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import unittest

from wallets.helius_normalization import (normalize_transaction, transaction_wallets,
    PUMP_PROGRAM, PUMP_AMM_PROGRAM, TOKEN_PROGRAM, SYSTEM_PROGRAM, ATA_PROGRAM,
    LEGACY_BUY, LEGACY_SELL, BASE58)
from wallets.models import SOL, Event

WALLET='wallet-beneficiary'
OTHER='sponsor-fee-payer'
MINT='token-mint'
BASE_ATA='wallet-token-account'
QUOTE_ATA='wallet-quote-account'
USDC='EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
STAMP=datetime(2026,10,28,12,tzinfo=timezone.utc)


def encoded(data):
    number=int.from_bytes(data,'big');result=''
    while number:
        number,remainder=divmod(number,58);result=BASE58[remainder]+result
    return '1'*(len(data)-len(data.lstrip(b'\0')))+result


def balance(index,mint,owner,amount,decimals=6):
    return {'accountIndex':index,'mint':mint,'owner':owner,
            'uiTokenAmount':{'amount':str(amount),'decimals':decimals,'uiAmount':float(Decimal(amount)/(10**decimals))}}


def case(*,kind='buy',sponsor=False,program=PUMP_PROGRAM,quote=None,discriminator=None,version=1):
    payer=OTHER if sponsor else WALLET
    if program==PUMP_AMM_PROGRAM:
        accounts=['pool',WALLET,'global',MINT,quote or USDC,BASE_ATA,QUOTE_ATA]
    elif version==2:
        accounts=['global',MINT,quote or SOL]+['account'+str(i) for i in range(3,16)]
        accounts[10]='curve';accounts[13]=WALLET;accounts[14]=BASE_ATA;accounts[15]=QUOTE_ATA
    elif version==3:
        accounts=['global',MINT,quote or SOL]+['account'+str(i) for i in range(3,11)]
        accounts[5]='curve';accounts[8]=WALLET;accounts[9]=BASE_ATA;accounts[10]=QUOTE_ATA
    else:
        accounts=['global','fees',MINT,'curve','curve-ata',BASE_ATA,WALLET]
    keys=list(dict.fromkeys([payer,WALLET]+accounts+[program,SYSTEM_PROGRAM,TOKEN_PROGRAM,ATA_PROGRAM]))
    discriminator=discriminator or (LEGACY_BUY if kind=='buy' else LEGACY_SELL)
    instruction={'programId':program,'accounts':accounts,'data':encoded(discriminator+b'\0'*16)}
    pre=[10000000]*len(keys);post=pre.copy()
    index=keys.index(WALLET)
    pre[index]=1000000000
    post[index]=989995000 if kind=='buy' else 1009995000
    if sponsor:
        post[0]-=5000;post[index]+=5000
    amount_before,amount_after=(0,1000000) if kind=='buy' else (1000000,0)
    before=[balance(keys.index(BASE_ATA),MINT,WALLET,amount_before)]
    after=[balance(keys.index(BASE_ATA),MINT,WALLET,amount_after)]
    if program==PUMP_AMM_PROGRAM or (version>=2 and quote not in (None,SOL)):
        quote=quote or USDC
        a,b=(100000000,90000000) if kind=='buy' else (90000000,100000000)
        before.append(balance(keys.index(QUOTE_ATA),quote,WALLET,a))
        after.append(balance(keys.index(QUOTE_ATA),quote,WALLET,b))
        post[index]=pre[index]-(5000 if not sponsor else 0)
    return {'slot':12345,'transactionIndex':7,'blockTime':int(STAMP.timestamp()),
            'transaction':{'signatures':['signature-one'],'message':{'accountKeys':keys,'instructions':[instruction]}},
            'meta':{'err':None,'fee':5000,'preBalances':pre,'postBalances':post,
                    'preTokenBalances':before,'postTokenBalances':after,'innerInstructions':[]}}


def kind_events(result,kind):return [e for e in result['events'] if e.kind==kind]


class HeliusNormalizationTests(unittest.TestCase):
    def test_direct_pump_buy_is_beneficiary_not_fee_payer(self):
        row=case(sponsor=True)
        result=normalize_transaction(row,WALLET,target_token=MINT)
        buys=kind_events(result,'buy')
        self.assertEqual([(e.token,e.quantity) for e in buys],[(MINT,Decimal(1))])
        self.assertTrue(all(e.source=='helius' for e in result['events']))
        self.assertEqual(result['evidence']['recognized_buys'],[MINT])
        self.assertEqual(result['evidence']['fee_payer'],OTHER)
        self.assertFalse(result['evidence']['fee_attributed_to_wallet'])
        self.assertEqual(result['evidence']['fee_lamports'],5000)
        self.assertEqual(transaction_wallets(row),[WALLET])
        sponsor=normalize_transaction(row,OTHER,target_token=MINT)
        self.assertEqual(kind_events(sponsor,'buy'),[])

    def test_actual_sold_units_not_instruction_limits(self):
        row=case(kind='sell')
        result=normalize_transaction(row,WALLET)
        self.assertEqual([(e.token,e.quantity) for e in kind_events(result,'sell')],[(MINT,Decimal(1))])
        self.assertEqual(result['evidence']['recognized_buys'],[])

    def test_usd_prices_fees_and_usdc_peg_not_invented(self):
        result=normalize_transaction(case(program=PUMP_AMM_PROGRAM),WALLET)
        self.assertEqual(len(kind_events(result,'buy')),1)
        self.assertEqual(len(kind_events(result,'sell')),1)
        self.assertTrue(all(e.usd_value is None and e.fee_usd is None for e in result['events']))
        self.assertTrue(result['evidence']['balance_coverage_complete'])
        self.assertTrue(result['evidence']['instruction_coverage_complete'])
        self.assertEqual(result['evidence']['fee_lamports'],5000)

    def test_only_exact_birdeye_historical_match_supplies_usd(self):
        row=case()
        historic=Event('bird',WALLET,MINT,STAMP,'buy',Decimal(1),Decimal('42.25'),
                       tx_hash='signature-one',source='birdeye')
        matched=normalize_transaction(row,WALLET,swap_evidence=[historic])
        self.assertEqual(kind_events(matched,'buy')[0].usd_value,Decimal('42.25'))
        self.assertIsNone(kind_events(matched,'buy')[0].fee_usd)
        for other in (replace(historic,quantity=Decimal('1.1')),replace(historic,source='fixture'),
                      replace(historic,tx_hash='other'),replace(historic,timestamp=STAMP.replace(hour=13)),
                      replace(historic,multi_step=True)):
            result=normalize_transaction(row,WALLET,swap_evidence=[other])
            self.assertIsNone(kind_events(result,'buy')[0].usd_value)
        result=normalize_transaction(row,WALLET,swap_evidence=[historic,historic])
        self.assertIsNone(kind_events(result,'buy')[0].usd_value)

    def test_raw_quantities_above_float_precision_remain_exact(self):
        row=case();raw=9007199254740993
        row['meta']['postTokenBalances'][0]['uiTokenAmount'].update(amount=str(raw),decimals=9,uiAmount=9007199.254740992)
        row['meta']['preTokenBalances'][0]['uiTokenAmount']['decimals']=9
        result=normalize_transaction(row,WALLET)
        self.assertEqual(kind_events(result,'buy')[0].quantity,Decimal('9007199.254740993'))

    def test_failed_transaction_does_not_create_inventory(self):
        row=case();row['meta']['err']={'InstructionError':[0,'Custom']}
        result=normalize_transaction(row,WALLET)
        self.assertEqual([e.kind for e in result['events']],['failed'])
        self.assertEqual(result['events'][0].quantity,0)
        self.assertEqual(result['evidence']['execution_status'],'failed')
        self.assertEqual(result['evidence']['recognized_buys'],[])

    def test_missing_status_is_not_success(self):
        row=case();del row['meta']['err']
        result=normalize_transaction(row,WALLET)
        self.assertEqual([e.kind for e in result['events']],['unknown'])
        self.assertFalse(result['evidence']['instruction_coverage_complete'])

    def test_two_opposite_transfers_are_not_a_swap(self):
        row=case(program=PUMP_AMM_PROGRAM)
        row['transaction']['message']['instructions']=[{
            'programId':TOKEN_PROGRAM,'parsed':{'type':'transferChecked','info':{
                'source':'external','destination':BASE_ATA,'mint':MINT,'tokenAmount':{'amount':'1000000','decimals':6}}}},
            {'programId':TOKEN_PROGRAM,'parsed':{'type':'transferChecked','info':{
                'source':QUOTE_ATA,'destination':'external2','mint':USDC,'tokenAmount':{'amount':'10000000','decimals':6}}}}]
        result=normalize_transaction(row,WALLET)
        self.assertEqual({e.kind for e in result['events']},{'transfer_in','transfer_out'})
        self.assertEqual(result['evidence']['recognized_buys'],[])
        self.assertTrue(all(e.usd_value is None for e in result['events']))

    def test_unsupported_program_and_unknown_route_are_not_buys(self):
        row=case();keys=row['transaction']['message']['accountKeys'];keys.append('unrecognized-dex')
        row['meta']['preBalances'].append(1);row['meta']['postBalances'].append(1)
        row['transaction']['message']['instructions'].append({'programId':'unrecognized-dex','accounts':[],'data':'11'})
        result=normalize_transaction(row,WALLET)
        self.assertEqual(kind_events(result,'buy'),[])
        self.assertEqual(result['evidence']['recognized_buys'],[])
        self.assertTrue(all(e.kind=='unknown' for e in result['events']))

    def test_unrecognized_pump_discriminator_is_unknown(self):
        row=case(discriminator=b'unknown!')
        result=normalize_transaction(row,WALLET)
        self.assertFalse(kind_events(result,'buy'))
        self.assertFalse(result['evidence']['instruction_coverage_complete'])

    def test_multiple_pump_steps_not_double_counted(self):
        row=case();row['transaction']['message']['instructions']*=2
        result=normalize_transaction(row,WALLET)
        self.assertFalse(kind_events(result,'buy'))
        self.assertEqual(len([e for e in result['events'] if e.token==MINT]),1)

    def test_missing_owner_and_missing_decimals_are_unknown(self):
        for field in ('owner','decimals'):
            row=case()
            if field=='owner':del row['meta']['preTokenBalances'][0]['owner']
            else:del row['meta']['postTokenBalances'][0]['uiTokenAmount']['decimals']
            result=normalize_transaction(row,WALLET)
            self.assertFalse(kind_events(result,'buy'))
            self.assertFalse(result['evidence']['balance_coverage_complete'])

    def test_unreported_balance_is_not_automatically_zero(self):
        row=case();row['meta']['preTokenBalances']=[]
        result=normalize_transaction(row,WALLET)
        self.assertFalse(kind_events(result,'buy'))
        self.assertFalse(result['evidence']['balance_coverage_complete'])
        # Conta inexistente com0lamports demonstra a criação, não uma lacuna.
        index=row['transaction']['message']['accountKeys'].index(BASE_ATA)
        row['meta']['preBalances'][index]=0
        result=normalize_transaction(row,WALLET)
        self.assertEqual(len(kind_events(result,'buy')),1)

    def test_closed_account_uses_pre_owner_and_no_fictional_purchase(self):
        row=case(kind='sell');row['meta']['postTokenBalances']=[]
        index=row['transaction']['message']['accountKeys'].index(BASE_ATA)
        row['meta']['postBalances'][index]=0
        result=normalize_transaction(row,WALLET)
        self.assertEqual(len(kind_events(result,'sell')),1)
        self.assertFalse(kind_events(result,'buy'))

    def test_owner_change_and_wrong_beneficiary_are_not_purchases(self):
        row=case();row['meta']['preTokenBalances'][0]['owner']=OTHER
        self.assertFalse(kind_events(normalize_transaction(row,WALLET),'buy'))
        row=case();row['meta']['preTokenBalances'][0]['owner']=OTHER;row['meta']['postTokenBalances'][0]['owner']=OTHER
        self.assertFalse(kind_events(normalize_transaction(row,WALLET),'buy'))

    def test_actual_transfer_solana_native_excludes_network_fee(self):
        row=case();row['meta']['preTokenBalances']=[];row['meta']['postTokenBalances']=[]
        row['transaction']['message']['instructions']=[{'programId':SYSTEM_PROGRAM,'parsed':{
            'type':'transfer','info':{'source':WALLET,'destination':'other','lamports':10000000}}}]
        result=normalize_transaction(row,WALLET)
        self.assertEqual([(e.kind,e.quantity) for e in result['events']],[('transfer_out',Decimal('.01'))])
        self.assertIsNone(result['events'][0].fee_usd)

    def test_wrap_and_unwrap_do_not_create_profit(self):
        for wrapping in (True,False):
            row=case(kind='buy' if wrapping else 'sell')
            token=BASE_ATA
            for phase in ('preTokenBalances','postTokenBalances'):
                row['meta'][phase][0]['mint']=SOL;row['meta'][phase][0]['uiTokenAmount']['decimals']=9
            row['transaction']['message']['instructions']=[{'programId':TOKEN_PROGRAM,'parsed':{
                'type':'syncNative' if wrapping else 'closeAccount','info':{'account':token,'owner':WALLET}}}]
            result=normalize_transaction(row,WALLET)
            self.assertEqual([e.kind for e in result['events']],['wrap'])
            self.assertFalse(kind_events(result,'buy'))
            self.assertIsNone(result['events'][0].usd_value)

    def test_mint_burn_and_liquidity_do_not_become_buys(self):
        for kind in ('mintTo','burn','setAuthority'):
            row=case();row['transaction']['message']['instructions']=[{'programId':TOKEN_PROGRAM,'parsed':{
                'type':kind,'info':{'account':BASE_ATA,'mint':MINT,'destination':BASE_ATA}}}]
            result=normalize_transaction(row,WALLET)
            self.assertFalse(kind_events(result,'buy'))
            self.assertTrue(all(e.kind=='unknown' for e in result['events']))
        # Instrução LP ainda não suportada é desconhecida, não swap por net flows.
        row=case(program=PUMP_AMM_PROGRAM,discriminator=b'liquidit')
        self.assertFalse(kind_events(normalize_transaction(row,WALLET),'buy'))

    def test_extra_base_transfer_disallows_inflated_purchase_quantity(self):
        row=case();row['transaction']['message']['instructions'].append({'programId':TOKEN_PROGRAM,'parsed':{
            'type':'transfer','info':{'source':'airdrop-source','destination':BASE_ATA,'amount':'100'}}})
        result=normalize_transaction(row,WALLET)
        self.assertFalse(kind_events(result,'buy'))

    def test_signatures_and_loaded_addresses_compiled_instructions(self):
        row=case();message=row['transaction']['message'];original=list(message['accountKeys'])
        instruction=message['instructions'][0]
        instruction['programIdIndex']=original.index(instruction.pop('programId'))
        instruction['accounts']=[original.index(value) for value in instruction['accounts']]
        # Última conta é carregada via address lookup table (json, não jsonParsed).
        removed=message['accountKeys'].pop()
        row['meta']['loadedAddresses']={'writable':[removed],'readonly':[]}
        result=normalize_transaction(row,WALLET)
        self.assertEqual(len(kind_events(result,'buy')),1)
        # jsonParsed já contém as loaded keys: não duplicar.
        row=case();row['transaction']['message']['accountKeys']=[{'pubkey':key,'signer':key==WALLET,'writable':True} for key in row['transaction']['message']['accountKeys']]
        row['meta']['loadedAddresses']={'writable':['ignored-already-parsed'],'readonly':[]}
        self.assertEqual(len(kind_events(normalize_transaction(row,WALLET),'buy')),1)

    def test_supported_v2_v3_and_pumpswap_variants(self):
        for version,disc in ((2,bytes([184,23,238,97,103,197,211,61])),(3,bytes([7,5,29,196,245,23,101,80]))):
            result=normalize_transaction(case(version=version,quote=USDC,discriminator=disc),WALLET)
            self.assertEqual(len(kind_events(result,'buy')),1)
        row=case(program=PUMP_AMM_PROGRAM,discriminator=bytes([198,46,21,82,180,217,232,112]))
        self.assertEqual(len(kind_events(normalize_transaction(row,WALLET),'buy')),1)

    def test_bad_identity_and_missing_metadata_fail_closed(self):
        for change in (lambda r:r.update(meta=None),lambda r:r.update(blockTime=None),
                       lambda r:r['transaction'].update(signatures=[]),lambda r:r['transaction']['message'].update(accountKeys=[])):
            row=case();change(row)
            with self.assertRaises(ValueError):normalize_transaction(row,WALLET)

    def test_event_identity_deterministic_and_no_priority_double_count(self):
        row=case();row['meta']['priority_fee']=4000
        first=normalize_transaction(row,WALLET);second=normalize_transaction(deepcopy(row),WALLET)
        self.assertEqual(first,second)
        self.assertEqual(first['evidence']['fee_lamports'],5000)
        self.assertEqual(first['evidence']['transaction_index'],7)
        self.assertEqual(first['evidence']['slot'],12345)
        self.assertEqual(first['evidence']['block_time'],STAMP)

    def test_no_token_balance_data_is_unknown_not_complete(self):
        row=case();del row['meta']['preTokenBalances'];del row['meta']['postTokenBalances']
        result=normalize_transaction(row,WALLET)
        self.assertFalse(result['evidence']['balance_coverage_complete'])
        self.assertFalse(result['evidence']['recognized_buys'])

    def test_documented_pumpswap_deposit_and_withdraw_are_liquidity(self):
        for deposit,discriminator in ((True,bytes([242,35,198,137,82,225,242,182])),
                                      (False,bytes([183,18,70,156,148,109,161,34]))):
            row=case(program=PUMP_AMM_PROGRAM)
            accounts=['pool','global',WALLET,MINT,USDC,'lp-mint',BASE_ATA,QUOTE_ATA,'user-lp-ata']
            keys=list(dict.fromkeys([WALLET]+accounts+[PUMP_AMM_PROGRAM,TOKEN_PROGRAM]))
            row['transaction']['message']={'accountKeys':keys,'instructions':[{'programId':PUMP_AMM_PROGRAM,
                'accounts':accounts,'data':encoded(discriminator+b'\0'*24)}]}
            row['meta']['preBalances']=[10000000]*len(keys);row['meta']['postBalances']=[10000000]*len(keys)
            row['meta']['postBalances'][0]-=5000
            starts=[10000000,100000000,0] if deposit else [0,0,1000000]
            ends=[0,0,1000000] if deposit else [10000000,100000000,0]
            row['meta']['preTokenBalances']=[balance(keys.index(account),mint,WALLET,raw) for account,mint,raw in zip(accounts[6:9],accounts[3:6],starts)]
            row['meta']['postTokenBalances']=[balance(keys.index(account),mint,WALLET,raw) for account,mint,raw in zip(accounts[6:9],accounts[3:6],ends)]
            result=normalize_transaction(row,WALLET)
            self.assertEqual({e.kind for e in result['events']},{'liquidity'})
            self.assertEqual(len(result['events']),3)
            self.assertEqual(result['evidence']['recognized_buys'],[])
            self.assertEqual(result['evidence']['liquidity_operations'][0]['kind'],'deposit' if deposit else 'withdraw')

    def test_documented_anchor_event_cpi_does_not_hide_direct_buy(self):
        from wallets.helius_normalization import ANCHOR_EVENT_TAG
        row=case();message=row['transaction']['message'];instruction=message['instructions'][0]
        while len(instruction['accounts'])<=10:instruction['accounts'].append('extra-'+str(len(instruction['accounts'])))
        authority=instruction['accounts'][10]
        for key in instruction['accounts']:
            if key not in message['accountKeys']:
                message['accountKeys'].append(key)
                row['meta']['preBalances'].append(1);row['meta']['postBalances'].append(1)
        event={'programId':PUMP_PROGRAM,'accounts':[authority],
               'data':encoded(ANCHOR_EVENT_TAG+bytes([189,219,127,211,78,230,97,238])+b'fixture-event-body')}
        row['meta']['innerInstructions']=[{'index':0,'instructions':[event]}]
        self.assertEqual(len(kind_events(normalize_transaction(row,WALLET),'buy')),1)
        for change in ('tag','authority','event','top_level'):
            malformed=deepcopy(row)
            altered=malformed['meta']['innerInstructions'][0]['instructions'][0]
            if change=='tag':altered['data']=encoded(b'not-event'+b'\0'*16)
            elif change=='authority':altered['accounts']=[WALLET]
            elif change=='event':altered['data']=encoded(ANCHOR_EVENT_TAG+b'notknown'+b'fixture')
            else:
                malformed['meta']['innerInstructions']=[]
                malformed['transaction']['message']['instructions'].append(altered)
            self.assertFalse(kind_events(normalize_transaction(malformed,WALLET),'buy'),change)
