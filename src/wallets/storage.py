"""SQLite: eventos deduplicados, fotografias imutáveis, quota e outbox conservadora."""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
import zlib
from .models import Event, Coverage, TokenResult, Check, Evaluation, Status, dumps, iso, parse_time

# A compressão é apenas representação local. Não elimina nem modifica provas.
# SQLite mantém compatibilidade com os payloads TEXT das versões anteriores.
PAYLOAD_MAGIC = b'wallets:zlib:v1\x00'
MAX_PAYLOAD_BYTES = 64 * 1024 * 1024


def _pack_payload(text):
    raw = text.encode('utf-8')
    if len(raw) > MAX_PAYLOAD_BYTES:
        raise ValueError('Payload de armazenamento acima do limite permitido')
    return PAYLOAD_MAGIC + len(raw).to_bytes(8, 'big') + zlib.compress(raw, level=6)


def _unpack_payload(payload):
    if isinstance(payload, str):
        if len(payload.encode('utf-8')) > MAX_PAYLOAD_BYTES:
            raise ValueError('Payload de armazenamento acima do limite permitido')
        return payload
    if not isinstance(payload, (bytes, memoryview)):
        raise ValueError('Formato de payload armazenado inválido')
    payload = bytes(payload)
    header_size = len(PAYLOAD_MAGIC) + 8
    if (len(payload) <= header_size or not payload.startswith(PAYLOAD_MAGIC)
            or len(payload) > MAX_PAYLOAD_BYTES + 1024 * 1024):
        raise ValueError('Payload compactado inválido ou versão desconhecida')
    expected = int.from_bytes(payload[len(PAYLOAD_MAGIC):header_size], 'big')
    if expected > MAX_PAYLOAD_BYTES:
        raise ValueError('Payload descompactado acima do limite permitido')
    try:
        decoder = zlib.decompressobj()
        # Mais um byte permite detetar expansão indevida sem alocar a saída toda.
        raw = decoder.decompress(payload[header_size:], expected + 1)
        if (len(raw) != expected or not decoder.eof or decoder.unconsumed_tail
                or decoder.unused_data):
            raise ValueError('Payload compactado incompleto ou divergente')
        return raw.decode('utf-8')
    except (zlib.error, UnicodeDecodeError) as exc:
        raise ValueError('Não foi possível validar o payload compactado') from exc


SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS events(wallet TEXT NOT NULL,id TEXT NOT NULL,at TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(wallet,id));
CREATE TABLE IF NOT EXISTS discoveries(wallet TEXT NOT NULL,token TEXT NOT NULL,at TEXT NOT NULL,PRIMARY KEY(wallet,token));
CREATE TABLE IF NOT EXISTS discovery_evidence(wallet TEXT NOT NULL,token TEXT NOT NULL,at TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(wallet,token,at));
CREATE TABLE IF NOT EXISTS discovery_observations(id TEXT PRIMARY KEY,wallet TEXT NOT NULL,token TEXT NOT NULL,at TEXT NOT NULL,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS token_selections(id TEXT PRIMARY KEY,token TEXT NOT NULL,at TEXT NOT NULL,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cursors(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY,expires TEXT NOT NULL,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS api_failures(id TEXT PRIMARY KEY,source TEXT NOT NULL,endpoint TEXT NOT NULL,at TEXT NOT NULL,payload TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS immutable_api_failure_update BEFORE UPDATE ON api_failures BEGIN SELECT RAISE(ABORT,'immutable API diagnostic'); END;
CREATE TRIGGER IF NOT EXISTS immutable_api_failure_delete BEFORE DELETE ON api_failures BEGIN SELECT RAISE(ABORT,'immutable API diagnostic'); END;
CREATE TABLE IF NOT EXISTS budgets(month TEXT PRIMARY KEY,credits INTEGER NOT NULL CHECK(credits>=0));
CREATE TABLE IF NOT EXISTS provider_budgets(namespace TEXT NOT NULL,month TEXT NOT NULL,credits INTEGER NOT NULL CHECK(credits>=0),PRIMARY KEY(namespace,month));
CREATE TABLE IF NOT EXISTS source_transactions(source TEXT NOT NULL,address TEXT NOT NULL,signature TEXT NOT NULL,at TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(source,address,signature));
CREATE TABLE IF NOT EXISTS source_events(source TEXT NOT NULL,wallet TEXT NOT NULL,id TEXT NOT NULL,at TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(source,wallet,id));
CREATE TABLE IF NOT EXISTS evaluations(id TEXT PRIMARY KEY,wallet TEXT NOT NULL,at TEXT NOT NULL,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS followups(id TEXT PRIMARY KEY,selection_id TEXT NOT NULL REFERENCES evaluations(id),payload TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS immutable_followup_update BEFORE UPDATE ON followups BEGIN SELECT RAISE(ABORT,'immutable followup'); END;
CREATE TRIGGER IF NOT EXISTS immutable_followup_delete BEFORE DELETE ON followups BEGIN SELECT RAISE(ABORT,'immutable followup'); END;
CREATE TABLE IF NOT EXISTS latest(wallet TEXT PRIMARY KEY,evaluation_id TEXT NOT NULL REFERENCES evaluations(id));
CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY,wallet TEXT NOT NULL,evaluation_id TEXT NOT NULL REFERENCES evaluations(id),at TEXT NOT NULL,state TEXT NOT NULL,message_id TEXT,UNIQUE(evaluation_id));
CREATE TRIGGER IF NOT EXISTS immutable_eval_update BEFORE UPDATE ON evaluations BEGIN SELECT RAISE(ABORT,'immutable snapshot'); END;
CREATE TRIGGER IF NOT EXISTS immutable_eval_delete BEFORE DELETE ON evaluations BEGIN SELECT RAISE(ABORT,'immutable snapshot'); END;
PRAGMA user_version=1;
'''

class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=30)
        version = self.db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0,1):
            raise ValueError('Versão de estado não suportada')
        self.db.executescript(SCHEMA)
        # Migração aditiva: preservar origens já registadas sem perder observações simultâneas.
        with self.db:
            for wallet,token,at,payload in self.db.execute('SELECT wallet,token,at,payload FROM discovery_evidence').fetchall():
                identity=hashlib.sha256(dumps([wallet,token,at,payload]).encode()).hexdigest()
                self.db.execute('INSERT OR IGNORE INTO discovery_observations VALUES(?,?,?,?,?)',(identity,wallet,token,at,payload))
        self.prepaid = None
        self._provider_prepaid = {}

    def close(self): self.db.close()
    def __enter__(self): return self
    def __exit__(self,*args): self.close()

    def put_events(self, events):
        with self.db:
            for event in events:
                payload = dumps(asdict(event))
                old = self.db.execute('SELECT payload FROM events WHERE wallet=? AND id=?',(event.wallet,event.id)).fetchone()
                if old and old[0] != payload:
                    before=json.loads(old[0]);after=json.loads(payload)
                    old_multi=before.pop('multi_step',False);new_multi=after.pop('multi_step',False)
                    if before==after and not old_multi and new_multi:
                        self.db.execute('UPDATE events SET payload=? WHERE wallet=? AND id=?',(payload,event.wallet,event.id))
                        continue
                    raise ValueError('Evento existente com conteúdo divergente; reavaliar origem')
                self.db.execute('INSERT OR IGNORE INTO events VALUES(?,?,?,?)',(event.wallet,event.id,iso(event.timestamp),payload))

    def get_events(self,wallet,start,end):
        rows=self.db.execute('SELECT payload FROM events WHERE wallet=? AND at>=? AND at<=? ORDER BY at,id',(wallet,iso(start),iso(end)))
        result=[]
        for row in rows:
            p=json.loads(row[0]);p['timestamp']=parse_time(p['timestamp'])
            for key in ('quantity','usd_value','fee_usd'):
                if p[key] is not None:p[key]=Decimal(p[key])
            result.append(Event(**p))
        return result

    def record_discovery(self,wallet,token,when,evidence=None):
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO discoveries VALUES(?,?,?)',(wallet,token,iso(when)))
            if evidence is not None:
                encoded=dumps(evidence);at=iso(when)
                self.db.execute('INSERT OR IGNORE INTO discovery_evidence VALUES(?,?,?,?)',(wallet,token,at,encoded))
                identity=hashlib.sha256(dumps([wallet,token,at,encoded]).encode()).hexdigest()
                self.db.execute('INSERT OR IGNORE INTO discovery_observations VALUES(?,?,?,?,?)',(identity,wallet,token,at,encoded))
    def discovery_evidence(self,wallet,before=None):
        rows=self.db.execute('SELECT token,at,payload FROM discovery_observations WHERE wallet=? ORDER BY at,id',(wallet,))
        return [dict(token=row[0],observed_at=row[1],evidence=json.loads(row[2])) for row in rows if before is None or parse_time(row[1])<=before]
    def discovery_tokens(self,wallet):
        return [r[0] for r in self.db.execute('SELECT token FROM discoveries WHERE wallet=? ORDER BY token',(wallet,))]
    def discoveries(self):
        return [dict(wallet=r[0],token=r[1],discovered_at=r[2]) for r in self.db.execute('SELECT wallet,token,at FROM discoveries ORDER BY at')]
    def wallets(self):
        return [r[0] for r in self.db.execute('SELECT wallet FROM discoveries UNION SELECT wallet FROM latest ORDER BY wallet')]
    def get_cursor(self,key):
        row=self.db.execute('SELECT value FROM cursors WHERE key=?',(key,)).fetchone();return row[0] if row else None
    def set_cursor(self,key,value):
        with self.db:self.db.execute('INSERT OR REPLACE INTO cursors VALUES(?,?)',(key,str(value)))
    def cache_get(self,key,now):
        row=self.db.execute('SELECT payload,expires FROM cache WHERE key=?',(key,)).fetchone()
        return json.loads(_unpack_payload(row[0])) if row and parse_time(row[1])>now else None
    def cache_put(self,key,value,expires_at):
        with self.db:self.db.execute('INSERT OR REPLACE INTO cache VALUES(?,?,?)',(key,iso(expires_at),_pack_payload(dumps(value))))
    def prune_cache(self,now):
        with self.db:
            cursor=self.db.execute('DELETE FROM cache WHERE expires<=?',(iso(now),))
            return cursor.rowcount

    def record_api_failure(self,source,endpoint,status,when,diagnostic):
        """Mensagem já redigida: apenas cofre privado, nunca relatórios ou Telegram."""
        if source != 'birdeye' or status not in (400,401,402,403,422,429):
            raise ValueError('Diagnóstico fora do âmbito permitido')
        payload=dumps({'http_status':status,'diagnostic':diagnostic})
        at=iso(when)
        identity=hashlib.sha256(dumps([source,endpoint,at,payload]).encode()).hexdigest()
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO api_failures VALUES(?,?,?,?,?)',
                            (identity,source,endpoint,at,payload))
        return identity

    def reserve_budget(self,month,cost,ceiling,namespace='birdeye'):
        if namespace not in ('birdeye','helius'):raise ValueError('Fornecedor não permitido')
        if isinstance(cost,bool) or not isinstance(cost,int) or cost<0:raise ValueError('Crédito inválido')
        prepaid=self.prepaid if namespace=='birdeye' else self._provider_prepaid.get(namespace)
        if prepaid is not None:
            if prepaid['month']!=month or cost>prepaid['remaining']:return False
            prepaid['remaining']-=cost
            return True
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if namespace=='birdeye':
                row=self.db.execute('SELECT credits FROM budgets WHERE month=?',(month,)).fetchone()
            else:
                row=self.db.execute('SELECT credits FROM provider_budgets WHERE namespace=? AND month=?',(namespace,month)).fetchone()
            used=row[0] if row else 0
            if used+cost>ceiling:return False
            if namespace=='birdeye':self.db.execute('INSERT OR REPLACE INTO budgets VALUES(?,?)',(month,used+cost))
            else:self.db.execute('INSERT OR REPLACE INTO provider_budgets VALUES(?,?,?)',(namespace,month,used+cost))
        return True
    def prepay_run(self,month,credits,ceiling,checkpoint,namespace='birdeye'):
        existing=self.prepaid if namespace=='birdeye' else self._provider_prepaid.get(namespace)
        if existing is not None:raise ValueError('Reserva já existente')
        if not self.reserve_budget(month,credits,ceiling,namespace):return False
        checkpoint()  # reserva durável ANTES de qualquer pedido, sem devolução após crash
        reservation={'month':month,'remaining':credits}
        if namespace=='birdeye':self.prepaid=reservation
        else:self._provider_prepaid[namespace]=reservation
        return True

    def record_token_selection(self,token,at,evidence):
        payload=dumps(evidence);identity=hashlib.sha256(dumps([token,iso(at),payload]).encode()).hexdigest()
        with self.db:self.db.execute('INSERT OR IGNORE INTO token_selections VALUES(?,?,?,?)',(identity,token,iso(at),payload))
    def discovered_tokens(self):
        return [r[0] for r in self.db.execute('SELECT DISTINCT token FROM discoveries ORDER BY token')]

    def put_source_transactions(self,source,address,rows):
        if source!='helius':raise ValueError('Origem bruta não permitida')
        with self.db:
            for row in rows:
                signatures=(row.get('transaction') or {}).get('signatures')
                signature=signatures[0] if isinstance(signatures,list) and signatures else row.get('signature')
                if not isinstance(signature,str) or not signature:raise ValueError('Transação sem assinatura')
                at=iso(parse_time(row.get('blockTime')))
                payload=dumps(row)
                old=self.db.execute('SELECT payload FROM source_transactions WHERE source=? AND address=? AND signature=?',(source,address,signature)).fetchone()
                if old and _unpack_payload(old[0])!=payload:raise ValueError('Transação de origem divergente')
                self.db.execute('INSERT OR IGNORE INTO source_transactions VALUES(?,?,?,?,?)',(source,address,signature,at,_pack_payload(payload)))
    def get_source_transactions(self,source,address,start,end):
        return [json.loads(_unpack_payload(r[0])) for r in self.db.execute('SELECT payload FROM source_transactions WHERE source=? AND address=? AND at>=? AND at<=? ORDER BY at,signature',(source,address,iso(start),iso(end)))]
    def put_source_events(self,source,events):
        if source!='helius':raise ValueError('Origem de eventos não permitida')
        with self.db:
            for event in events:
                if event.source!=source:raise ValueError('Origem de evento incoerente')
                payload=dumps(asdict(event))
                old=self.db.execute('SELECT payload FROM source_events WHERE source=? AND wallet=? AND id=?',(source,event.wallet,event.id)).fetchone()
                if old and old[0]!=payload:raise ValueError('Evento de origem divergente')
                self.db.execute('INSERT OR IGNORE INTO source_events VALUES(?,?,?,?,?)',(source,event.wallet,event.id,iso(event.timestamp),payload))
    def get_source_events(self,source,wallet,start,end):
        result=[]
        for row in self.db.execute('SELECT payload FROM source_events WHERE source=? AND wallet=? AND at>=? AND at<=? ORDER BY at,id',(source,wallet,iso(start),iso(end))):
            p=json.loads(row[0]);p['timestamp']=parse_time(p['timestamp'])
            for key in ('quantity','usd_value','fee_usd'):
                if p[key] is not None:p[key]=Decimal(p[key])
            result.append(Event(**p))
        return result
    def get_analysis_events(self,wallet,start,end):
        from .reconciliation import reconcile
        return reconcile(self.get_events(wallet,start,end),self.get_source_events('helius',wallet,start,end))[0]

    def save_evaluation(self,evaluation):
        payload=evaluation.to_dict();payload['id']=''
        encoded=dumps(payload)
        snapshot_id=hashlib.sha256(encoded.encode()).hexdigest()
        evaluation.id=snapshot_id
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO evaluations VALUES(?,?,?,?)',(snapshot_id,evaluation.wallet,iso(evaluation.evaluated_at),encoded))
            previous=self.db.execute('SELECT e.id,e.at,e.payload FROM latest l JOIN evaluations e ON l.evaluation_id=e.id WHERE l.wallet=?',(evaluation.wallet,)).fetchone()
            update=not previous or evaluation.evaluated_at>parse_time(previous[1])
            if previous and evaluation.evaluated_at==parse_time(previous[1]):
                prior=decode_evaluation(json.loads(previous[2]))
                update=(previous[0]==snapshot_id or (prior.eligible and not evaluation.eligible))
            if update:
                self.db.execute('INSERT OR REPLACE INTO latest VALUES(?,?)',(evaluation.wallet,snapshot_id))
        return snapshot_id
    def save_followup(self,observation):
        encoded=dumps(observation);snapshot_id=hashlib.sha256(encoded.encode()).hexdigest()
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO followups VALUES(?,?,?)',(snapshot_id,observation['selection_id'],encoded))
        return snapshot_id

    def get_evaluation(self,snapshot_id):
        row=self.db.execute('SELECT payload FROM evaluations WHERE id=?',(snapshot_id,)).fetchone()
        if not row:return None
        if hashlib.sha256(row[0].encode()).hexdigest()!=snapshot_id:raise ValueError('Fotografia inválida')
        result=decode_evaluation(json.loads(row[0]));result.id=snapshot_id;return result
    def latest_evaluations(self):
        return [self.get_evaluation(r[0]) for r in self.db.execute('SELECT evaluation_id FROM latest ORDER BY wallet')]
    def latest_id(self,wallet):
        row=self.db.execute('SELECT evaluation_id FROM latest WHERE wallet=?',(wallet,)).fetchone();return row[0] if row else None
    def last_notification(self,wallet):
        row=self.db.execute("SELECT evaluation_id,at,state FROM notifications WHERE wallet=? AND state!='cancelled' ORDER BY id DESC LIMIT 1",(wallet,)).fetchone()
        return dict(evaluation_id=row[0],at=parse_time(row[1]),state=row[2]) if row else None
    def reserve_notification(self,evaluation,at):
        try:
            with self.db:
                self.db.execute('INSERT INTO notifications(wallet,evaluation_id,at,state) VALUES(?,?,?,?)',(evaluation.wallet,evaluation.id,iso(at),'pending'))
            return True
        except sqlite3.IntegrityError:return False
    def finish_notification(self,snapshot_id,state,message_id=None):
        with self.db:self.db.execute('UPDATE notifications SET state=?,message_id=? WHERE evaluation_id=?',(state,str(message_id) if message_id is not None else None,snapshot_id))


def decode_evaluation(p):
    for key in ('evaluated_at',):p[key]=parse_time(p[key])
    c=p['coverage']
    for key in ('start','end','fetched_at'):c[key]=parse_time(c[key])
    p['coverage']=Coverage(**c)
    for c in p['checks']:
        c['status']=Status(c['status'])
        for key in ('period_start','period_end','evaluated_at'):c[key]=parse_time(c[key])
    p['checks']=[Check(**c) for c in p['checks']]
    for t in p['tokens']:
        for key in ('realized','identified_realized','costs','realized_cost','fees','open_quantity','open_cost','unrealized','unknown_cost_quantity'):
            if key in t and t[key] is not None:t[key]=Decimal(t[key])
        t['entries']=[Decimal(v) for v in t['entries']]
        t['weekly']={k:Decimal(v) for k,v in t['weekly'].items()}
    p['tokens']=[TokenResult(**t) for t in p['tokens']]
    return Evaluation(**p)

@contextmanager
def process_lock(db_path):
    """Um processo por DB; GitHub concurrency protege execuções remotas."""
    import fcntl
    path=Path(str(db_path)+'.lock');path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:yield
        finally:fcntl.flock(handle,fcntl.LOCK_UN)
