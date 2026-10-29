"""Descoberta Helius recente: janela, origem e isolamento de cursores antigos."""
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest

from test_helius_collection import Client, NOW, TOKEN, WALLET, normalize_fixture, tx
from wallets.helius_collection import HeliusHistory
from wallets.models import dumps, iso
from wallets.storage import Store


class RecentHeliusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name)/'state.sqlite')
        self.addCleanup(self.store.close)
        self.start = NOW-timedelta(days=7)

    def history(self, client):
        return HeliusHistory(client,self.store,{'helius':{'page_size':1000,'max_pages':5},
                             'recent':{'enabled':True}},clock=lambda:NOW,normalizer=normalize_fixture)

    def test_implicit_recent_discovery_is_bounded_and_records_the_profile(self):
        client=Client([{'data':[tx()], 'paginationToken':None}])
        result=self.history(client).discover_first_observed(TOKEN,NOW)
        query=client.calls[0][1][1]
        self.assertEqual(query['filters']['blockTime'],{'gte':int(self.start.timestamp()),'lte':int(NOW.timestamp())})
        self.assertEqual(query['limit'],1000)
        self.assertEqual(result['wallets'],[WALLET])
        evidence=self.store.discovery_evidence(WALLET)[0]['evidence']
        self.assertEqual(evidence['profile'],'recent-7d')
        self.assertEqual(evidence['time_frame'],'7d')
        self.assertEqual(evidence['window_start'],iso(self.start))

    def test_recent_scope_never_resumes_the_legacy_epoch_cursor(self):
        legacy=HeliusHistory.cursor_key(TOKEN,True,False)
        self.store.set_cursor(legacy,dumps({'schema':1,'covered_start':None,'covered_end':None,
            'pending':{'start':'1970-01-01T00:00:00+00:00','end':iso(NOW),'next':'100:1','seen':['100:1']}}))
        client=Client([{'data':[tx()], 'paginationToken':None}])
        self.history(client).discover_first_observed(TOKEN,NOW,start=self.start)
        query=client.calls[0][1][1]
        self.assertNotIn('paginationToken',query)
        self.assertEqual(query['filters']['blockTime']['gte'],int(self.start.timestamp()))
        self.assertTrue(self.store.get_cursor(legacy))
        self.assertTrue(self.store.get_cursor(legacy+':recent-7d'))

    def test_discovery_rejects_an_eight_day_request_before_network(self):
        client=Client()
        with self.assertRaises(ValueError):
            self.history(client).discover_first_observed(TOKEN,NOW,start=NOW-timedelta(days=8))
        self.assertEqual(client.calls,[])

    def test_trade_at_the_cutoff_is_not_a_recent_discovery(self):
        client=Client([{'data':[tx(at=NOW)], 'paginationToken':None}])
        result=self.history(client).discover_first_observed(TOKEN,NOW,start=self.start)
        self.assertEqual(result['wallets'],[])
        self.assertEqual(self.store.discoveries(),[])
