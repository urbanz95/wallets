"""Evidência comprimida e interpretações versionadas; snapshots antigos imutáveis."""
from hashlib import sha256
import json
from .models import dumps, iso, parse_time
from .storage import _pack_payload, _unpack_payload
from .profit_persistence import load_receipts, _canonical_receipt, _atomic
from .helius_collection import transaction_identity

VERSION = '2026-11-02.sol.2'


def schema(store):
    store.db.execute('CREATE TABLE IF NOT EXISTS profit_raw_evidence(wallet TEXT,signature TEXT,at TEXT,hash TEXT,payload BLOB,PRIMARY KEY(wallet,signature))')
    store.db.execute('CREATE TABLE IF NOT EXISTS profit_interpretations(wallet TEXT,signature TEXT,version TEXT,hash TEXT,payload BLOB,PRIMARY KEY(wallet,signature,version,hash))')
    for op in ('UPDATE','DELETE'):
        store.db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_profit_interpretations_{op.lower()} BEFORE {op} ON profit_interpretations BEGIN SELECT RAISE(ABORT,'immutable interpretation'); END")
    store.db.commit()


def replay(store,wallet,start,end):
    schema(store)
    # Valida sempre as provas legadas antes de as complementar.
    items={r['signature']:r for r in load_receipts(store,wallet,start,end)}
    for signature,at,digest,blob in store.db.execute('SELECT signature,at,hash,payload FROM profit_raw_evidence WHERE wallet=? AND at>=? AND at<?',(wallet,iso(start),iso(end))):
        text=_unpack_payload(blob)
        if sha256(text.encode()).hexdigest()!=digest:raise ValueError('Evidência de origem corrompida')
        raw=json.loads(text)
        if transaction_identity(raw)!=signature or iso(parse_time(raw['blockTime']))!=at:raise ValueError('Identidade de origem incoerente')
        items[signature]=raw
    return list(items.values())


def persist(store,wallet,receipts,raw,start,end):
    schema(store)
    by_sig={r['signature']:r for r in receipts}
    prepared=[]
    for row in raw:
        sig=transaction_identity(row); stamp=parse_time(row['blockTime'])
        text=dumps(row);digest=sha256(text.encode()).hexdigest()
        if not start<=stamp<end:raise ValueError('Origem fora da janela')
        rec=by_sig.get(sig)
        if rec is None or rec['raw_hash']!=digest:raise ValueError('Origem sem interpretação correspondente')
        prepared.append((sig,iso(stamp),digest,text))
    with _atomic(store.db):
        for sig,at,digest,text in prepared:
            old=store.db.execute('SELECT hash FROM profit_raw_evidence WHERE wallet=? AND signature=?',(wallet,sig)).fetchone()
            if old and old[0]!=digest:raise ValueError('Origem mudou; exige reconciliação explícita')
            store.db.execute('INSERT OR IGNORE INTO profit_raw_evidence VALUES(?,?,?,?,?)',(wallet,sig,at,digest,_pack_payload(text)))
        for receipt in receipts:
            values=_canonical_receipt(receipt,wallet,start,end)
            # Interpretações alternativas acrescentam versões; nunca substituem snapshots.
            store.db.execute('INSERT OR IGNORE INTO profit_interpretations VALUES(?,?,?,?,?)',(wallet,values[0],VERSION,values[3],_pack_payload(values[4])))
        # Eliminar só a segunda cópia, depois da evidência reprocessável autenticada.
        for sig,at,digest,text in prepared:
            old=store.db.execute("SELECT payload FROM source_transactions WHERE source='helius' AND address=? AND signature=?",(wallet,sig)).fetchone()
            if old and sha256(_unpack_payload(old[0]).encode()).hexdigest()!=digest:raise ValueError('Origem armazenada divergente')
            store.db.execute("DELETE FROM source_transactions WHERE source='helius' AND address=? AND signature=?",(wallet,sig))
    return {'receipts_saved':len(receipts),'raw_retained':len(prepared),'interpretation_version':VERSION}


def expire(store,before):
    schema(store)
    # Mantém as interpretações/snapshots; expira apenas origem fora do horizonte.
    store.db.execute('DELETE FROM profit_raw_evidence WHERE at<?',(iso(before),));store.db.commit()
