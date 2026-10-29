#!/usr/bin/env python3
"""Experiência de sete dias com estado isolado e quota mensal partilhada.

As reservas são confirmadas no cofre de produção ANTES de qualquer pedido. Só
os contadores de quota de produção mudam; eventos e avaliações experimentais
ficam num segundo cofre cifrado. Este programa não disponibiliza envio Telegram.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from wallets import cli
from wallets.collection import valid_address
from wallets.models import iso, utcnow
from wallets.storage import Store, process_lock, decode_evaluation
from state_vault import GitHub, TAG, Vault, VaultError

EXPERIMENT_TAG = 'wallets-state-recent-7d'
EXPERIMENT_BRANCH = 'feat/recent-7d'


class ExperimentError(RuntimeError):
    pass


class ExperimentGitHub(GitHub):
    """O mesmo repositório, exclusivamente no cofre experimental fixo."""
    def command(self, args, *, allow_missing=False):
        mapped = [EXPERIMENT_TAG if value == TAG else value.replace(
            '/releases/tags/' + TAG, '/releases/tags/' + EXPERIMENT_TAG)
            for value in args]
        return super().command(mapped, allow_missing=allow_missing)

    def create(self):
        self.command(['release', 'create', EXPERIMENT_TAG, '--repo', self.repository,
                      '--title', 'Estado cifrado da experiência de sete dias',
                      '--notes', 'Experiência isolada; sem Telegram. Quota reservada no cofre principal.',
                      '--target', EXPERIMENT_BRANCH])


class PrepaidStoreFactory:
    """Uma única utilização das reservas que já foram guardadas remotamente."""
    def __init__(self, path, grants):
        self.path = Path(path).resolve()
        self.grants = deepcopy(grants)
        self.used = False

    def __call__(self, path):
        if self.used or Path(path).resolve() != self.path:
            raise ExperimentError('Reserva experimental inválida ou já utilizada.')
        self.used = True
        store = Store(path)
        store.prepaid = deepcopy(self.grants['birdeye'])
        if 'helius' in self.grants:
            store._provider_prepaid['helius'] = deepcopy(self.grants['helius'])
        return store


def _reserve_and_copy(production, destination, config, vault, now, *, copy_state):
    grants = {}
    with Store(production) as source:
        for namespace, settings in (('birdeye', config['api']), ('helius', config['helius'])):
            if namespace == 'helius' and not settings.get('enabled', False):
                continue
            if not source.prepay_run(now.strftime('%Y-%m'), settings['max_credits_per_run'],
                                     settings['monthly_credit_ceiling'],
                                     lambda: vault.save(production), namespace=namespace):
                raise ExperimentError('Quota mensal partilhada insuficiente; experiência adiada sem consultas.')
            reservation = source.prepaid if namespace == 'birdeye' else source._provider_prepaid[namespace]
            grants[namespace] = deepcopy(reservation)
        if copy_state:
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            with sqlite3.connect(destination) as target:
                source.db.backup(target)
                # A geração pertence ao cofre principal, não ao novo cofre.
                target.execute('DELETE FROM vault_checkpoint')
        else:
            with sqlite3.connect(destination) as target:
                # A reserva central é a única contabilização mensal. A cópia
                # experimental recebe o total, sem voltar a incrementar o ledger.
                target.execute('DELETE FROM budgets')
                target.executemany('INSERT INTO budgets VALUES(?,?)',
                                   source.db.execute('SELECT month,credits FROM budgets').fetchall())
                target.execute('DELETE FROM provider_budgets')
                target.executemany('INSERT INTO provider_budgets VALUES(?,?,?)',
                                   source.db.execute('SELECT namespace,month,credits FROM provider_budgets').fetchall())
    os.chmod(destination, 0o600)
    return grants


def _public_summary(db, output, started_at):
    """Só contagens e tokens públicos; nunca endereços de carteiras ou segredos."""
    report_path = Path(output) / 'report.json'
    report = json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else {'evaluations': []}
    evaluations = report.get('evaluations', [])
    checks = {}
    for evaluation in evaluations:
        selection = evaluation.get('metrics', {}).get('selection', {})
        for check in selection.get('checks', []):
            name, status = check['name'], check['status']
            checks.setdefault(name, {})[status] = checks.setdefault(name, {}).get(status, 0) + 1
    tokens = []
    with Store(db) as store:
        for token, at, payload in store.db.execute(
                'SELECT token,at,payload FROM token_selections WHERE at>=? ORDER BY at,token',
                (iso(started_at),)):
            if not valid_address(token):
                continue
            data = json.loads(payload)
            tokens.append({'address': token, 'symbol': str(data.get('symbol', ''))[:64],
                           'name': str(data.get('name', ''))[:128], 'selected_at': at,
                           'recent_listing_time': data.get('recent_listing_time'),
                           'volume_24h_usd': str(data.get('volume_24h_usd', '')),
                           'liquidity_usd': str(data.get('liquidity', ''))})
    selected = 0
    for evaluation in evaluations:
        try:
            selected += bool(decode_evaluation(deepcopy(evaluation)).observed_eligible)
        except (KeyError, TypeError, ValueError, AttributeError):
            pass
    summary = {'profile': 'recent-7d', 'started_at': iso(started_at),
               'evaluated_wallets': len(evaluations), 'observed_selected': selected,
               'telegram_sent': 0, 'checks': checks, 'tokens': tokens,
               'scope': 'Sete dias anteriores à pesquisa; seleção observada, sem aprovação integral.'}
    path = Path(output) / 'experiment-summary.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    return summary


def run_experiment(production, experiment, output, config, production_vault, experiment_vault,
                   *, now=None, runner=None):
    """Orquestração testável: reserva central, estado isolado, execução sem envio."""
    production, experiment, output = Path(production), Path(experiment), Path(output)
    now = now or utcnow()
    runner = runner or cli.run
    if (production.resolve() == experiment.resolve() or not production.is_file()
            or config.get('recent', {}).get('enabled') is not True
            or config.get('selection', {}).get('notifications', 'strict') != 'strict'):
        raise ExperimentError('Configuração ou isolamento da experiência inválidos.')
    if experiment.exists():
        raise ExperimentError('Já existe estado experimental local; não substituir uma base possivelmente aberta.')
    experiment.parent.mkdir(parents=True, exist_ok=True)
    with process_lock(production):
        existing = experiment_vault.github.release()
        if existing is not None:
            experiment_vault.restore(experiment)
        grants = _reserve_and_copy(production, experiment, config, production_vault, now,
                                   copy_state=existing is None)
        if existing is None:
            experiment_vault.github.create()
            experiment_vault.save(experiment, initializing=True)
        else:
            experiment_vault.save(experiment)
        factory = PrepaidStoreFactory(experiment, grants)
        with sqlite3.connect(experiment) as connection:
            prior_notifications = connection.execute('SELECT COUNT(*) FROM notifications').fetchone()[0]
        args = SimpleNamespace(db=str(experiment), output=str(output), wallet=[], token=[],
                               checkpoint_command=None, notify=False)
        # checkpoint_override é o cofre experimental; não se desliga a proteção
        # GitHub Actions nem se passa um comando de reserva outra vez ao CLI.
        try:
            runner(args, config, store_factory=factory,
                   checkpoint_override=lambda: experiment_vault.save(experiment))
        finally:
            if factory.used:
                experiment_vault.save(experiment)
        if not factory.used:
            raise ExperimentError('A execução não utilizou a reserva experimental.')
        with sqlite3.connect(experiment) as connection:
            count = connection.execute('SELECT COUNT(*) FROM notifications').fetchone()[0]
        if count != prior_notifications:
            raise ExperimentError('A experiência modificou notificações; resultado recusado.')
        return _public_summary(experiment, output, now)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Experiência isolada de sete dias, sem Telegram')
    parser.add_argument('--production-db', default='state/production.sqlite')
    parser.add_argument('--db', default='state/recent-7d.sqlite')
    parser.add_argument('--output', default='reports/recent-7d')
    parser.add_argument('--config', default='config.recent-7d.toml')
    args = parser.parse_args(argv)
    try:
        for name in ('STATE_ENCRYPTION_KEY', 'BIRDEYE_API_KEY', 'HELIUS_API_KEY'):
            if not os.environ.get(name):
                raise ExperimentError('Falta um segredo indispensável à experiência em Actions.')
        config = cli.load_config(args.config)
        key = os.environ['STATE_ENCRYPTION_KEY']
        result = run_experiment(args.production_db, args.db, args.output, config,
                                Vault(key), Vault(key, ExperimentGitHub()))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    except (ExperimentError, VaultError, OSError, ValueError, sqlite3.DatabaseError) as exc:
        # Mensagens livres da rede e valores de autenticação nunca são emitidos.
        print('Experiência interrompida (' + type(exc).__name__ + '); sem Telegram. Consultar estado cifrado.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
