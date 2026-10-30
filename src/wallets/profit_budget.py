"""Reservas duráveis por estágio; devolução só de créditos nunca tentados.

O ledger é partilhado com a rotina principal. Cada tentativa continua a ser
cobrada antes da rede pelo Store dos clientes existentes. Um timeout não devolve
essa tentativa; uma interrupção de processo conserva toda a reserva por fechar.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
from uuid import uuid4

from .models import iso, parse_time, utcnow


class ProfitBudgetError(RuntimeError):
    pass


SCHEMA = '''
CREATE TABLE IF NOT EXISTS profit_stage_reservations(
 id TEXT NOT NULL, stage TEXT NOT NULL, namespace TEXT NOT NULL,
 month TEXT NOT NULL, reserved INTEGER NOT NULL CHECK(reserved>=0),
 attempted INTEGER, refunded INTEGER, state TEXT NOT NULL,
 created_at TEXT NOT NULL, closed_at TEXT,
 PRIMARY KEY(id,namespace)
);
'''


def check_state_space(path, *, max_bytes=128 * 1024 * 1024,
                      minimum_free_bytes=512 * 1024 * 1024):
    """Falhar antes de reservar quota se o estado/disco não tiverem margem."""
    path = Path(path)
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ProfitBudgetError('Limite de estado inválido.')
    if type(minimum_free_bytes) is not int or minimum_free_bytes < 0:
        raise ProfitBudgetError('Margem de disco inválida.')
    size = sum(candidate.stat().st_size for candidate in
               (path, Path(str(path)+'-wal'), Path(str(path)+'-journal'))
               if candidate.exists())
    if size > max_bytes:
        raise ProfitBudgetError('Estado sem margem; nenhuma quota foi reservada.')
    directory = path.parent
    while not directory.exists() and directory != directory.parent:
        directory = directory.parent
    if shutil.disk_usage(directory).free < minimum_free_bytes + 2*size:
        raise ProfitBudgetError('Disco sem margem; nenhuma quota foi reservada.')
    return {'state_bytes': size, 'state_limit_bytes': max_bytes}


@dataclass
class _Ticket:
    identity: str
    name: str
    grants: dict
    store: object
    clients: dict
    requests_before: dict
    closed: bool = False


class ProfitBudget:
    def __init__(self, production_store, checkpoint_production, config, *, now=None,
                 space_check=None):
        if not callable(checkpoint_production):
            raise ProfitBudgetError('Checkpoint durável obrigatório.')
        if production_store.prepaid is not None or production_store._provider_prepaid:
            raise ProfitBudgetError('O ledger central não pode reutilizar outra reserva.')
        self.source = production_store
        self.checkpoint = checkpoint_production
        self.config = config
        self.now = parse_time(now or utcnow())
        self.month = self.now.strftime('%Y-%m')
        self.space_check = space_check or check_state_space
        self._stats = {}
        self._allocated = {'birdeye': 0, 'helius': 0}
        self._active = False
        self._halted = False
        self.source.db.executescript(SCHEMA)

    @property
    def stats(self):
        return deepcopy(self._stats)

    def preflight(self, path):
        settings = self.config.get('profit', {})
        return self.space_check(path,
            max_bytes=settings.get('max_state_bytes', 128*1024*1024),
            minimum_free_bytes=settings.get('minimum_free_disk_bytes', 512*1024*1024))

    def bind_store(self, store):
        """Sem um estágio ativo, os clientes não têm créditos para fazer rede."""
        if self._active:
            raise ProfitBudgetError('Já existe um estágio ativo.')
        store.prepaid = {'month': self.month, 'remaining': 0}
        store._provider_prepaid['helius'] = {'month': self.month, 'remaining': 0}

    def _limits(self, namespace):
        settings = self.config['api'] if namespace == 'birdeye' else self.config.get('helius', {})
        return settings.get('max_credits_per_run', 0), settings.get('monthly_credit_ceiling', 0)

    def _used(self, namespace):
        if namespace == 'birdeye':
            row = self.source.db.execute('SELECT credits FROM budgets WHERE month=?', (self.month,)).fetchone()
        else:
            row = self.source.db.execute('SELECT credits FROM provider_budgets WHERE namespace=? AND month=?',
                                         (namespace, self.month)).fetchone()
        return row[0] if row else 0

    def _open(self, name, amounts, store, clients):
        if self._halted or self._active:
            raise ProfitBudgetError('Experiência sem autorização para outro estágio.')
        if not isinstance(name, str) or not re.fullmatch('[a-z][a-z0-9_]{0,23}', name) or name in self._stats:
            raise ProfitBudgetError('Estágio inválido ou repetido.')
        self.preflight(store.path)
        for namespace, amount in amounts.items():
            maximum, ceiling = self._limits(namespace)
            if type(amount) is not int or amount < 0 or self._allocated[namespace]+amount > maximum:
                raise ProfitBudgetError('Orçamento por execução excedido antes da rede.')
            if amount and self._used(namespace)+amount > ceiling:
                raise ProfitBudgetError('Quota mensal partilhada insuficiente; estágio adiado.')
        if store.prepaid is not None and store.prepaid.get('remaining', 0):
            raise ProfitBudgetError('Reserva experimental anterior ainda aberta.')
        if any(value.get('remaining', 0) for value in store._provider_prepaid.values()):
            raise ProfitBudgetError('Reserva experimental anterior ainda aberta.')
        identity = uuid4().hex
        grants = {}
        self._active = True
        try:
            # O processo/workflow possui a exclusão mútua do cofre. A verificação
            # de todas as fontes precede a primeira reserva para não gastar a
            # quota Birdeye quando a reserva Helius já seria impossível.
            for namespace, amount in amounts.items():
                if amount and not self.source.reserve_budget(self.month, amount, self._limits(namespace)[1], namespace):
                    raise ProfitBudgetError('Não foi possível reservar a quota partilhada.')
                grants[namespace] = {'month': self.month, 'remaining': amount}
                with self.source.db:
                    self.source.db.execute('INSERT INTO profit_stage_reservations VALUES(?,?,?,?,?,NULL,NULL,?,?,NULL)',
                        (identity, name, namespace, self.month, amount, 'reserved', iso(self.now)))
            self.checkpoint()  # Antes de conceder créditos a qualquer cliente.
        except BaseException:
            self._halted = True
            self._active = False
            raise
        for namespace, amount in amounts.items():
            self._allocated[namespace] += amount
        store.prepaid = grants['birdeye']
        store._provider_prepaid['helius'] = grants['helius']
        counters = {namespace: getattr(client, 'requests', 0) for namespace, client in clients.items()}
        self._stats[name] = {'state': 'reserved', **{
            namespace: {'reserved': amount, 'attempted': 0, 'refunded': 0, 'requests': 0}
            for namespace, amount in amounts.items()}}
        return _Ticket(identity, name, grants, store, clients, counters)

    def _close(self, ticket):
        if ticket.closed:
            return
        unused = {}
        try:
            for namespace, grant in ticket.grants.items():
                amount = self._stats[ticket.name][namespace]['reserved']
                remaining = grant.get('remaining')
                if grant.get('month') != self.month or type(remaining) is not int or not 0 <= remaining <= amount:
                    raise ProfitBudgetError('Consumo de reserva incoerente; não reembolsar.')
                unused[namespace] = remaining
                self._stats[ticket.name][namespace]['attempted'] = amount-remaining
                client = ticket.clients.get(namespace)
                if client is not None:
                    requests = getattr(client, 'requests', 0)-ticket.requests_before.get(namespace, 0)
                    if type(requests) is not int or requests < 0:
                        raise ProfitBudgetError('Contador de pedidos incoerente.')
                    self._stats[ticket.name][namespace]['requests'] = requests
        except BaseException:
            self._halted = True
            ticket.closed = True
            self._stats[ticket.name]['state'] = 'invalid_grant'
            raise
        finally:
            # Selar também o dicionário que o cliente recebeu: guardar uma
            # referência à reserva não permite reutilizar créditos devolvidos.
            for grant in ticket.grants.values():
                grant['remaining'] = 0
            ticket.store.prepaid = {'month': self.month, 'remaining': 0}
            ticket.store._provider_prepaid['helius'] = {'month': self.month, 'remaining': 0}
            self._active = False
        try:
            with self.source.db:
                self.source.db.execute('BEGIN IMMEDIATE')
                for namespace, amount in unused.items():
                    row = self.source.db.execute('SELECT reserved,state FROM profit_stage_reservations WHERE id=? AND namespace=?',
                                                  (ticket.identity, namespace)).fetchone()
                    if not row or row[0] != self._stats[ticket.name][namespace]['reserved']:
                        raise ProfitBudgetError('Reserva central não corresponde ao estágio.')
                    if row[1] == 'settled':
                        continue
                    if row[1] != 'reserved':
                        raise ProfitBudgetError('Estado de reserva desconhecido.')
                    if amount:
                        if namespace == 'birdeye':
                            cursor = self.source.db.execute('UPDATE budgets SET credits=credits-? WHERE month=? AND credits>=?',
                                                            (amount, self.month, amount))
                        else:
                            cursor = self.source.db.execute('UPDATE provider_budgets SET credits=credits-? WHERE namespace=? AND month=? AND credits>=?',
                                                            (amount, namespace, self.month, amount))
                        if cursor.rowcount != 1:
                            raise ProfitBudgetError('Ledger central incoerente; reembolso recusado.')
                    self.source.db.execute('UPDATE profit_stage_reservations SET attempted=?,refunded=?,state=?,closed_at=? WHERE id=? AND namespace=?',
                        (row[0]-amount, amount, 'settled', iso(utcnow()), ticket.identity, namespace))
            self.checkpoint()
            for namespace, amount in unused.items():
                self._stats[ticket.name][namespace]['refunded'] = amount
            self._stats[ticket.name]['state'] = 'settled'
        except BaseException:
            self._halted = True
            self._stats[ticket.name]['state'] = 'checkpoint_or_ledger_failed'
            raise
        finally:
            ticket.closed = True

    @contextmanager
    def stage(self, name, *, birdeye=0, helius=0, store, clients=None):
        ticket = self._open(name, {'birdeye': birdeye, 'helius': helius}, store, clients or {})
        try:
            yield self._stats[name]
        finally:
            self._close(ticket)
