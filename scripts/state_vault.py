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
import zlib

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
            status=re.search(rb'HTTP ([0-9]{3})',result.stderr)
            suffix=' (HTTP '+status.group(1).decode('ascii')+')' if status else ''
            raise VaultError('Operação GitHub falhou'+suffix+'; estado não confirmado.')
        return result.stdout

    def release(self):
        content = self.command(['api', f'repos/{self.repository}/releases/tags/{TAG}'], allow_missing=True)
        if content is None:
            return None
        release = json.loads(content)
        # O endpoint do release não é uma estratégia de paginação de assets.
        # Ler todas as páginas evita restaurar uma geração antiga após meses de execução.
        pages = self.command(['api', '--paginate',
                              f'repos/{self.repository}/releases/{int(release["id"])}/assets?per_page=100'])
        # gh 2.46 já suporta --paginate, mas ainda não --slurp. Cada página
        # é um documento JSON independente; nunca aceitar apenas a primeira.
        assets = []
        decoder = json.JSONDecoder()
        position = 0
        page_count = 0
        try:
            document = pages.decode('utf-8')
            while position < len(document):
                while position < len(document) and document[position] in ' \t\r\n':
                    position += 1
                if position == len(document):
                    break
                page, position = decoder.raw_decode(document, position)
                if not isinstance(page, list) or any(not isinstance(asset, dict) for asset in page):
                    raise VaultError('Lista de snapshots GitHub inválida; recuperação recusada.')
                assets.extend(page)
                page_count += 1
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise VaultError('Paginação de snapshots GitHub inválida; recuperação recusada.') from exc
        if page_count == 0:
            raise VaultError('Paginação de snapshots GitHub ausente; recuperação recusada.')
        release['assets'] = assets
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


def _snapshot(db, max_bytes=None):
    max_bytes = MAX_STATE_BYTES if max_bytes is None else max_bytes
    if type(max_bytes) is not int or max_bytes <= 0:
        raise VaultError('Limite de estado inválido.')
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'snapshot.sqlite'
        with sqlite3.connect(db) as source, sqlite3.connect(path) as target:
            source.backup(target)
            # Libertar páginas vazias da cache no snapshot, sem alterar o DB aberto.
            target.execute('VACUUM')
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise VaultError('A base de dados não passou a verificação de integridade.')
        if path.stat().st_size > max_bytes:
            raise VaultError('Estado demasiado grande; requer manutenção antes de continuar.')
        raw = path.read_bytes()
    if len(raw) > max_bytes:
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
    def __init__(self, key, github=None, *, compress_snapshot=False, max_state_bytes=None):
        max_state_bytes = MAX_STATE_BYTES if max_state_bytes is None else max_state_bytes
        if (type(compress_snapshot) is not bool or type(max_state_bytes) is not int
                or max_state_bytes <= 0):
            raise VaultError('Configuração de armazenamento inválida.')
        self.compress_snapshot = compress_snapshot
        self.max_state_bytes = max_state_bytes
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
        raw = _snapshot(db, self.max_state_bytes)
        stored = zlib.compress(raw, level=6) if self.compress_snapshot else raw
        body = {'schema': 1, 'generation': generation, 'parent': current,
                'created_at': datetime.now(timezone.utc).isoformat(),
                'sha256': hashlib.sha256(raw).hexdigest(),
                'database': base64.b64encode(stored).decode('ascii')}
        if self.compress_snapshot:
            # O SHA refere-se sempre ao SQLite canónico, nunca à representação
            # compactada. A produção mantém o envelope original sem estes campos.
            body['database_encoding'] = 'zlib'
            body['database_size'] = len(raw)
        encrypted = self.fernet.encrypt(json.dumps(body, sort_keys=True).encode())
        pending=db.with_suffix(db.suffix+'.pending.enc')
        _private_write(pending,encrypted)  # recuperação cifrada mesmo se o upload falhar
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / generation
            _private_write(path, encrypted)
            self.github.upload(path)
        # Confirmar existência e tamanho após upload. A próxima execução autentica o conteúdo.
        confirmed = latest_asset(self.github.release())
        if confirmed['name'] != generation or confirmed.get('size') != len(encrypted):
            raise VaultError('Checkpoint remoto não confirmado; notificações bloqueadas.')
        _private_write(db.with_suffix(db.suffix + '.enc'), encrypted)
        pending.unlink(missing_ok=True)
        return generation

    def restore(self, db):
        db = Path(db)
        release = self.github.release()
        if release is None:
            raise VaultError('Estado remoto ausente. Primeiro arranque exige initialize_state manual.')
        asset = latest_asset(release)
        encrypted = self.github.download(asset)
        if not encrypted or len(encrypted) > 2 * self.max_state_bytes:
            raise VaultError('Snapshot ausente ou demasiado grande.')
        try:
            body = json.loads(self.fernet.decrypt(encrypted))
            if not isinstance(body, dict):
                raise ValueError('Envelope inválido')
            stored = base64.b64decode(body['database'], validate=True)
            encoding = body.get('database_encoding')
            if encoding is None:
                raw = stored
            elif encoding == 'zlib':
                expected = body['database_size']
                if type(expected) is not int or not 0 < expected <= self.max_state_bytes:
                    raise ValueError('Tamanho descompactado inválido')
                decoder = zlib.decompressobj()
                raw = decoder.decompress(stored, self.max_state_bytes + 1)
                if (len(raw) != expected or len(raw) > self.max_state_bytes
                        or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail):
                    raise ValueError('Estado compactado incompleto, divergente ou excessivo')
            else:
                raise ValueError('Codificação de estado desconhecida')
        except (InvalidToken, ValueError, KeyError, TypeError, zlib.error) as exc:
            raise VaultError('Não foi possível autenticar/decifrar o estado. Não será reinicializado.') from exc
        if (body.get('schema') != 1 or body.get('generation') != asset['name']
                or hashlib.sha256(raw).hexdigest() != body.get('sha256')
                or len(raw) > self.max_state_bytes):
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


