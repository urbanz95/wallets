"""Pedido manual autenticado/cifrado para Actions; nenhum endereço em claro no input."""
from datetime import timedelta
from hashlib import sha256
import json
from uuid import uuid4
from cryptography.fernet import Fernet,InvalidToken
from .collection import valid_address
from .models import dumps,iso,parse_time
from .profit_queue import enqueue

PURPOSE='wallets-profit-manual-v1'


def encode_request(key,wallet,now):
    if not valid_address(wallet):raise ValueError('Endereço inválido')
    body={'purpose':PURPOSE,'wallet':wallet,'issued_at':iso(now),
          'expires_at':iso(now+timedelta(hours=12)),'nonce':uuid4().hex}
    return Fernet(key.encode() if isinstance(key,str) else key).encrypt(dumps(body).encode()).decode()


def accept_request(store,key,ciphertext,now):
    if not ciphertext:return False
    if not isinstance(ciphertext,str) or len(ciphertext)>2048:raise ValueError('Pedido cifrado inválido')
    try:
        raw=Fernet(key.encode() if isinstance(key,str) else key).decrypt(ciphertext.encode())
        body=json.loads(raw)
        issued,expires=parse_time(body['issued_at']),parse_time(body['expires_at'])
        if (body.get('purpose')!=PURPOSE or not valid_address(body.get('wallet'))
                or not isinstance(body.get('nonce'),str) or len(body['nonce'])!=32
                or not issued<=now+timedelta(seconds=60) or not now<=expires
                or not timedelta(0)<expires-issued<=timedelta(hours=12)):
            raise ValueError
    except (InvalidToken,ValueError,TypeError,KeyError,OverflowError):
        raise ValueError('Pedido manual não autenticado, inválido ou expirado') from None
    identity='profit-request:'+sha256(ciphertext.encode()).hexdigest()
    if store.get_cursor(identity):return False
    enqueue(store,[{'wallet':body['wallet'],'origins':[],'discovery':{'source':'manual'}}],now,manual=True)
    store.set_cursor(identity,iso(now))
    return True
