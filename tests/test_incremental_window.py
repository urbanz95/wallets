"""Aumentar a janela não pode saltar um período nunca recolhido."""
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest

from test_collection import FakeClient, NOW, WALLET, trade
from wallets.collection import Collector
from wallets.models import iso
from wallets.storage import Store


class IncrementalWindowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'state.sqlite')
        self.addCleanup(self.store.close)

    def collect(self, client, days, max_pages=5):
        collector = Collector(client, self.store, {'api': {'max_pages': max_pages}}, clock=lambda: NOW)
        return collector.collect(WALLET, NOW-timedelta(days=days), NOW)

    @staticmethod
    def trader_query(client):
        return next(call[2] for call in client.calls if call[1]=='/trader/txs/seek_by_time')

    def test_expanding_thirty_to_ninety_days_backfills_older_period(self):
        old=trade(block_unix_time=int((NOW-timedelta(days=60)).timestamp()))
        recent=trade(tx_hash='recent')
        events,_,_=self.collect(FakeClient([old,recent]),30)
        self.assertEqual(len(events),2)
        events,_,_=self.collect(FakeClient([old,recent]),90)
        self.assertEqual({e.tx_hash for e in events},{'hash','recent'})
        self.assertEqual(self.store.get_cursor('trader-start:'+WALLET),iso(NOW-timedelta(days=90)))

    def test_same_or_shorter_window_deduplicates_and_accepts_delayed_indexing(self):
        self.collect(FakeClient([trade()]),90)
        delayed=trade(tx_hash='delayed-index',block_unix_time=int((NOW-timedelta(days=2)).timestamp()))
        events,_,_=self.collect(FakeClient([trade(),delayed]),30)
        self.assertEqual(len(events),4)
        self.assertEqual({e.tx_hash for e in events},{'hash','delayed-index'})
        self.assertEqual(self.store.get_cursor('trader-start:'+WALLET),iso(NOW-timedelta(days=90)))

    def test_legacy_end_cursor_without_start_must_refetch_requested_window(self):
        self.store.set_cursor('trader:'+WALLET,iso(NOW))
        old=trade(block_unix_time=int((NOW-timedelta(days=60)).timestamp()))
        events,_,_=self.collect(FakeClient([old]),90)
        self.assertEqual(len(events),2)
        self.assertEqual(self.store.get_cursor('trader-start:'+WALLET),iso(NOW-timedelta(days=90)))

    def test_truncated_backfill_keeps_previous_covered_start(self):
        self.collect(FakeClient(), 30)
        old_start=self.store.get_cursor('trader-start:'+WALLET)
        # hasNext mesmo sem itens não demonstra cobertura completa.
        _, coverage, _=self.collect(FakeClient(more=True), 90, max_pages=1)
        self.assertTrue(coverage.truncated)
        self.assertEqual(self.store.get_cursor('trader-start:'+WALLET), old_start)
        self.assertEqual(self.store.get_cursor('trader:'+WALLET), iso(NOW))
