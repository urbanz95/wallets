#!/usr/bin/env python3
"""Adicionar pedido privado ao cofre; nunca pôr endereços no formulário Actions."""
from getpass import getpass,GetPassWarning
import warnings
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from wallets.models import utcnow
from wallets.storage import Store
from wallets.collection import valid_address
from wallets.profit_queue import enqueue
from profit_experiment import ExperimentGitHub,EXPERIMENT_MAX_STATE_BYTES
from state_vault import Vault


def enqueue_private(vault,wallet):
    if not valid_address(wallet):raise ValueError('Endereço inválido')
    with TemporaryDirectory(prefix='wallets-request-') as directory:
        db=Path(directory)/'private.sqlite'
        vault.restore(db)
        with Store(db) as store:
            enqueue(store,[{'wallet':wallet,'origins':[],'discovery':{'source':'manual'}}],utcnow(),manual=True)
        vault.save(db)  # Recusa estado remoto que tenha avançado; nunca sobrepõe gerações.


def main():
    try:
        warnings.simplefilter('error',GetPassWarning)
        key=os.environ.get('STATE_ENCRYPTION_KEY') or getpass('Chave de recuperação (oculta): ')
        wallet=getpass('Endereço a analisar (oculto): ').strip()
        enqueue_private(Vault(key,ExperimentGitHub(),compress_snapshot=True,max_state_bytes=EXPERIMENT_MAX_STATE_BYTES),wallet)
    except Exception:
        print('Não foi possível guardar o pedido privado. Confirme a chave/acesso e ausência de execução concorrente.');return 1
    print('Pedido guardado no estado cifrado. Executar manual_only no branch experimental.');return 0


if __name__=='__main__':raise SystemExit(main())
