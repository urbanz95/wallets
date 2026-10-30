#!/usr/bin/env python3
"""Ranking privado por evidência, quotas por estágio e nenhuma entrega Telegram."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sqlite3

from wallets import cli
from wallets.models import iso, utcnow
from wallets.profit_budget import ProfitBudget, ProfitBudgetError, check_state_space
from wallets.storage import Store, process_lock
from state_vault import GitHub, TAG, Vault, VaultError

EXPERIMENT_TAG = 'wallets-state-profit-ranking'
EXPERIMENT_BRANCH = 'feat/profit-evidence-funnel'
EXPERIMENT_MAX_STATE_BYTES = 512 * 1024 * 1024
ADDRESS = re.compile(r'(?<![1-9A-HJ-NP-Za-km-z])[1-9A-HJ-NP-Za-km-z]{32,44}(?![1-9A-HJ-NP-Za-km-z])')


class ExperimentError(RuntimeError):
    pass


class ExperimentGitHub(GitHub):
    def command(self, args, *, allow_missing=False):
        mapped = [EXPERIMENT_TAG if value == TAG else value.replace(
            '/releases/tags/'+TAG, '/releases/tags/'+EXPERIMENT_TAG) for value in args]
        return super().command(mapped, allow_missing=allow_missing)

    def create(self):
        self.command(['release', 'create', EXPERIMENT_TAG, '--repo', self.repository,
                      '--title', 'Estado cifrado da experiência de ranking por evidência',
                      '--notes', 'Ranking privado; sem Telegram; quotas partilhadas por estágio.',
                      '--target', EXPERIMENT_BRANCH])


def _numeric_tree(value, depth=0):
    if depth > 4 or not isinstance(value, dict):
        return {}
    result = {}
    for key, number in value.items():
        if (not isinstance(key, str) or not re.fullmatch('[a-z][a-z0-9_]{0,63}', key)
                or ADDRESS.search(key)):
            continue
        if type(number) is int and number >= 0:
            result[key] = number
        elif isinstance(number, dict):
            nested = _numeric_tree(number, depth+1)
            if nested:
                result[key] = nested
    return result


def public_summary(result, budget, started_at):
    """A saída pública aceita só contagens; listas/endereços não são copiados."""
    if not isinstance(result, dict):
        raise ExperimentError('Resumo da experiência inválido.')
    summary = {
        'profile': 'profit-evidence-v1', 'started_at': iso(started_at),
        'counts': _numeric_tree(result.get('counts', result.get('stages', result.get('stage_counts', {})))),
        'rejection_histogram': _numeric_tree(result.get('rejection_histogram', result.get('rejections', {}))),
        'budget': {}, 'telegram_sent': 0,
        'ranking_levels': {level: result.get('ranking_levels', {}).get(level, 0)
                           if type(result.get('ranking_levels', {}).get(level, 0)) is int else 0
                           for level in ('A', 'B', 'C')},
    }
    for name, stage in budget.stats.items():
        summary['budget'][name] = {
            provider: {key: number for key, number in stage.get(provider, {}).items()
                       if type(number) is int and number >= 0}
            for provider in ('birdeye', 'helius')}
    if ADDRESS.search(json.dumps(summary)):
        raise ExperimentError('O resumo público contém dados fora do contrato.')
    return summary


def run_experiment(production, experiment, output, config, production_vault, experiment_vault,
                   *, now=None, pipeline=None, space_check=None):
    production, experiment, output = Path(production), Path(experiment), Path(output)
    now = now or utcnow()
    if (production.resolve() == experiment.resolve() or not production.is_file()
            or config.get('profit', {}).get('enabled') is not True
            or config.get('selection', {}).get('notifications', 'strict') != 'strict'):
        raise ExperimentError('Configuração ou isolamento da experiência inválidos.')
    if experiment.exists():
        raise ExperimentError('Já existe uma base experimental local; não substituir.')
    experiment.parent.mkdir(parents=True, exist_ok=True)
    with process_lock(production):
        existing = experiment_vault.github.release()
        if existing is None:
            descriptor = os.open(experiment, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
        else:
            experiment_vault.restore(experiment)
        os.chmod(experiment, 0o600)
        with Store(production) as central, Store(experiment) as store:
            budget = ProfitBudget(central, lambda: production_vault.save(production), config,
                                  now=now, space_check=space_check)
            budget.preflight(experiment)  # Antes de qualquer reserva central.
            budget.bind_store(store)
            if existing is None:
                experiment_vault.github.create()
                experiment_vault.save(experiment, initializing=True)
            notifications = store.db.execute('SELECT COUNT(*) FROM notifications').fetchone()[0]
            if pipeline is None:
                from wallets.profit_pipeline import run_pipeline
                pipeline = run_pipeline
            checkpoint = lambda: experiment_vault.save(experiment)
            try:
                result = pipeline(store, config, budget, checkpoint, str(output), now=now)
            finally:
                checkpoint()  # Recuperar resultados parciais em caso de falha.
            if store.db.execute('SELECT COUNT(*) FROM notifications').fetchone()[0] != notifications:
                raise ExperimentError('A experiência tentou modificar notificações.')
            summary = public_summary(result, budget, now)
            output.mkdir(parents=True, exist_ok=True)
            (output/'experiment-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
            return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description='Ranking privado e experimentação, sem Telegram')
    parser.add_argument('--production-db', default='state/production.sqlite')
    parser.add_argument('--db', default='state/profit.sqlite')
    parser.add_argument('--output', default='reports/profit')
    parser.add_argument('--config', default='config.profit.toml')
    parser.add_argument('--probe-wallet-access', action='store_true',
                        help='Uma única consulta Wallet API a uma carteira já descoberta; sem ranking novo.')
    args = parser.parse_args(argv)
    try:
        for name in ('STATE_ENCRYPTION_KEY', 'BIRDEYE_API_KEY', 'HELIUS_API_KEY'):
            if not os.environ.get(name):
                raise ExperimentError('Falta um segredo indispensável à experiência em Actions.')
        config = cli.load_config(args.config)
        key = os.environ['STATE_ENCRYPTION_KEY']
        pipeline = None
        if args.probe_wallet_access:
            from wallets.profit_probe import run_probe
            pipeline = run_probe
        summary = run_experiment(args.production_db, args.db, args.output, config,
            Vault(key), Vault(key, ExperimentGitHub(), compress_snapshot=True,
                              max_state_bytes=EXPERIMENT_MAX_STATE_BYTES), pipeline=pipeline)
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    except (ExperimentError, ProfitBudgetError, VaultError, OSError, ValueError, sqlite3.DatabaseError) as exc:
        print('Experiência interrompida ('+type(exc).__name__+'); sem Telegram. Consultar o estado cifrado.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