def print_state_statistics(db):
    """Só contagens/tamanhos; nunca imprime payloads, endereços ou credenciais."""
    try:
        with sqlite3.connect(Path(db).resolve().as_uri()+'?mode=ro',uri=True) as con:
            counts={key:con.execute('PRAGMA '+key).fetchone()[0] for key in ('page_count','page_size','freelist_count')}
            counts['tables']={}
            names={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for name in ('cache','source_transactions','source_events','events','evaluations'):
                if name in names:
                    row=con.execute('SELECT COUNT(*),COALESCE(SUM(length(payload)),0) FROM '+name).fetchone()
                    counts['tables'][name]={'rows':row[0],'payload_bytes':row[1]}
        print('Diagnóstico de armazenamento (sem conteúdo): '+json.dumps(counts,sort_keys=True))
    except (OSError,sqlite3.DatabaseError):
        print('Diagnóstico de armazenamento indisponível.')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Cofre cifrado de estado GitHub')
    parser.add_argument('action', choices=['init', 'restore', 'save'])
    parser.add_argument('--db', default='state/wallets.sqlite')
    args = parser.parse_args(argv)
    try:
        vault = Vault(os.environ.get('STATE_ENCRYPTION_KEY', ''))
        generation = getattr(vault, args.action)(args.db)
    except VaultError as exc:
        print('Estado não confirmado: '+str(exc)+' Execução interrompida sem autorização de novos envios.')
        print_state_statistics(args.db)
        return 1
    except (OSError, sqlite3.DatabaseError, json.JSONDecodeError) as exc:
        print('Estado não confirmado ('+type(exc).__name__+'). Execução interrompida sem autorização de novos envios.')
        print_state_statistics(args.db)
        return 1
    print(f'Estado {args.action}: {generation}')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
