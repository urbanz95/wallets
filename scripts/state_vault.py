#!/usr/bin/env python3
"""Estado SQLite cifrado, autenticado e recuperável em GitHub Release assets.

Nunca substitui assets. Um checkpoint falhado impede operações dependentes.
"""
from __future__ import annotations
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
import uuid

from cryptography.fernet import Fernet, InvalidToken

REPOSITORY = 'urbanz95/wallets'
TAG = 'wallets-state'
PATTERN = re.compile(r'^state-\d{8}T\d{12}Z-[0-9a-f]{32}\.enc$')
MAX_STATE_BYTES = 256 * 1024 * 1024

class VaultError(RuntimeError):
    pass

class GitHub:
    def __init__(self, repository=REPOSITORY):
        if repository != REPOSITORY:
            raise VaultError('O cofre só pode usar urbanz95/wallets.')
        self.repository = repository

    def command(self, args, *, allow_missing=False):
        try:
            result = subprocess.run(['gh', *args], check=False, capture_output=True)
        except OSError as exc:
            raise VaultError('GitHub CLI indisponível.') from exc
        if result.returncode:
            # Não imprimir stderr: pode conter URLs ou dados de autenticação.
            if allow_missing and b'HTTP 404' in result.stderr:
                return None
            raise VaultError('Operação GitHub falhou; estado não confirmado.')
        return result.stdout

    def release(self):
        content = self.command(['api', f'repos/{self.repository}/releases/tags/{TAG}'], allow_missing=True)
        if content is None:
            return None
        release = json.loads(content)
        # O endpoint do release não é uma estratégia de paginação de assets.
        # Ler todas as páginas evita restaurar uma geração antiga após meses de execução.
        pages = self.command(['api', '--paginate', '--slurp',
                              f'repos/{self.repository}/releases/{int(release["id"])}/assets?per_page=100'])
        release['assets'] = [asset for page in json.loads(pages) for asset in page]
        return release

    def create(self):
        self.command(['release', 'create', TAG, '--repo', self.repository,
                      '--title', 'Estado cifrado da ferramenta wallets',
                      '--notes', 'Snapshots cifrados e autenticados. Não apagar sem cópia de segurança.',
                      '--target', 'main'])

    def download(self, asset):
        return self.command(['api', '-H', 'Accept: application/octet-stream',
                             f'repos/{self.repository}/releases/assets/{int(asset["id"])}'])

    def upload(self, path):
        self.command(['release', 'upload', TAG, str(path), '--repo', self.repository])


def latest_asset(release):
    assets = [a for a in release.get('assets', []) if PATTERN.fullmatch(a.get('name', ''))]
    if not assets:
        raise VaultError('O cofre existe mas não tem um snapshot recuperável. Não reinicializar.')
    return max(assets, key=lambda a: a['name'])


def _read_generation(db):
    try:
        with sqlite3.connect(db) as con:
            row = con.execute('SELECT generation FROM vault_checkpoint WHERE id=1').fetchone()
            return row[0] if row else None
    except sqlite3.OperationalError:
        return None


def _set_generation(db, generation):
    with sqlite3.connect(db) as con:
        con.execute('CREATE TABLE IF NOT EXISTS vault_checkpoint(id INTEGER PRIMARY KEY CHECK(id=1), generation TEXT NOT NULL)')
        con.execute('INSERT OR REPLACE INTO vault_checkpoint VALUES(1,?)', (generation,))


def _snapshot(db):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'snapshot.sqlite'
        with sqlite3.connect(db) as source, sqlite3.connect(path) as target:
            source.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise VaultError('A base de dados não passou a verificação de integridade.')
        raw = path.read_bytes()
    if len(raw) > MAX_STATE_BYTES:
        raise VaultError('Estado demasiado grande; requer manutenção antes de continuar.')
    return raw


def _private_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, 'wb') as output:
        output.write(data)


