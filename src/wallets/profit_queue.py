"""Fila privada persistente: pedidos manuais, pendências e rotação de candidatas."""
import json
from .collection import valid_address
from .models import dumps, iso, parse_time
from .storage import _pack_payload, _unpack_payload


def schema(store):
    store.db.execute('CREATE TABLE IF NOT EXISTS profit_queue(wallet TEXT PRIMARY KEY,first_seen TEXT,last_seen TEXT,last_checked TEXT,pending INTEGER,manual INTEGER,payload BLOB)')
    store.db.commit()


def enqueue(store,candidates,now,manual=False):
    schema(store)
    for candidate in candidates:
        wallet=candidate.get('wallet')
        if not valid_address(wallet):raise ValueError('Endereço inválido')
        prior=store.db.execute('SELECT manual FROM profit_queue WHERE wallet=?',(wallet,)).fetchone()
        candidate=dict(candidate,manual=bool(manual or (prior and prior[0])))
        store.db.execute('INSERT INTO profit_queue VALUES(?,?,?,?,?,?,?) ON CONFLICT(wallet) DO UPDATE SET last_seen=excluded.last_seen,manual=excluded.manual,payload=excluded.payload',
            (wallet,iso(now),iso(now),None,0,int(candidate['manual']),_pack_payload(dumps(candidate))))
    store.db.commit()


def select(store,candidates,now,limit,pending_slots=2):
    enqueue(store,candidates,now)
    rows=[]
    for wallet,first,last,checked,pending,manual,blob in store.db.execute('SELECT * FROM profit_queue'):
        if (now-parse_time(last)).total_seconds()>7*86400 and not manual:continue
        row=json.loads(_unpack_payload(blob));row['manual']=bool(manual)
        row['_pending']=bool(pending);row['_checked']=checked
        rows.append(row)
    def key(row):return (row['_checked'] is not None,row['_checked'] or '',-len(row.get('origins',[])))
    manual=sorted((r for r in rows if r['manual']),key=key)
    pending=sorted((r for r in rows if r['_pending'] and not r['manual']),key=key)[:pending_slots]
    chosen=(manual+pending)[:limit];seen={r['wallet'] for r in chosen}
    chosen+=(sorted((r for r in rows if r['wallet'] not in seen),key=key))[:max(0,limit-len(chosen))]
    return chosen


def checked(store,candidate,now):
    pending=bool(candidate.get('prefilter7',{}).get('passed') and not candidate.get('deep',{}).get('complete'))
    store.db.execute('UPDATE profit_queue SET last_checked=?,pending=?,manual=0 WHERE wallet=?',(iso(now),int(pending),candidate['wallet']))
    store.db.commit()
