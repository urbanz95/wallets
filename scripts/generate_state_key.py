#!/usr/bin/env python3
"""Cria uma chave de cofre local, sem substituir uma chave que já exista."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from cryptography.fernet import Fernet


def generate_key_file(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    if hasattr(os,'fchmod'):
        os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, 'wb') as handle:
        handle.write(key + b'\n')
    return key.decode('ascii')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Criar a chave de cifragem apenas no teu computador')
    parser.add_argument('--output', default='.local-secrets/state-encryption.key')
    parser.add_argument('--show-key', action='store_true', help='Mostrar a nova chave localmente para copiar para GitHub Secrets')
    args = parser.parse_args(argv)
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        print('Não gerar nem mostrar chaves nos logs do GitHub. Executa este auxiliar no teu computador.', file=sys.stderr)
        return 2
    if args.show_key and not sys.stdout.isatty():
        print('A chave só pode ser mostrada num terminal interativo local; não redireciones a saída para logs.', file=sys.stderr)
        return 2
    try:
        key = generate_key_file(args.output)
    except FileExistsError:
        print('Já existe uma chave nesse ficheiro. Não foi substituída. Conserva a original para recuperar o histórico.', file=sys.stderr)
        return 2
    except OSError:
        print('Não foi possível guardar a chave localmente; não foi publicada.', file=sys.stderr)
        return 2
    print('Cópia local privada guardada em: ' + str(Path(args.output)))
    if args.show_key:
        print('Copia apenas a próxima linha para o secret STATE_ENCRYPTION_KEY; não a publiques:')
        print(key)
    else:
        print('O ficheiro contém a chave para guardar em STATE_ENCRYPTION_KEY. Não o publiques.')
    print('Guarda também uma cópia num gestor de palavras-passe. Não voltes a gerar a chave para um cofre existente.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
