#!/usr/bin/env python3
"""Iniciar avaliação manual com pedido cifrado, sem permissões locais de upload."""
from getpass import getpass,GetPassWarning
import warnings
import os
import json
import subprocess
from wallets.models import utcnow
from wallets.profit_request import encode_request


def dispatch_private(key,wallet):
    ticket=encode_request(key,wallet,utcnow())
    payload={'manual_only':'true','wallet':'','notify':'false',
             'initialize_state':'false','probe_only':'false','probe_contracts':'false',
             'validate_only':'false','probe_wallet_access':'false','private_request':ticket}
    result=subprocess.run(['gh','workflow','run','daily.yml','--repo','urbanz95/wallets',
         '--ref','feat/profit-evidence-funnel','--json'],input=json.dumps(payload),text=True,capture_output=True)
    if result.returncode:raise ValueError('GitHub recusou o arranque do pedido privado')


def main():
    try:
        warnings.simplefilter('error',GetPassWarning)
        key=os.environ.get('STATE_ENCRYPTION_KEY') or getpass('Chave de recuperação (oculta): ')
        wallet=getpass('Endereço a analisar (oculto): ').strip()
        dispatch_private(key,wallet)
    except Exception:
        print('Não foi possível iniciar a análise privada. Confirme a chave e o acesso GitHub.');return 1
    print('Análise privada iniciada no branch experimental.');return 0


if __name__=='__main__':raise SystemExit(main())
