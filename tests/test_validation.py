from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import sqlite3
import tempfile
import unittest
from test_checks import NOW,run_case,full_case
from wallets.storage import Store
from wallets.validation import followup

class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.selection=run_case()
        self.event=replace(full_case()[0][0],timestamp=NOW+timedelta(days=1))
    def test_observed_future_not_retroactive_profit(self):
        result=followup(self.selection,[self.event],NOW+timedelta(days=2))
        self.assertEqual(result['event_count'],1)
        self.assertIsNone(result['observed_wallet_pnl'])
        self.assertIsNone(result['follower_pnl'])
        self.assertFalse(result['real_performance_validated'])
        self.assertFalse(result['simulation'])
        self.assertEqual(result['source'],'fixture')
    def test_selection_history_cannot_be_forward_observation(self):
        for stamp in (NOW,NOW-timedelta(days=1)):
            with self.assertRaises(ValueError):followup(self.selection,[replace(self.event,timestamp=stamp)],NOW+timedelta(days=2))
    def test_information_future_of_asof_rejected(self):
        with self.assertRaises(ValueError):followup(self.selection,[self.event],NOW+timedelta(hours=1))
        with self.assertRaises(ValueError):followup(self.selection,[],NOW)
    def test_other_wallet_and_conflicting_duplicates_rejected(self):
        with self.assertRaises(ValueError):followup(self.selection,[replace(self.event,wallet='other')],NOW+timedelta(days=2))
        with self.assertRaises(ValueError):followup(self.selection,[self.event,replace(self.event,quantity=self.event.quantity+1)],NOW+timedelta(days=2))
    def test_dedup_and_no_data_never_prove_real_performance(self):
        self.assertEqual(followup(self.selection,[self.event,self.event],NOW+timedelta(days=2))['event_count'],1)
        result=followup(replace(self.selection,source='birdeye'),[],NOW+timedelta(days=2))
        self.assertEqual(result['status'],'INDETERMINADA')
        self.assertFalse(result['real_performance_validated'])
    def test_observations_append_only_and_do_not_mutate_selection(self):
        with tempfile.TemporaryDirectory() as directory,Store(Path(directory)/'db.sqlite') as store:
            store.save_evaluation(self.selection)
            original=store.get_evaluation(self.selection.id).to_dict()
            observation=followup(self.selection,[self.event],NOW+timedelta(days=2))
            observation_id=store.save_followup(observation)
            self.assertEqual(store.save_followup(observation),observation_id)
            self.assertEqual(store.get_evaluation(self.selection.id).to_dict(),original)
            with self.assertRaises(sqlite3.IntegrityError):store.db.execute('UPDATE followups SET payload=? WHERE id=?',('{}',observation_id))
