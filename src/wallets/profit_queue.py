"""Fila privada: rotação, prioridade económica e origens recentes verificáveis."""
from datetime import timedelta
from decimal import Decimal, InvalidOperation
import json

from .collection import valid_address
from .models import dumps, iso, parse_time
from .storage import _pack_payload, _unpack_payload


def schema(store):
    store.db.execute('CREATE TABLE IF NOT EXISTS profit_queue(wallet TEXT PRIMARY KEY,first_seen TEXT,last_seen TEXT,last_checked TEXT,pending INTEGER,manual INTEGER,payload BLOB)')
    store.db.commit()


def _time(value):
    try:
        return parse_time(value)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _origin_times(candidate, now, *, fallback=None):
    """Não renovar origens pela seleção, por PnL novo ou por pedido manual.

    No legado, last_seen é a data em que esse payload foi efetivamente guardado;
    não é substituída por now. Depois da migração, o mapa por token é autoritativo.
    """
    mapping = candidate.get('origin_observed_at')
    if not isinstance(mapping, dict):
        discovery = candidate.get('discovery')
        discovery = discovery if isinstance(discovery, dict) else {}
        stamp = discovery.get('observed_at', fallback)
        mapping = {token: stamp for token in candidate.get('origins', [])
                   if isinstance(token, str) and token}
    result = {}
    for token, value in mapping.items():
        stamp = _time(value)
        if isinstance(token, str) and token and stamp is not None and now-timedelta(days=7) <= stamp <= now:
            result[token] = iso(stamp)
    return result


def _with_origins(candidate, mapping):
    result = dict(candidate, origins=sorted(mapping), origin_observed_at=mapping)
    discovery = result.get('discovery')
    if isinstance(discovery, dict):
        result['discovery'] = dict(discovery, distinct_token_origins=len(mapping))
    return result


def enqueue(store,candidates,now,manual=False):
    schema(store)
    now = parse_time(now)
    for candidate in candidates:
        wallet=candidate.get('wallet')
        if not valid_address(wallet):raise ValueError('Endereço inválido')
        prior=store.db.execute('SELECT manual,last_seen,payload FROM profit_queue WHERE wallet=?',(wallet,)).fetchone()
        previous=json.loads(_unpack_payload(prior[2])) if prior else {}
        origins=_origin_times(previous,now,fallback=prior[1] if prior else None)
        # Um endereço introduzido manualmente não é uma nova descoberta num token.
        if not manual:
            incoming=_origin_times(candidate,now,fallback=iso(now))
            for token, stamp in incoming.items():
                origins[token]=max(origins.get(token,stamp),stamp)
        merged=dict(previous,**candidate)
        for key in ('_checked','_pending'):
            merged.pop(key,None)
        merged['manual']=bool(manual or (prior and prior[0]))
        merged=_with_origins(merged,origins)
        store.db.execute('INSERT INTO profit_queue VALUES(?,?,?,?,?,?,?) ON CONFLICT(wallet) DO UPDATE SET last_seen=excluded.last_seen,manual=excluded.manual,payload=excluded.payload',
            (wallet,iso(now),iso(now),None,0,int(merged['manual']),_pack_payload(dumps(merged))))
    store.db.commit()


def _provider_profit(row, now):
    discovery=row.get('discovery')
    if not isinstance(discovery,dict):return None
    stamp=_time(discovery.get('observed_at'))
    if stamp is None or not now-timedelta(days=7)<=stamp<=now:return None
    value=discovery.get('provider_pnl_usd',discovery.get('pnl_usd'))
    if isinstance(value,bool):return None
    try:
        value=Decimal(str(value))
        return value if value.is_finite() else None
    except (InvalidOperation,ValueError,TypeError):
        return None


def select(store,candidates,now,limit,pending_slots=2):
    now=parse_time(now)
    enqueue(store,candidates,now)
    rows=[]
    for wallet,first,last,checked,pending,manual,blob in store.db.execute('SELECT * FROM profit_queue'):
        if (now-parse_time(last)).total_seconds()>7*86400 and not manual:continue
        row=json.loads(_unpack_payload(blob));row['manual']=bool(manual)
        row=_with_origins(row,_origin_times(row,now,fallback=last))
        row['_pending']=bool(pending);row['_checked']=checked
        rows.append(row)
    def key(row):
        origins=len(row.get('origins',[]));profit=_provider_profit(row,now)
        return (row['_checked'] is not None,row['_checked'] or '',
                -int(origins>=2),-origins,profit is None,-(profit or Decimal(0)),row['wallet'])
    manual=sorted((r for r in rows if r['manual']),key=key)
    pending=sorted((r for r in rows if r['_pending'] and not r['manual']),key=key)[:pending_slots]
    chosen=(manual+pending)[:limit];seen={r['wallet'] for r in chosen}
    chosen+=(sorted((r for r in rows if r['wallet'] not in seen),key=key))[:max(0,limit-len(chosen))]
    return chosen


def checked(store,candidate,now):
    pending=bool(candidate.get('prefilter7',{}).get('passed') and not candidate.get('deep',{}).get('complete'))
    store.db.execute('UPDATE profit_queue SET last_checked=?,pending=?,manual=0 WHERE wallet=?',(iso(now),int(pending),candidate['wallet']))
    store.db.commit()
