"""Persistência real SQLite: fechar/reabrir, sem depender do disco de um runner."""
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

from wallets.models import Check, Coverage, Evaluation, Event, REQUIRED_CHECKS, Status, TokenResult, dumps
from wallets.storage import Store, process_lock

NOW = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)


def evaluation(when=NOW, status=Status.PASS):
    coverage = Coverage(when-timedelta(days=30), when-timedelta(minutes=1), when,
                        source='fixture', complete=True, all_tokens=True,
                        opening_inventory_known=True, fees_complete=True)
    checks = [Check(name, status, 'fixture de persistência', {'value': D('1.25')},
                    coverage.start, coverage.end, {}, when, 'test.1') for name in REQUIRED_CHECKS]
    token = TokenResult('mint', realized=D('4.50'), identified_realized=D('4.50'),
                        costs=D('10.1'), fees=D('0.2'), realized_cost=D('10.2'),
                        open_quantity=D('0.00000001'), open_cost=D('0.1'),
                        unrealized=D('-0.04'), unknown_cost_quantity=D('0'),
                        entries=[D('10.1')], holding_seconds=[10.0],
                        weekly={'2026-10-26': D('4.5')}, reliable=True)
    return Evaluation('wallet', when, 'test.1', coverage, checks,
                      {'decimal': D('1.25'), 'data': ['fixture']}, [token], ['mint'], 'fixture')


