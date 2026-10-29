"""Quotas partilhadas e isolamento experimental, sem credenciais ou rede."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet

SCRIPTS = Path(__file__).parents[1] / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import recent_experiment as module
from wallets.storage import Store

NOW = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
WALLET = 'A' * 44
TOKEN = 'B' * 44


class FakeGitHub:
    def __init__(self):
        self.data = None
        self.files = {}
        self.fail_upload = False

    def release(self):
        return deepcopy(self.data)

    def create(self):
        self.data = {'assets': []}

    def upload(self, path):
        if self.fail_upload:
            raise module.VaultError('Falha de upload simulada.')
        number = len(self.files) + 1
        content = Path(path).read_bytes()
        self.files[number] = content
        self.data['assets'].append({'name': Path(path).name, 'id': number, 'size': len(content)})

    def download(self, asset):
        return self.files[asset['id']]


class RecentExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.production = self.root / 'production.sqlite'
        self.experiment = self.root / 'experiment.sqlite'
        self.output = self.root / 'reports'
        self.production_github, self.experiment_github = FakeGitHub(), FakeGitHub()
        key = Fernet.generate_key()
        self.production_vault = module.Vault(key, self.production_github)
        self.experiment_vault = module.Vault(key, self.experiment_github)
        self.production_vault.init(self.production)
        with Store(self.production) as store:
            store.record_discovery(WALLET, TOKEN, NOW, {'source': 'fixture'})
        self.production_vault.save(self.production)
        self.config = {
            'recent': {'enabled': True}, 'selection': {'notifications': 'strict'},
            'api': {'max_credits_per_run': 800, 'monthly_credit_ceiling': 28000},
            'helius': {'enabled': True, 'max_credits_per_run': 5000,
                       'monthly_credit_ceiling': 900000},
        }
        for target in ('socket.socket.connect', 'urllib.request.urlopen',
                       'urllib.request.OpenerDirector.open'):
            blocker = patch(target, side_effect=AssertionError('Rede proibida no teste'))
            blocker.start()
            self.addCleanup(blocker.stop)

    def run_experiment(self, runner, *, path=None):
        return module.run_experiment(self.production, path or self.experiment, self.output,
                                     self.config, self.production_vault, self.experiment_vault,
                                     now=NOW, runner=runner)

    def fake_run(self, args, config, *, store_factory, checkpoint_override):
        self.assertFalse(args.notify)
        self.assertIsNone(args.checkpoint_command)
        with store_factory(args.db) as store:
            self.assertTrue(store.reserve_budget('2026-10', 12, 28000))
            self.assertTrue(store.reserve_budget('2026-10', 10, 900000, namespace='helius'))
            store.set_cursor('experimental-only', 'progress-1')
            store.record_token_selection(TOKEN, NOW, {
                'name': 'Fixture experimental', 'symbol': 'FIX', 'volume_24h_usd': '100000',
                'liquidity': '150000', 'recent_listing_time': int(NOW.timestamp())})
        Path(args.output).mkdir(parents=True, exist_ok=True)
        (Path(args.output) / 'report.json').write_text(json.dumps({'evaluations': []}))
        checkpoint_override()

    def test_one_shared_reservation_per_provider_and_isolated_state(self):
        summary = self.run_experiment(self.fake_run)
        with Store(self.production) as source, Store(self.experiment) as target:
            self.assertEqual(source.db.execute('SELECT credits FROM budgets').fetchone()[0], 800)
            self.assertEqual(source.db.execute('SELECT credits FROM provider_budgets').fetchone()[0], 5000)
            self.assertEqual(target.db.execute('SELECT credits FROM budgets').fetchone()[0], 800)
            self.assertEqual(target.db.execute('SELECT credits FROM provider_budgets').fetchone()[0], 5000)
            self.assertIsNone(source.get_cursor('experimental-only'))
            self.assertEqual(target.get_cursor('experimental-only'), 'progress-1')
            self.assertEqual(source.db.execute('SELECT COUNT(*) FROM evaluations').fetchone()[0], 0)
        self.assertEqual(self.experiment.stat().st_mode & 0o777, 0o600)
        self.assertEqual(summary['telegram_sent'], 0)
        self.assertEqual(summary['tokens'][0]['address'], TOKEN)
        self.assertNotIn(WALLET, json.dumps(summary))
        self.assertNotEqual(self.production_github.files, self.experiment_github.files)
        for encrypted in self.experiment_github.files.values():
            self.assertNotIn(WALLET.encode(), encrypted)
            self.assertNotIn(b'progress-1', encrypted)

    def test_monthly_limit_stops_before_runner_and_no_experiment_creation(self):
        with Store(self.production) as store:
            self.assertTrue(store.reserve_budget('2026-10', 28000, 28000))
        self.production_vault.save(self.production)
        runner = Mock()
        with self.assertRaises(module.ExperimentError):
            self.run_experiment(runner)
        runner.assert_not_called()
        self.assertFalse(self.experiment.exists())
        self.assertIsNone(self.experiment_github.data)

    def test_second_provider_limit_keeps_first_reservation_without_queries(self):
        with Store(self.production) as store:
            store.reserve_budget('2026-10', 900000, 900000, namespace='helius')
        self.production_vault.save(self.production)
        runner = Mock()
        with self.assertRaises(module.ExperimentError):
            self.run_experiment(runner)
        runner.assert_not_called()
        with Store(self.production) as store:
            self.assertEqual(store.db.execute('SELECT credits FROM budgets').fetchone()[0], 800)
        self.assertFalse(self.experiment.exists())

    def test_failed_durable_checkpoint_prevents_any_market_queries(self):
        self.production_github.fail_upload = True
        runner = Mock()
        with self.assertRaises(module.VaultError):
            self.run_experiment(runner)
        runner.assert_not_called()
        self.assertFalse(self.experiment.exists())
        self.assertTrue(self.production.with_suffix('.sqlite.pending.enc').exists())

    def test_restore_resumes_experimental_progress_without_importing_into_production(self):
        self.run_experiment(self.fake_run)
        second = self.root / 'resumed.sqlite'
        observed = []
        def resume(args, config, *, store_factory, checkpoint_override):
            with store_factory(args.db) as store:
                observed.append(store.get_cursor('experimental-only'))
                self.assertEqual(store.prepaid['remaining'], 800)
                self.assertEqual(store._provider_prepaid['helius']['remaining'], 5000)
            checkpoint_override()
        self.run_experiment(resume, path=second)
        self.assertEqual(observed, ['progress-1'])
        with Store(self.production) as store:
            self.assertIsNone(store.get_cursor('experimental-only'))
            self.assertEqual(store.db.execute('SELECT credits FROM budgets').fetchone()[0], 1600)
            self.assertEqual(store.db.execute('SELECT credits FROM provider_budgets').fetchone()[0], 10000)

    def test_crash_keeps_quota_and_saves_experimental_progress(self):
        def crash(args, config, *, store_factory, checkpoint_override):
            with store_factory(args.db) as store:
                store.set_cursor('before-crash', 'saved')
            raise RuntimeError('Falha simulada após gravar progresso.')
        with self.assertRaises(RuntimeError):
            self.run_experiment(crash)
        restored = self.root / 'after-crash.sqlite'
        self.experiment_vault.restore(restored)
        with Store(restored) as store:
            self.assertEqual(store.get_cursor('before-crash'), 'saved')
        with Store(self.production) as store:
            self.assertEqual(store.db.execute('SELECT credits FROM budgets').fetchone()[0], 800)
            self.assertIsNone(store.get_cursor('before-crash'))

    def test_prepaid_factory_cannot_reuse_or_change_the_database(self):
        self.run_experiment(self.fake_run)
        grants = {'birdeye': {'month': '2026-10', 'remaining': 800}}
        factory = module.PrepaidStoreFactory(self.experiment, grants)
        with self.assertRaises(module.ExperimentError):
            factory(self.production)
        with factory(self.experiment) as store:
            self.assertTrue(store.reserve_budget('2026-10', 800, 28000))
            self.assertFalse(store.reserve_budget('2026-10', 1, 28000))
        with self.assertRaises(module.ExperimentError):
            factory(self.experiment)

    def test_wrong_profile_or_same_database_is_rejected_without_reservations(self):
        runner = Mock()
        self.config['recent']['enabled'] = False
        with self.assertRaises(module.ExperimentError):
            self.run_experiment(runner)
        self.config['recent']['enabled'] = True
        with self.assertRaises(module.ExperimentError):
            self.run_experiment(runner, path=self.production)
        runner.assert_not_called()
        with Store(self.production) as store:
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM budgets').fetchone()[0], 0)

    def test_experimental_github_maps_only_the_fixed_release_tag(self):
        github = module.ExperimentGitHub()
        with patch.object(module.GitHub, 'command', return_value=b'{}') as command:
            github.command(['api', 'repos/urbanz95/wallets/releases/tags/wallets-state'])
            self.assertEqual(command.call_args.args[0],
                             ['api', 'repos/urbanz95/wallets/releases/tags/wallets-state-recent-7d'])
            github.create()
            self.assertIn('feat/recent-7d', command.call_args.args[0])
            self.assertIn('wallets-state-recent-7d', command.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
