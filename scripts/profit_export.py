#!/usr/bin/env python3
"""Recuperar o Excel privado do último ranking, sem recolha nem Telegram."""
from __future__ import annotations

import argparse
from getpass import GetPassWarning, getpass
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import warnings

from wallets.profit_persistence import latest_run
from wallets.profit_reporting import write_profit_reports
from wallets.storage import Store
from profit_experiment import EXPERIMENT_MAX_STATE_BYTES, ExperimentGitHub
from state_vault import Vault, VaultError


class ExportError(RuntimeError):
    pass


def export_latest(output, *, db=None, vault=None):
    """Só o Excel retém identidades; a base descifrada desaparece no fim.

    Mesmo uma base local é copiada por backup SQLite para não aplicar migrações
    nem abrir transações de escrita sobre o ficheiro fornecido pelo utilizador.
    O cofre é apenas lido: não há criação, gravação nem reserva de créditos.
    """
    if (db is None) == (vault is None):
        raise ExportError('Escolha uma base local ou o cofre da experiência.')
    with tempfile.TemporaryDirectory(prefix='wallets-profit-export-') as directory:
        temporary = Path(directory)/'profit.sqlite'
        if db is not None:
            source = Path(db).resolve()
            if not source.is_file():
                raise ExportError('Base local indisponível.')
            with sqlite3.connect(source.as_uri()+'?mode=ro', uri=True) as origin:
                with sqlite3.connect(temporary) as target:
                    origin.backup(target)
            os.chmod(temporary, 0o600)
        else:
            vault.restore(temporary)
        with Store(temporary) as store:
            snapshot = latest_run(store)
        if snapshot is None:
            raise ExportError('Ainda não existe um ranking guardado nesta experiência.')
        ranking, telemetry = snapshot.get('ranking'), snapshot.get('telemetry', {})
        if (not isinstance(ranking, list)
                or any(not isinstance(candidate, dict) for candidate in ranking)
                or not isinstance(telemetry, dict)):
            raise ExportError('A fotografia não tem um ranking válido.')
        reports = write_profit_reports(ranking, telemetry, Path(output))
        return reports['xlsx'].resolve()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Recuperar o Excel privado do último ranking; não volta a analisar carteiras.')
    parser.add_argument('--db', help='SQLite local da experiência; sem esta opção, ler o cofre GitHub.')
    parser.add_argument('--output', default='reports/profit-private',
                        help='Pasta privada de destino (por defeito: reports/profit-private).')
    args = parser.parse_args(argv)
    try:
        vault = None
        if args.db is None:
            key = os.environ.get('STATE_ENCRYPTION_KEY')
            if not key:
                # Um terminal sem suporte para ocultar a entrada não pode cair
                # no fallback que mostra a chave ao escrever.
                with warnings.catch_warnings():
                    warnings.simplefilter('error', GetPassWarning)
                    key = getpass('Cole STATE_ENCRYPTION_KEY (não aparece no ecrã): ').strip()
            if not key:
                raise ExportError('Falta a chave de recuperação.')
            vault = Vault(key, ExperimentGitHub(), compress_snapshot=True,
                          max_state_bytes=EXPERIMENT_MAX_STATE_BYTES)
        path = export_latest(args.output, db=args.db, vault=vault)
    except (ExportError, VaultError, OSError, ValueError, TypeError,
            sqlite3.DatabaseError, EOFError, KeyboardInterrupt, GetPassWarning):
        # Não publicar exceções: podem conter endereços, credenciais ou payloads.
        print('Não foi possível recuperar o Excel. Confirme a chave, o acesso GitHub '
              'e se a experiência já guardou um ranking.', file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