def event(identity='tx:0', when=NOW, **changes):
    values = dict(id=identity, wallet='wallet', token='mint', timestamp=when,
                  kind='buy', quantity=D('0.00000001'), usd_value=D('1.23456789'),
                  fee_usd=None, tx_hash='tx', source='fixture')
    values.update(changes)
    return Event(**values)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)/'nested'/'wallets.sqlite'

    def tearDown(self):
        self.directory.cleanup()

    def test_events_survive_reopen_with_decimal_and_dedup(self):
        original = event()
        with Store(self.path) as store:
            store.put_events([original, original])
        with Store(self.path) as store:
            self.assertEqual(store.get_events('wallet', NOW-timedelta(days=1), NOW), [original])
            store.put_events([original])
            self.assertEqual(len(store.get_events('wallet', NOW-timedelta(days=1), NOW)), 1)

    def test_conflicting_duplicate_rejected_transactionally(self):
        with Store(self.path) as store:
            store.put_events([event()])
            with self.assertRaises(ValueError):
                store.put_events([event('new'), event(quantity=D('9'))])
            self.assertEqual(store.get_events('wallet', NOW-timedelta(days=1), NOW), [event()])

    def test_multi_step_can_only_be_upgraded_to_less_certain(self):
        with Store(self.path) as store:
            store.put_events([event()])
            store.put_events([event(multi_step=True)])
            self.assertTrue(store.get_events('wallet', NOW-timedelta(days=1), NOW)[0].multi_step)
            with self.assertRaises(ValueError):
                store.put_events([event(multi_step=False)])

    def test_future_events_not_returned_to_past_evaluation(self):
        with Store(self.path) as store:
            store.put_events([event('past', NOW-timedelta(seconds=1)),
                              event('boundary', NOW),
                              event('future', NOW+timedelta(microseconds=1))])
            got = store.get_events('wallet', NOW-timedelta(days=1), NOW)
            self.assertEqual({item.id for item in got}, {'past', 'boundary'})

    def test_discovery_dedup_preserves_first_seen_and_origins(self):
        with Store(self.path) as store:
            store.record_discovery('wallet', 'mint1', NOW)
            store.record_discovery('wallet', 'mint1', NOW+timedelta(days=1))
            store.record_discovery('wallet', 'mint2', NOW+timedelta(hours=1))
        with Store(self.path) as store:
            self.assertEqual(store.discovery_tokens('wallet'), ['mint1', 'mint2'])
            self.assertEqual(store.discoveries()[0]['discovered_at'], NOW.isoformat())
            self.assertEqual(store.wallets(), ['wallet'])

    def test_cursor_and_cache_survive_reopen_and_cache_expires(self):
        with Store(self.path) as store:
            store.set_cursor('wallet:seek', '100')
            store.cache_put('query', {'data': [1, 2]}, NOW+timedelta(seconds=1))
        with Store(self.path) as store:
            self.assertEqual(store.get_cursor('wallet:seek'), '100')
            self.assertEqual(store.cache_get('query', NOW), {'data': [1, 2]})
            self.assertIsNone(store.cache_get('query', NOW+timedelta(seconds=1)))
            self.assertIsNone(store.cache_get('missing', NOW))

    def test_monthly_quota_survives_reopen_and_month_rollover(self):
        with Store(self.path) as store:
            self.assertTrue(store.reserve_budget('2026-10', 70, 100))
        with Store(self.path) as store:
            self.assertFalse(store.reserve_budget('2026-10', 31, 100))
            self.assertTrue(store.reserve_budget('2026-10', 30, 100))
            self.assertTrue(store.reserve_budget('2026-11', 100, 100))
            with self.assertRaises(ValueError):
                store.reserve_budget('2026-11', -1, 100)

    def test_prepay_is_committed_before_checkpoint_and_spending_not_double_charged(self):
        observations = []
        def checkpoint():
            with sqlite3.connect(self.path) as external:
                observations.append(external.execute('SELECT credits FROM budgets WHERE month=?', ('2026-10',)).fetchone()[0])
        with Store(self.path) as store:
            self.assertTrue(store.prepay_run('2026-10', 80, 100, checkpoint))
            self.assertEqual(observations, [80])
            self.assertTrue(store.reserve_budget('2026-10', 30, 100))
            self.assertTrue(store.reserve_budget('2026-10', 50, 100))
            self.assertFalse(store.reserve_budget('2026-10', 1, 100))
            self.assertFalse(store.reserve_budget('2026-11', 1, 100))
        with Store(self.path) as store:
            self.assertTrue(store.reserve_budget('2026-10', 20, 100))
            self.assertFalse(store.reserve_budget('2026-10', 1, 100))

    def test_failed_durable_prepay_does_not_allow_prepaid_requests(self):
        def failure():
            raise OSError('simulação de falha remota')
        with Store(self.path) as store:
            with self.assertRaises(OSError):
                store.prepay_run('2026-10', 100, 100, failure)
            self.assertIsNone(store.prepaid)
            self.assertFalse(store.reserve_budget('2026-10', 1, 100))
        with Store(self.path) as store:
            self.assertFalse(store.reserve_budget('2026-10', 1, 100))

    def test_snapshots_roundtrip_canonical_content_and_never_change(self):
        original = evaluation()
        with Store(self.path) as store:
            identity = store.save_evaluation(original)
            before = dumps(store.get_evaluation(identity).to_dict())
            original.metrics['data'].append('mutação local')
            self.assertEqual(dumps(store.get_evaluation(identity).to_dict()), before)
            for sql in ('UPDATE evaluations SET payload=? WHERE id=?', 'DELETE FROM evaluations WHERE id=?'):
                with self.subTest(sql=sql), self.assertRaisesRegex(sqlite3.IntegrityError, 'immutable'):
                    if sql.startswith('UPDATE'):
                        store.db.execute(sql, ('{}', identity))
                    else:
                        store.db.execute(sql, (identity,))
        with Store(self.path) as store:
            recovered = store.get_evaluation(identity)
            self.assertEqual(dumps(recovered.to_dict()), before)
            self.assertEqual(recovered.tokens[0].realized_cost, D('10.2'))
            self.assertEqual(recovered.tokens[0].entries, [D('10.1')])
            self.assertEqual(recovered.tokens[0].weekly, {'2026-10-26': D('4.5')})
            self.assertEqual(store.latest_id('wallet'), identity)

    def test_notification_reservation_survives_restart_and_blocks_duplicate(self):
        selected = evaluation()
        with Store(self.path) as store:
            store.save_evaluation(selected)
            self.assertTrue(store.reserve_notification(selected, NOW))
        with Store(self.path) as store:
            self.assertFalse(store.reserve_notification(selected, NOW+timedelta(seconds=1)))
            self.assertEqual(store.last_notification('wallet')['state'], 'pending')
            store.finish_notification(selected.id, 'uncertain')
        with Store(self.path) as store:
            self.assertEqual(store.last_notification('wallet')['state'], 'uncertain')
            self.assertFalse(store.reserve_notification(selected, NOW+timedelta(days=30)))

    def test_old_approval_cannot_replace_later_rejection_as_latest(self):
        old = evaluation(NOW-timedelta(hours=1), Status.PASS)
        latest = evaluation(NOW, Status.FAIL)
        with Store(self.path) as store:
            store.save_evaluation(old)
            store.save_evaluation(latest)
            try:
                store.save_evaluation(old)
            except ValueError:
                pass  # Rejeitar ou manter latest são ambas implementações seguras.
            self.assertEqual(store.latest_id('wallet'), latest.id)

    def test_equal_timestamp_downgrade_cannot_be_replaced_by_approval(self):
        approved = evaluation(NOW, Status.PASS)
        rejected = evaluation(NOW, Status.FAIL)
        with Store(self.path) as store:
            store.save_evaluation(approved)
            store.save_evaluation(rejected)
            self.assertEqual(store.latest_id('wallet'), rejected.id)
            try:
                store.save_evaluation(approved)
            except ValueError:
                pass
            self.assertEqual(store.latest_id('wallet'), rejected.id)

    def test_equal_timestamp_indeterminate_cannot_be_replaced_by_approval(self):
        approved = evaluation(NOW, Status.PASS)
        inconclusive = evaluation(NOW, Status.UNKNOWN)
        with Store(self.path) as store:
            store.save_evaluation(inconclusive)
            try:
                store.save_evaluation(approved)
            except ValueError:
                pass
            self.assertEqual(store.latest_id('wallet'), inconclusive.id)

    def test_exclusive_process_lock_prevents_parallel_writers(self):
        with process_lock(self.path):
            with self.assertRaises(BlockingIOError):
                with process_lock(self.path):
                    self.fail('Segundo escritor não devia adquirir o lock')
        with process_lock(self.path):
            pass

    def test_unsupported_schema_never_silently_resets_state(self):
        self.path.parent.mkdir(parents=True)
        with sqlite3.connect(self.path) as con:
            con.execute('PRAGMA user_version=999')
        with self.assertRaisesRegex(ValueError, 'não suportada'):
            Store(self.path)
        with sqlite3.connect(self.path) as con:
            self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0], 999)

    def test_discovery_labels_preserved_with_observation_cutoff(self):
        # O estado guarda evidência do fornecedor sem a transformar em certificação.
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from datetime import datetime,timedelta,timezone
        from wallets.storage import Store
        when=datetime(2026,10,28,tzinfo=timezone.utc)
        with TemporaryDirectory() as directory,Store(Path(directory)/'labels.sqlite') as store:
            store.record_discovery('wallet','token',when,{'tags':['sniper']})
            store.record_discovery('wallet','token',when+timedelta(days=1),{'tags':['smart_trader']})
            observations=store.discovery_evidence('wallet',when)
            self.assertEqual(len(observations),1)
            self.assertEqual(observations[0]['evidence']['tags'],['sniper'])


if __name__ == '__main__':
    unittest.main()
