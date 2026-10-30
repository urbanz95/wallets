"""Provas SOL compactas e fotografias privadas, antes da poda dos dados brutos.

Os recibos conservam assinatura, hash da resposta de origem, âmbito temporal e
interpretação, incluindo operações desconhecidas. Não são uma cópia dos dados
brutos: programas ainda não suportados exigem nova recolha para reinterpretar.
Os manifests impedem que um cursor com cobertura conhecida esconda recibos
perdidos. Apenas o payload Helius da carteira/assinatura normalizada é podado.
"""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import re

from .helius_collection import transaction_identity
from .models import dumps, iso, parse_time, utcnow
from .storage import _pack_payload, _unpack_payload

_HEX = re.compile(r'^[0-9a-f]{64}$')
_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS profit_receipts(
        wallet TEXT NOT NULL,signature TEXT NOT NULL,at TEXT NOT NULL,
        raw_hash TEXT NOT NULL,receipt_hash TEXT NOT NULL,payload BLOB NOT NULL,
        PRIMARY KEY(wallet,signature))''',
    '''CREATE TABLE IF NOT EXISTS profit_receipt_manifests(
        id TEXT PRIMARY KEY,wallet TEXT NOT NULL,window_start TEXT NOT NULL,
        window_end TEXT NOT NULL,payload BLOB NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS profit_runs(
        id TEXT PRIMARY KEY,at TEXT NOT NULL,payload BLOB NOT NULL)''',
    '''CREATE INDEX IF NOT EXISTS profit_receipts_window
        ON profit_receipts(wallet,at,signature)''',
    '''CREATE INDEX IF NOT EXISTS profit_manifests_window
        ON profit_receipt_manifests(wallet,window_start,window_end)''',
)


@contextmanager
def _atomic(db):
    # SAVEPOINT também respeita uma transação já aberta pelo chamador.
    name = 'wallets_profit_persistence'
    db.execute('SAVEPOINT '+name)
    try:
        yield
    except BaseException:
        db.execute('ROLLBACK TO '+name)
        db.execute('RELEASE '+name)
        raise
    else:
        db.execute('RELEASE '+name)


def ensure_schema(store):
    """Migração apenas aditiva; as tabelas operacionais anteriores não mudam."""
    with _atomic(store.db):
        for statement in _SCHEMA:
            store.db.execute(statement)
        for table in ('profit_receipts', 'profit_receipt_manifests', 'profit_runs'):
            for operation in ('UPDATE', 'DELETE'):
                store.db.execute(
                    f'CREATE TRIGGER IF NOT EXISTS immutable_{table}_{operation.lower()} '
                    f'BEFORE {operation} ON {table} BEGIN '
                    "SELECT RAISE(ABORT,'immutable profit evidence'); END"
                )


def _identity(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label)
    return value


def _window(start, end):
    start, end = parse_time(start), parse_time(end)
    if start >= end:
        raise ValueError('Janela da prova inválida.')
    return start, end


def _canonical_receipt(receipt, wallet, start=None, end=None):
    if not isinstance(receipt, dict) or receipt.get('schema') != 'sol-receipt-v1':
        raise ValueError('Formato do recibo SOL inválido.')
    if 'wallet' in receipt and receipt['wallet'] != wallet:
        raise ValueError('Recibo pertence a outra carteira.')
    signature = _identity(receipt.get('signature'), 'Recibo sem assinatura.')
    timestamp = parse_time(receipt.get('timestamp'))
    if start is not None and not start <= timestamp < end:
        raise ValueError('Recibo fora da janela de normalização.')
    raw_hash = receipt.get('raw_hash')
    if not isinstance(raw_hash, str) or not _HEX.fullmatch(raw_hash):
        raise ValueError('Recibo sem hash de origem válido.')
    transaction_index = receipt.get('transaction_index', receipt.get('index'))
    if (type(receipt.get('slot')) is not int or receipt['slot'] < 0
            or (transaction_index is not None
                and (type(transaction_index) is not int or transaction_index < 0))):
        raise ValueError('Recibo sem posição verificável.')
    if (not isinstance(receipt.get('programs'), list)
            or any(not isinstance(program, str) or not program for program in receipt['programs'])
            or not isinstance(receipt.get('status'), str) or not receipt['status']
            or 'fee_sol' not in receipt
            or not isinstance(receipt.get('legs'), list)
            or any(not isinstance(leg, dict) for leg in receipt['legs'])):
        raise ValueError('Prova mínima do recibo incompleta.')
    result = dict(receipt, timestamp=iso(timestamp))
    encoded = dumps(result)
    return signature, iso(timestamp), raw_hash, sha256(encoded.encode()).hexdigest(), encoded


def _stored_receipt(row, wallet):
    """Validar identidade e hash antes de confiar num recibo recuperado."""
    signature, at, raw_hash, receipt_hash, payload = row
    text = _unpack_payload(payload)
    if sha256(text.encode()).hexdigest() != receipt_hash:
        raise ValueError('Hash do recibo guardado inválido.')
    try:
        receipt = json.loads(text)
        values = _canonical_receipt(receipt, wallet)
    except (TypeError, KeyError, OverflowError, json.JSONDecodeError) as exc:
        raise ValueError('Recibo guardado inválido.') from exc
    if values[:4] != (signature, at, raw_hash, receipt_hash) or values[4] != text:
        raise ValueError('Identidade do recibo guardado incoerente.')
    return receipt


def save_receipts_and_prune(store, wallet, receipts, raw_transactions,
                            window_start, window_end, metadata=None):
    """Persistir provas e manifest atomicamente, só depois eliminar raw coberto.

    Todos os raw fornecidos precisam de recibo correspondente e do mesmo hash.
    Recibos desconhecidos são conservados como tal. Recibos já existentes podem
    ser repetidos, mas nunca alterados. Falha em qualquer validação impede a poda.
    """
    wallet = _identity(wallet, 'Carteira da prova inválida.')
    start, end = _window(window_start, window_end)
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError('Metadata da prova inválida.')
    canonical = {}
    for receipt in receipts:
        values = _canonical_receipt(receipt, wallet, start, end)
        prior = canonical.get(values[0])
        if prior is not None and prior != values:
            raise ValueError('Recibos repetidos com conteúdo divergente.')
        canonical[values[0]] = values
    raw = {}
    for transaction in raw_transactions:
        signature = transaction_identity(transaction)
        timestamp = parse_time(transaction.get('blockTime'))
        if not start <= timestamp < end:
            raise ValueError('Transação bruta fora da janela de normalização.')
        text = dumps(transaction)
        raw_hash = sha256(text.encode()).hexdigest()
        previous = raw.get(signature)
        if previous is not None and previous != (iso(timestamp), raw_hash):
            raise ValueError('Transações brutas repetidas com conteúdo divergente.')
        raw[signature] = (iso(timestamp), raw_hash)
        receipt = canonical.get(signature)
        if receipt is None:
            raise ValueError('Falta recibo de uma transação bruta; poda recusada.')
        if (receipt[1], receipt[2]) != raw[signature]:
            raise ValueError('Recibo não corresponde à origem bruta; poda recusada.')
    # Calcular todos os blobs antes da transação evita uma poda parcial por limite
    # ou erro de serialização. Não há pressuposto de custo zero nesta operação.
    packed = {signature: _pack_payload(values[4]) for signature, values in canonical.items()}
    items = [dict(signature=values[0], timestamp=values[1], raw_hash=values[2],
                  receipt_hash=values[3]) for values in sorted(canonical.values(), key=lambda item: (item[1], item[0]))]
    manifest = dict(schema='sol-receipt-manifest-v1', wallet=wallet,
                    window_start=iso(start), window_end=iso(end),
                    receipt_count=len(items), raw_count=len(raw),
                    receipts=items, metadata=metadata or {})
    manifest_text = dumps(manifest)
    manifest_id = sha256(manifest_text.encode()).hexdigest()
    packed_manifest = _pack_payload(manifest_text)
    ensure_schema(store)
    added = pruned = 0
    with _atomic(store.db):
        for signature, values in canonical.items():
            old = store.db.execute(
                'SELECT signature,at,raw_hash,receipt_hash,payload FROM profit_receipts '
                'WHERE wallet=? AND signature=?', (wallet, signature),
            ).fetchone()
            if old:
                _stored_receipt(old, wallet)
                if (old[1:4] != values[1:4]
                        or _unpack_payload(old[4]) != values[4]):
                    raise ValueError('Recibo existente diverge; poda recusada.')
            else:
                store.db.execute('INSERT INTO profit_receipts VALUES(?,?,?,?,?,?)',
                                 (wallet, signature, values[1], values[2], values[3], packed[signature]))
                added += 1
        for signature, (_, raw_hash) in raw.items():
            old = store.db.execute(
                "SELECT payload FROM source_transactions WHERE source='helius' AND address=? AND signature=?",
                (wallet, signature),
            ).fetchone()
            if old:
                old_text = _unpack_payload(old[0])
                if sha256(old_text.encode()).hexdigest() != raw_hash:
                    raise ValueError('Origem bruta guardada diverge; poda recusada.')
        existing = store.db.execute('SELECT payload FROM profit_receipt_manifests WHERE id=?',
                                    (manifest_id,)).fetchone()
        if existing and _unpack_payload(existing[0]) != manifest_text:
            raise ValueError('Manifest existente diverge; poda recusada.')
        store.db.execute('INSERT OR IGNORE INTO profit_receipt_manifests VALUES(?,?,?,?,?)',
                         (manifest_id, wallet, iso(start), iso(end), packed_manifest))
        signatures = list(raw)
        for offset in range(0, len(signatures), 500):
            part = signatures[offset:offset+500]
            placeholders = ','.join('?' for _ in part)
            cursor = store.db.execute(
                "DELETE FROM source_transactions WHERE source='helius' AND address=? "
                f'AND signature IN ({placeholders})', (wallet, *part),
            )
            pruned += cursor.rowcount
    return {'receipts_saved': added, 'raw_pruned': pruned, 'manifest_id': manifest_id}


def load_receipts(store, wallet, start, end):
    """Ler provas compactas e comprovar os manifests que intersectam a janela."""
    wallet = _identity(wallet, 'Carteira da prova inválida.')
    start, end = _window(start, end)
    ensure_schema(store)
    rows = store.db.execute(
        'SELECT signature,at,raw_hash,receipt_hash,payload FROM profit_receipts WHERE wallet=?',
        (wallet,),
    ).fetchall()
    by_signature = {}
    for row in rows:
        receipt = _stored_receipt(row, wallet)
        by_signature[row[0]] = (row, receipt)
    manifests = store.db.execute(
        'SELECT id,window_start,window_end,payload FROM profit_receipt_manifests '
        'WHERE wallet=? AND window_start<? AND window_end>?',
        (wallet, iso(end), iso(start)),
    ).fetchall()
    for identity, a, b, payload in manifests:
        text = _unpack_payload(payload)
        if sha256(text.encode()).hexdigest() != identity:
            raise ValueError('Hash do manifest inválido.')
        try:
            manifest = json.loads(text)
            items = manifest['receipts']
            if (manifest['schema'] != 'sol-receipt-manifest-v1' or manifest['wallet'] != wallet
                    or manifest['window_start'] != a or manifest['window_end'] != b
                    or not isinstance(items, list) or manifest['receipt_count'] != len(items)):
                raise ValueError('Manifest incoerente.')
            for item in items:
                existing = by_signature.get(item['signature'])
                if existing is None or existing[0][1:4] != (
                        item['timestamp'], item['raw_hash'], item['receipt_hash']):
                    raise ValueError('Recibo esperado ausente ou divergente; cobertura não comprovada.')
        except (TypeError, KeyError, json.JSONDecodeError) as exc:
            raise ValueError('Manifest guardado inválido.') from exc
    return [receipt for row, receipt in sorted(by_signature.values(), key=lambda value: (value[0][1], value[0][0]))
            if start <= parse_time(row[1]) < end]


def save_run(store, payload):
    """Fotografia privada e imutável; conteúdo completo só segue para o cofre."""
    if not isinstance(payload, dict):
        raise ValueError('Fotografia da execução inválida.')
    text = dumps(payload)
    identity = sha256(text.encode()).hexdigest()
    packed = _pack_payload(text)
    when = parse_time(payload.get('started_at', payload.get('ended_at', utcnow())))
    ensure_schema(store)
    with _atomic(store.db):
        old = store.db.execute('SELECT payload FROM profit_runs WHERE id=?', (identity,)).fetchone()
        if old and _unpack_payload(old[0]) != text:
            raise ValueError('Fotografia existente diverge.')
        store.db.execute('INSERT OR IGNORE INTO profit_runs VALUES(?,?,?)', (identity, iso(when), packed))
    return identity


def latest_run(store):
    ensure_schema(store)
    row = store.db.execute('SELECT id,payload FROM profit_runs ORDER BY at DESC,rowid DESC LIMIT 1').fetchone()
    if row is None:
        return None
    text = _unpack_payload(row[1])
    if sha256(text.encode()).hexdigest() != row[0]:
        raise ValueError('Hash da fotografia da execução inválido.')
    result = json.loads(text)
    if not isinstance(result, dict):
        raise ValueError('Fotografia guardada inválida.')
    return result