class Vault:
    def __init__(self, key, github=None):
        try:
            self.fernet = Fernet(key.encode() if isinstance(key, str) else key)
        except (ValueError, TypeError) as exc:
            raise VaultError('STATE_ENCRYPTION_KEY ausente ou inválida.') from exc
        self.github = github or GitHub(os.environ.get('GH_REPO', REPOSITORY))

    def init(self, db):
        """Apenas primeiro arranque explícito; um release existente bloqueia reset."""
        db = Path(db)
        if self.github.release() is not None:
            raise VaultError('Já existe estado remoto; inicialização recusada.')
        if db.exists():
            raise VaultError('Já existe uma base local; inicialização recusada.')
        db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db) as con:
            con.execute('PRAGMA user_version=1')
        os.chmod(db, 0o600)
        self.github.create()
        return self.save(db, initializing=True)

    def save(self, db, *, initializing=False):
        db = Path(db)
        if not db.is_file():
            raise VaultError('Não existe base de dados para guardar.')
        release = self.github.release()
        if release is None:
            raise VaultError('Cofre remoto ausente. Inicialização manual explícita necessária.')
        assets = [a for a in release.get('assets', []) if PATTERN.fullmatch(a.get('name', ''))]
        current = _read_generation(db)
        if initializing:
            if assets or current is not None:
                raise VaultError('O cofre já contém estado; inicialização recusada.')
        elif not assets or current != latest_asset(release)['name']:
            raise VaultError('Estado local desatualizado ou checkpoint incerto. Restaurar antes de continuar.')
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        generation = f'state-{stamp}-{uuid.uuid4().hex}.enc'
        if assets and generation <= latest_asset(release)['name']:
            raise VaultError('Relógio anterior ao último checkpoint; gravação recusada.')
        # Atualizar antes de upload: uma falha ambígua exige restore; nunca prosseguir
        # para Telegram com uma reserva que não foi confirmada no armazenamento durável.
        _set_generation(db, generation)
        raw = _snapshot(db)
        body = {'schema': 1, 'generation': generation, 'parent': current,
                'created_at': datetime.now(timezone.utc).isoformat(),
                'sha256': hashlib.sha256(raw).hexdigest(),
                'database': base64.b64encode(raw).decode('ascii')}
        encrypted = self.fernet.encrypt(json.dumps(body, sort_keys=True).encode())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / generation
            _private_write(path, encrypted)
            self.github.upload(path)
        # Confirmar existência e tamanho após upload. A próxima execução autentica o conteúdo.
        confirmed = latest_asset(self.github.release())
        if confirmed['name'] != generation or confirmed.get('size') != len(encrypted):
            raise VaultError('Checkpoint remoto não confirmado; notificações bloqueadas.')
        _private_write(db.with_suffix(db.suffix + '.enc'), encrypted)
        return generation

    def restore(self, db):
        db = Path(db)
        release = self.github.release()
        if release is None:
            raise VaultError('Estado remoto ausente. Primeiro arranque exige initialize_state manual.')
        asset = latest_asset(release)
        encrypted = self.github.download(asset)
        if not encrypted or len(encrypted) > 2 * MAX_STATE_BYTES:
            raise VaultError('Snapshot ausente ou demasiado grande.')
        try:
            body = json.loads(self.fernet.decrypt(encrypted))
            raw = base64.b64decode(body['database'], validate=True)
        except (InvalidToken, ValueError, KeyError, TypeError) as exc:
            raise VaultError('Não foi possível autenticar/decifrar o estado. Não será reinicializado.') from exc
        if (body.get('schema') != 1 or body.get('generation') != asset['name']
                or hashlib.sha256(raw).hexdigest() != body.get('sha256')
                or len(raw) > MAX_STATE_BYTES):
            raise VaultError('Integridade do snapshot inválida.')
        db.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=db.parent) as directory:
            candidate = Path(directory) / 'restored.sqlite'
            _private_write(candidate, raw)
            try:
                with sqlite3.connect(candidate) as con:
                    if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise VaultError('SQLite restaurado inválido.')
                if _read_generation(candidate) != asset['name']:
                    raise VaultError('Geração SQLite incompatível com o snapshot.')
            except sqlite3.DatabaseError as exc:
                raise VaultError('SQLite restaurado inválido.') from exc
            # Nunca restaurar por cima de uma base aberta: CLI/workflow usam fases sequenciais.
            for suffix in ('-wal', '-shm', '-journal'):
                if Path(str(db) + suffix).exists():
                    raise VaultError('Base de dados possivelmente aberta; restauração recusada.')
            os.replace(candidate, db)
        _private_write(db.with_suffix(db.suffix + '.enc'), encrypted)
        return asset['name']


def main(argv=None):
    parser = argparse.ArgumentParser(description='Cofre cifrado de estado GitHub')
    parser.add_argument('action', choices=['init', 'restore', 'save'])
    parser.add_argument('--db', default='state/wallets.sqlite')
    args = parser.parse_args(argv)
    try:
        vault = Vault(os.environ.get('STATE_ENCRYPTION_KEY', ''))
        generation = getattr(vault, args.action)(args.db)
    except (VaultError, OSError, sqlite3.DatabaseError, json.JSONDecodeError):
        print('Estado não confirmado. Execução interrompida sem autorização de novos envios.')
        return 1
    print(f'Estado {args.action}: {generation}')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
