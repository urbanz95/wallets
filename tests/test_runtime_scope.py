"""Âmbito observado derivado de provas de paginação, sem garantias globais."""
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_collection import FakeClient, NOW, WALLET, T1, trade
from wallets.birdeye import BirdeyeError
from wallets.collection import Collector
from wallets.models import Coverage, Event, iso
from wallets.pipeline import augment_with_helius
from wallets.storage import Store


class RuntimeScopeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = Store(Path(temporary.name) / 'state.sqlite')
        self.addCleanup(self.store.close)
        self.start = NOW - timedelta(days=30)

    def collect(self, client, pages=5):
        return Collector(client,self.store,{'api':{'max_pages':pages}},clock=lambda:NOW).collect(WALLET,self.start,NOW)

    def assert_global_unknown(self, coverage):
        for name in ('all_tokens','opening_inventory_known','fees_complete','marks_complete',
                     'liquidity_complete','execution_complete','coordination_complete','labels_complete'):
            self.assertFalse(getattr(coverage,name),name)

    def augment(self,result,provider=None,base=None,parsed=None):
        coverage=Coverage(self.start,NOW,NOW,source='birdeye',failures=['Birdeye: histórico parcial.'])
        history=Mock();history.history.return_value=result
        normalizer=Mock(return_value=parsed or {'events':[],'evidence':{},'limitations':[]})
        with patch('wallets.helius_normalization.normalize_transaction',normalizer),patch('wallets.pipeline.utcnow',return_value=NOW):
            return augment_with_helius(WALLET,self.start,NOW,base or [],coverage,provider or {},history,self.store)

    def test_completed_history_has_observed_coverage_without_global_claims(self):
        _,coverage,provider=self.collect(FakeClient([trade()]))
        self.assertTrue(coverage.complete)
        self.assertFalse(coverage.truncated)
        self.assertEqual(coverage.failures,[])
        self.assert_global_unknown(coverage)
        self.assertEqual(provider['history_scope']['start'],iso(self.start))
        self.assertEqual(provider['history_scope']['end'],iso(NOW))
        self.assertTrue(provider['history_scope']['pages_complete'])
        self.assertIn('não universo global',provider['history_scope']['definition'])
        self.assertTrue(any('Retenção integral' in text for text in provider['limitations']))

    def test_truncated_history_is_still_incomplete(self):
        _,coverage,provider=self.collect(FakeClient([trade()],more=True),pages=1)
        self.assertFalse(coverage.complete)
        self.assertTrue(coverage.truncated)
        self.assertFalse(provider['history_scope']['pages_complete'])
        self.assert_global_unknown(coverage)

    def test_optional_pnl_and_holdings_failure_preserves_history_evidence(self):
        client=FakeClient([trade()]);original=client.request
        def request(method,path,params=None,body=None,**kwargs):
            if path in ('/wallet/v2/pnl/details','/wallet/v2/current-net-worth'):
                raise BirdeyeError('Resposta opcional indisponível.')
            return original(method,path,params,body,**kwargs)
        client.request=request
        _,coverage,provider=self.collect(client)
        self.assertTrue(coverage.complete)
        self.assertFalse(coverage.truncated)
        self.assertEqual(coverage.failures,[])
        self.assertFalse(provider['pages_complete']['pnl'])
        self.assertFalse(provider['pages_complete']['holdings'])
        self.assertTrue(any('PnL informativo indisponível' in value for value in provider['limitations']))
        self.assertTrue(any('Inventário indicativo indisponível' in value for value in provider['limitations']))
        self.assert_global_unknown(coverage)

    def test_excluded_future_or_other_owner_trade_cannot_confirm_history(self):
        for row in (trade(block_unix_time=int((NOW+timedelta(days=1)).timestamp())),trade(owner='other-owner')):
            with self.subTest(row=row):
                _,coverage,provider=self.collect(FakeClient([row]))
                self.assertFalse(coverage.complete)
                self.assertTrue(coverage.truncated)
                self.assertFalse(provider['pages_complete']['trades'])
                self.assertTrue(coverage.failures)

    def test_excluded_invalid_transfer_cannot_confirm_history(self):
        for row in ({'unix_time':'not-a-date'},
                    {'unix_time':int((NOW+timedelta(days=1)).timestamp())},
                    {'unix_time':int(NOW.timestamp()),'owner':'other-owner'}):
            with self.subTest(row=row):
                client=FakeClient();original=client.request
                def request(method,path,params=None,body=None,**kwargs):
                    if path=='/wallet/v2/transfer':return {'success':True,'data':[row]}
                    return original(method,path,params,body,**kwargs)
                client.request=request
                _,coverage,provider=self.collect(client)
                self.assertFalse(coverage.complete)
                self.assertTrue(coverage.truncated)
                self.assertFalse(provider['pages_complete']['transfers'])
                self.assertTrue(coverage.failures)

    def test_helius_completed_scope_can_supply_history_without_erasing_birdeye_failures(self):
        _,coverage,provider=self.augment({'transactions':[],'pages_complete':True,'truncated':False,'failures':[]})
        self.assertTrue(coverage.complete)
        self.assertFalse(coverage.truncated)
        self.assertIn('Birdeye: histórico parcial.',coverage.failures)
        self.assertEqual(provider['history_scope']['complete_sources'],['helius'])
        self.assert_global_unknown(coverage)

    def test_helius_normalization_failure_does_not_promote_history(self):
        invalid=Event('wrong-owner','another-wallet',T1,NOW-timedelta(days=1),'buy',Decimal(1),source='helius')
        _,coverage,provider=self.augment({'transactions':[{}],'pages_complete':True,'truncated':False,'failures':[]},
                                         parsed={'events':[invalid],'evidence':{},'limitations':[]})
        self.assertFalse(coverage.complete)
        self.assertTrue(coverage.truncated)
        self.assertTrue(provider['helius']['failures'])
        self.assert_global_unknown(coverage)

    def test_birdeye_completed_history_survives_helius_truncation_but_records_it(self):
        _,coverage,provider=self.augment({'transactions':[],'pages_complete':False,'truncated':True,'failures':['quota']},
                                         provider={'pages_complete':{'trades':True,'transfers':True}})
        self.assertTrue(coverage.complete)
        self.assertFalse(coverage.truncated)
        self.assertEqual(provider['history_scope']['complete_sources'],['birdeye'])
        self.assertTrue(any('Helius' in failure for failure in coverage.failures))
        self.assert_global_unknown(coverage)

    def test_neither_source_complete_keeps_history_truncated(self):
        _,coverage,provider=self.augment({'transactions':[],'pages_complete':False,'truncated':True,'failures':[]},
                                         provider={'pages_complete':{'trades':True,'transfers':False}})
        self.assertFalse(coverage.complete)
        self.assertTrue(coverage.truncated)
        self.assertEqual(provider['history_scope']['complete_sources'],[])

    def test_quantity_conflicts_are_preserved_as_evidence_and_failure(self):
        base=Event('bird',WALLET,T1,NOW-timedelta(days=1),'buy',Decimal(1),Decimal(10),tx_hash='same',source='birdeye')
        h=Event('hel',WALLET,T1,NOW-timedelta(days=1),'buy',Decimal(2),tx_hash='same',source='helius')
        _,coverage,provider=self.augment({'transactions':[{}],'pages_complete':True,'truncated':False,'failures':[]},
                                         base=[base],parsed={'events':[h],'evidence':{},'limitations':[]})
        self.assertTrue(provider['helius']['reconciliation']['conflicting_transactions'])
        self.assertTrue(any('discordam' in note for note in coverage.failures))
        self.assert_global_unknown(coverage)
