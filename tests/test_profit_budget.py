"""Reservas por estágio: durabilidade, créditos incertos e reembolso idempotente."""
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from wallets.profit_budget import ProfitBudget, ProfitBudgetError, check_state_space
from wallets.storage import Store

NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
MONTH = '2026-10'
CONFIG = {'api': {'max_credits_per_run': 800, 'monthly_credit_ceiling': 28000},
          'helius': {'enabled': True, 'max_credits_per_run': 300, 'monthly_credit_ceiling': 900000},
          'profit': {'enabled': True}}


class ProfitBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.central = Store(self.root/'central.sqlite'); self.addCleanup(self.central.close)
        self.store = Store(self.root/'experiment.sqlite'); self.addCleanup(self.store.close)
        self.checkpoint = Mock()
        self.budget = ProfitBudget(self.central,self.checkpoint,CONFIG,now=NOW,space_check=lambda *a,**kw: None)
        self.budget.bind_store(self.store)
        no_network = patch('socket.socket.connect',side_effect=AssertionError('Rede proibida'))
        no_network.start();self.addCleanup(no_network.stop)

    def used(self, namespace='birdeye'):
        if namespace == 'birdeye':
            row=self.central.db.execute('SELECT credits FROM budgets WHERE month=?',(MONTH,)).fetchone()
        else:
            row=self.central.db.execute('SELECT credits FROM provider_budgets WHERE namespace=? AND month=?',(namespace,MONTH)).fetchone()
        return row[0] if row else 0

    def test_reservation_is_checkpointed_before_grant_and_only_unused_is_refunded(self):
        observations=[]
        self.checkpoint.side_effect=lambda: observations.append((self.used(),self.store.prepaid['remaining']))
        client=SimpleNamespace(requests=0)
        with self.budget.stage('discovery',birdeye=230,store=self.store,clients={'birdeye':client}):
            self.assertEqual(observations,[(230,0)])
            grant=self.store.prepaid
            self.assertTrue(self.store.reserve_budget(MONTH,30,28000))
            client.requests+=1
        self.assertEqual(self.used(),30)
        self.assertEqual(observations,[(230,0),(30,0)])
        self.assertEqual(grant['remaining'],0)
        self.assertFalse(self.store.reserve_budget(MONTH,1,28000))
        self.assertEqual(self.budget.stats['discovery']['birdeye'],
                         {'reserved':230,'attempted':30,'refunded':200,'requests':1})

    def test_timeout_keeps_attempt_but_refunds_unattempted_remainder(self):
        with self.assertRaises(TimeoutError):
            with self.budget.stage('prefilter',birdeye=210,store=self.store):
                self.assertTrue(self.store.reserve_budget(MONTH,30,28000))
                raise TimeoutError('Resultado remoto incerto.')
        self.assertEqual(self.used(),30)
        self.assertEqual(self.budget.stats['prefilter']['birdeye']['refunded'],180)

    def test_crash_without_context_close_has_no_automatic_refund(self):
        ticket=self.budget._open('deep',{'birdeye':270,'helius':200},self.store,{})
        self.assertTrue(self.store.reserve_budget(MONTH,12,28000))
        self.assertTrue(self.store.reserve_budget(MONTH,100,900000,namespace='helius'))
        with sqlite3.connect(self.central.path) as recovered:
            self.assertEqual(recovered.execute('SELECT credits FROM budgets').fetchone()[0],270)
            self.assertEqual(recovered.execute('SELECT state FROM profit_stage_reservations WHERE id=? AND namespace=?',
                                              (ticket.identity,'birdeye')).fetchone()[0],'reserved')
        self.assertEqual(self.used('helius'),200)

    def test_open_checkpoint_failure_never_grants_network_and_halts(self):
        self.checkpoint.side_effect=RuntimeError('Checkpoint não confirmado')
        body=Mock()
        with self.assertRaises(RuntimeError):
            with self.budget.stage('discovery',birdeye=230,store=self.store):body()
        body.assert_not_called()
        self.assertEqual(self.store.prepaid['remaining'],0)
        with self.assertRaises(ProfitBudgetError):
            with self.budget.stage('prefilter',birdeye=210,store=self.store):pass

    def test_refund_is_idempotent_and_cannot_make_ledger_negative(self):
        ticket=self.budget._open('discovery',{'birdeye':230,'helius':0},self.store,{})
        self.store.reserve_budget(MONTH,30,28000)
        self.budget._close(ticket)
        self.budget._close(ticket)
        self.assertEqual(self.used(),30)
        row=self.central.db.execute('SELECT reserved,attempted,refunded,state FROM profit_stage_reservations WHERE id=? AND namespace=?',
                                    (ticket.identity,'birdeye')).fetchone()
        self.assertEqual(row,(230,30,200,'settled'))
        self.assertEqual(self.checkpoint.call_count,2)

    def test_insufficient_helius_quota_does_not_partially_reserve_birdeye(self):
        self.central.reserve_budget(MONTH,900000,900000,namespace='helius')
        with self.assertRaises(ProfitBudgetError):
            with self.budget.stage('deep',birdeye=270,helius=200,store=self.store):pass
        self.assertEqual(self.used(),0)
        self.assertEqual(self.used('helius'),900000)
        self.checkpoint.assert_not_called()

    def test_space_guard_runs_before_reservations(self):
        failed=Mock(side_effect=ProfitBudgetError('Estado cheio'))
        budget=ProfitBudget(self.central,self.checkpoint,CONFIG,now=NOW,space_check=failed)
        with self.assertRaises(ProfitBudgetError):
            with budget.stage('discovery',birdeye=230,store=self.store):pass
        self.assertEqual(self.used(),0)
        self.assertEqual(self.central.db.execute('SELECT COUNT(*) FROM profit_stage_reservations').fetchone()[0],0)
        self.checkpoint.assert_not_called()

    def test_global_allocation_cap_and_repeated_stage_are_rejected(self):
        with self.budget.stage('discovery',birdeye=230,store=self.store):pass
        with self.assertRaises(ProfitBudgetError):
            with self.budget.stage('discovery',birdeye=230,store=self.store):pass
        with self.assertRaises(ProfitBudgetError):
            with self.budget.stage('too_large',birdeye=600,store=self.store):pass
        self.assertEqual(self.used(),0)

    def test_invalid_remaining_halts_and_never_refunds(self):
        with self.assertRaises(ProfitBudgetError):
            with self.budget.stage('discovery',birdeye=230,store=self.store):
                self.store.prepaid['remaining']=231
        self.assertEqual(self.used(),230)
        with self.assertRaises(ProfitBudgetError):
            with self.budget.stage('prefilter',birdeye=210,store=self.store):pass

    def test_provider_grant_cannot_borrow_from_another_stage(self):
        with self.budget.stage('deep',helius=200,store=self.store):
            self.assertFalse(self.store.reserve_budget(MONTH,1,28000))
            self.assertTrue(self.store.reserve_budget(MONTH,100,900000,namespace='helius'))
            self.assertFalse(self.store.reserve_budget(MONTH,101,900000,namespace='helius'))
        self.assertEqual(self.used('helius'),100)
        self.assertEqual(self.used(),0)

    def test_closed_checkpoint_failure_is_not_reported_as_durable_refund(self):
        self.checkpoint.side_effect=[None,RuntimeError('Confirmação incerta')]
        with self.assertRaises(RuntimeError):
            with self.budget.stage('discovery',birdeye=230,store=self.store):
                self.store.reserve_budget(MONTH,30,28000)
        self.assertEqual(self.budget.stats['discovery']['birdeye']['refunded'],0)
        self.assertEqual(self.budget.stats['discovery']['state'],'checkpoint_or_ledger_failed')
        self.assertFalse(self.store.reserve_budget(MONTH,1,28000))


class SpaceGuardTests(unittest.TestCase):
    def test_oversize_is_rejected_without_even_consulting_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'state.sqlite';path.write_bytes(b'x'*100)
            with patch('wallets.profit_budget.shutil.disk_usage') as usage:
                with self.assertRaises(ProfitBudgetError):check_state_space(path,max_bytes=99)
            usage.assert_not_called()

    def test_disk_margin_covers_temporary_copies(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'state.sqlite';path.write_bytes(b'x'*100)
            with patch('wallets.profit_budget.shutil.disk_usage',return_value=SimpleNamespace(free=1199)):
                with self.assertRaises(ProfitBudgetError):check_state_space(path,max_bytes=1000,minimum_free_bytes=1000)
            with patch('wallets.profit_budget.shutil.disk_usage',return_value=SimpleNamespace(free=1200)):
                self.assertEqual(check_state_space(path,max_bytes=1000,minimum_free_bytes=1000)['state_bytes'],100)
