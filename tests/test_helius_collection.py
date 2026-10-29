"""Histórico realista em SQLite e cliente simulado; nenhuma chamada de rede."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wallets.helius import BudgetExceeded
from wallets.helius_collection import HeliusHistory
from wallets.models import Event, iso
from wallets.storage import Store

NOW = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
WALLET, TOKEN, CURVE, OTHER = 'A'*44, 'B'*44, 'C'*44, 'D'*44


def tx(signature='sig', *, at=None, slot=100, index=0, owner=WALLET, token=TOKEN, kind='buy', failed=False):
    at = at or NOW-timedelta(days=1)
    return {'slot':slot, 'transactionIndex':index, 'blockTime':int(at.timestamp()),
            'transaction':{'signatures':[signature], 'message':{'accountKeys':[owner], 'instructions':[]}},
            'meta':{'err':{'InstructionError':[0,'failure']} if failed else None, 'fee':5000,
                    'preTokenBalances':[], 'postTokenBalances':[{'accountIndex':1,'mint':token,'owner':owner,
                    'uiTokenAmount':{'amount':'9007199254740993','decimals':6}}]},
            'fixture_kind':kind}


class Client:
    def __init__(self, responses=()):
        self.responses = list(responses); self.calls=[]
    def request(self, method, params, **kwargs):
        self.calls.append((method,deepcopy(params)))
        if not self.responses:raise AssertionError('Pedido inesperado')
        result=self.responses.pop(0)
        if isinstance(result,Exception):raise result
        return deepcopy(result)


def normalize_fixture(row,wallet,*,target_token=None):
    # A identificação real de protocolos é testada no módulo de normalização.
    balance=row['meta']['postTokenBalances'][0]
    return {'events':[Event('helius:'+row['transaction']['signatures'][0]+':'+wallet,
        wallet,balance['mint'],datetime.fromtimestamp(row['blockTime'],timezone.utc),row['fixture_kind'],
        Decimal(balance['uiTokenAmount']['amount'])/Decimal(10)**balance['uiTokenAmount']['decimals'],
        tx_hash=row['transaction']['signatures'][0],source='helius')], 'evidence':{}, 'limitations':[]}


class HeliusCollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'state.sqlite'; self.store=Store(self.path)
        self.addCleanup(lambda:self.store.close())
        self.no_network=patch('socket.socket.connect',side_effect=AssertionError('Rede proibida'))
        self.no_network.start(); self.addCleanup(self.no_network.stop)
    def history(self,client,*,pages=5,size=100,now=NOW,extra=None):
        config={'helius':{'max_pages':pages,'page_size':size}}
        config['helius'].update(extra or {})
        return HeliusHistory(client,self.store,config,clock=lambda:now,normalizer=normalize_fixture)
    def fetch(self,client,**kwargs):
        return self.history(client,**kwargs).history(WALLET,NOW-timedelta(days=30),NOW)
    def test_complete_page_persists_raw_without_float_amount_loss(self):
        row=tx(); client=Client([{'data':[row,row],'paginationToken':None}])
        result=self.fetch(client)
        self.assertTrue(result['pages_complete']); self.assertFalse(result['truncated'])
        self.assertEqual(len(result['transactions']),1)
        self.assertEqual(result['transactions'][0]['meta']['postTokenBalances'][0]['uiTokenAmount']['amount'],'9007199254740993')
        query=client.calls[0][1][1]
        self.assertEqual(query['filters']['status'],'any')
        self.assertEqual(query['filters']['tokenAccounts'],'balanceChanged')
        self.assertEqual(query['maxSupportedTransactionVersion'],1)
        self.assertEqual(query['transactionDetails'],'full')
        again=self.fetch(Client())
        self.assertTrue(again['pages_complete']); self.assertEqual(again['pages_requested'],0)
    def test_pagination_dedup_and_failed_transactions_preserved(self):
        a,b=tx('a',slot=101),tx('failed',slot=102,failed=True)
        client=Client([{'data':[a],'paginationToken':'101:0'}, {'data':[a,b],'paginationToken':None}])
        result=self.fetch(client)
        self.assertTrue(result['pages_complete'])
        self.assertEqual(len(result['transactions']),2)
        self.assertIsNotNone(result['transactions'][0]['meta']['err'])
        self.assertEqual(client.calls[1][1][1]['paginationToken'],'101:0')
    def test_partial_fixed_window_resumes_after_database_restart_before_tail(self):
        first=Client([{'data':[tx('a')],'paginationToken':'100:0'}])
        result=self.fetch(first,pages=1)
        self.assertFalse(result['pages_complete']); self.assertEqual(len(result['transactions']),1)
        old_filters=first.calls[0][1][1]['filters']
        self.store.close(); self.store=Store(self.path)
        later=NOW+timedelta(days=1)
        second=Client([{'data':[tx('b',slot=101)],'paginationToken':None},
                       {'data':[tx('c',at=NOW+timedelta(hours=1),slot=102)],'paginationToken':None}])
        result=self.history(second,now=later).history(WALLET,NOW-timedelta(days=29),later)
        self.assertTrue(result['pages_complete'])
        self.assertEqual(second.calls[0][1][1]['filters'],old_filters)
        self.assertEqual(second.calls[0][1][1]['paginationToken'],'100:0')
        tail=second.calls[1][1][1]['filters']['blockTime']
        self.assertEqual(tail['gte'],int((NOW-timedelta(seconds=1)).timestamp()))
        self.assertEqual(tail['lte'],int(later.timestamp()))
        self.assertEqual(len(result['transactions']),3)
    def test_newer_pending_window_cannot_leak_into_historical_cutoff(self):
        self.fetch(Client([{'data':[tx()],'paginationToken':'100:0'}]),pages=1)
        client=Client()
        result=self.history(client).history(WALLET,NOW-timedelta(days=30),NOW-timedelta(hours=1))
        self.assertFalse(result['pages_complete']); self.assertFalse(client.calls)
        self.assertTrue(any('futura' in reason for reason in result['failures']))
    def test_expanding_lookback_refetches_older_days(self):
        self.fetch(Client([{'data':[tx()],'paginationToken':None}]))
        old=tx('older',at=NOW-timedelta(days=60),slot=1)
        client=Client([{'data':[old,tx()],'paginationToken':None}])
        result=self.history(client).history(WALLET,NOW-timedelta(days=90),NOW)
        self.assertTrue(result['pages_complete'])
        self.assertEqual(client.calls[0][1][1]['filters']['blockTime']['gte'],int((NOW-timedelta(days=90)).timestamp()))
        self.assertEqual(len(result['transactions']),2)
    def test_repeated_cursor_is_incomplete_without_skipping_page(self):
        client=Client([{'data':[tx('a')],'paginationToken':'100:0'},
                       {'data':[tx('b',slot=101)],'paginationToken':'100:0'}])
        result=self.fetch(client)
        self.assertFalse(result['pages_complete']); self.assertEqual(len(result['transactions']),1)
        state=json.loads(self.store.get_cursor(HeliusHistory.cursor_key(WALLET)))
        self.assertEqual(state['pending']['next'],'100:0')
    def test_conflicting_duplicate_rejects_page_and_preserves_safe_cursor(self):
        changed=tx('a'); changed['meta']['fee']=10000
        client=Client([{'data':[tx('a')],'paginationToken':'100:0'},
                       {'data':[changed],'paginationToken':'101:0'}])
        result=self.fetch(client)
        self.assertFalse(result['pages_complete'])
        self.assertEqual(result['transactions'][0]['meta']['fee'],5000)
        state=json.loads(self.store.get_cursor(HeliusHistory.cursor_key(WALLET)))
        self.assertEqual(state['pending']['next'],'100:0')
    def test_future_or_outside_range_rows_never_advance_cursor(self):
        for stamp in (NOW+timedelta(seconds=1),NOW-timedelta(days=31)):
            client=Client([{'data':[tx('future',at=stamp)],'paginationToken':None}])
            result=self.fetch(client)
            self.assertFalse(result['pages_complete']); self.assertEqual(result['transactions'],[])
            state=json.loads(self.store.get_cursor(HeliusHistory.cursor_key(WALLET)))
            self.assertIsNone(state['covered_end']); self.assertIsNone(state['pending']['next'])
    def test_malformed_pages_and_continuations_are_not_empty_success(self):
        for result in ({'data':None},{'data':[None]},{'data':[],'paginationToken':'bad'},
                       {'data':[],'paginationToken':'1:0'},{'data':[tx()],'paginationToken':5}):
            with self.subTest(result=result):
                history=self.fetch(Client([result]))
                self.assertFalse(history['pages_complete']); self.assertTrue(history['failures'])
    def test_full_page_without_end_metadata_stays_indeterminate(self):
        result=self.fetch(Client([{'data':[tx()]}]),size=1)
        self.assertFalse(result['pages_complete']); self.assertTrue(result['failures'])
    def test_optional_absent_cursor_on_short_page_is_terminal(self):
        result=self.fetch(Client([{'data':[tx()]}]),size=100)
        self.assertTrue(result['pages_complete'])
    def test_coverage_cursor_never_advances_before_persistence(self):
        client=Client([{'data':[tx()],'paginationToken':'100:0'}])
        with patch.object(self.store,'put_source_transactions',side_effect=ValueError('disk failure')):
            result=self.fetch(client,pages=1)
        self.assertFalse(result['pages_complete'])
        state=json.loads(self.store.get_cursor(HeliusHistory.cursor_key(WALLET)))
        self.assertIsNone(state['pending']['next'])
        resumed=self.fetch(Client([{'data':[tx()],'paginationToken':None}]))
        self.assertTrue(resumed['pages_complete'])
    def test_quota_and_corrupt_state_fail_closed_without_reset(self):
        result=self.fetch(Client([BudgetExceeded('secret-should-not-be-repeated')]))
        self.assertFalse(result['pages_complete'])
        self.assertNotIn('secret-should',str(result))
        key=HeliusHistory.cursor_key(WALLET)
        self.store.set_cursor(key,'broken-state')
        client=Client(); result=self.fetch(client)
        self.assertFalse(result['pages_complete']); self.assertFalse(client.calls)
        self.assertEqual(self.store.get_cursor(key),'broken-state')
    def test_direct_and_wallet_scopes_have_independent_pagination(self):
        client=Client([{'data':[],'paginationToken':None},{'data':[],'paginationToken':None}])
        history=self.history(client)
        history.history(WALLET,NOW-timedelta(days=30),NOW,token_accounts=False,ascending=True)
        history.history(WALLET,NOW-timedelta(days=30),NOW,token_accounts=True,ascending=False)
        self.assertEqual([c[1][1]['filters']['tokenAccounts'] for c in client.calls],['none','balanceChanged'])
        self.assertEqual([c[1][1]['sortOrder'] for c in client.calls],['asc','desc'])
    def test_missing_transaction_position_is_explicitly_unknown(self):
        row=tx(); row.pop('transactionIndex')
        result=self.fetch(Client([{'data':[row],'paginationToken':None}]))
        self.assertTrue(result['pages_complete']); self.assertFalse(result['ordering_complete'])
    def test_discovery_only_buys_and_first_per_owner_are_recorded(self):
        rows=[tx('later',slot=103),tx('earlier',slot=100),tx('transfer',slot=101,owner=OTHER,kind='transfer_in'),
              tx('failure',slot=102,owner=OTHER,failed=True)]
        result=self.history(Client([{'data':rows,'paginationToken':None}])).discover_first_observed(TOKEN,NOW)
        self.assertEqual(result['wallets'],[WALLET]); self.assertEqual(result['buyers'][0]['tx_hash'],'earlier')
        self.assertFalse(result['global_first_buyers_verified'])
        self.assertTrue(result['scope_pages_complete'])
        evidence=self.store.discovery_evidence(WALLET,NOW)[0]
        self.assertEqual(evidence['observed_at'],iso(NOW))
        self.assertEqual(evidence['evidence']['origin'],'helius_first_observed')
        self.assertEqual(evidence['evidence']['traded_at'],iso(NOW-timedelta(days=1)))
        self.assertEqual(self.store.discovery_tokens(WALLET),[TOKEN])
    def test_discovery_union_of_mint_and_launch_addresses_is_not_global_proof(self):
        rows=[tx('curve',owner=OTHER)]
        client=Client([{'data':[],'paginationToken':None},{'data':rows,'paginationToken':None}])
        result=self.history(client).discover_first_observed(TOKEN,NOW,addresses=[CURVE,CURVE])
        self.assertEqual(result['wallets'],[OTHER]); self.assertEqual(result['scope_addresses'],[TOKEN,CURVE])
        self.assertFalse(result['global_first_buyers_verified'])
        self.assertTrue(all(call[1][1]['filters']['tokenAccounts']=='none' for call in client.calls))
    def test_discovery_limits_do_not_claim_complete_first_buyers(self):
        rows=[tx('a',owner=WALLET),tx('b',owner=OTHER,slot=101)]
        result=self.history(Client([{'data':rows,'paginationToken':None}]),extra={'max_early_buyers':1,'max_launch_addresses':1}).discover_first_observed(TOKEN,NOW,addresses=[CURVE])
        self.assertEqual(result['wallets'],[WALLET]); self.assertTrue(result['limit_reached'])
        self.assertFalse(result['global_first_buyers_verified'])
    def test_future_discovery_and_invalid_address_refused_without_network(self):
        history=self.history(Client())
        for address in ('bad',''):
            with self.assertRaises(ValueError):history.history(address,NOW-timedelta(days=1),NOW)
        with self.assertRaises(ValueError):history.discover_first_observed(TOKEN,NOW+timedelta(seconds=1))
        with self.assertRaises(ValueError):history.discover_first_observed(TOKEN,NOW,addresses='not-a-list')

    def test_discovery_does_not_use_cross_scope_conflicting_transaction(self):
        a=tx('same'); b=deepcopy(a); b['meta']['fee']=9999
        client=Client([{'data':[a],'paginationToken':None},{'data':[b],'paginationToken':None}])
        result=self.history(client).discover_first_observed(TOKEN,NOW,addresses=[CURVE])
        self.assertEqual(result['wallets'],[])
        self.assertFalse(result['scope_pages_complete'])
        self.assertTrue(result['failures'])

    def test_discovery_malformed_token_balances_are_unknown(self):
        row=tx(); row['meta']['postTokenBalances']=5
        result=self.history(Client([{'data':[row],'paginationToken':None}])).discover_first_observed(TOKEN,NOW)
        self.assertEqual(result['wallets'],[])
        self.assertTrue(result['failures'])
