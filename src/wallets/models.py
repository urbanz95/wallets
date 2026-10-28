"""Contrato interno: valores monetários Decimal, tempo UTC e desconhecido explícito."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
import json

UTC = timezone.utc
SOL = 'So11111111111111111111111111111111111111112'

def utcnow():
    return datetime.now(UTC)

def iso(value):
    if value.tzinfo is None:
        raise ValueError('Data sem fuso horário')
    return value.astimezone(UTC).isoformat()

def parse_time(value):
    if isinstance(value,bool):raise ValueError('Data inválida')
    if isinstance(value, datetime):
        iso(value)
        return value.astimezone(UTC)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, UTC)
    if not isinstance(value,str):raise ValueError('Formato de data inválido')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    iso(result)
    return result.astimezone(UTC)

def json_default(value):
    if isinstance(value, Decimal): return str(value)
    if isinstance(value, datetime): return iso(value)
    if isinstance(value, Enum): return value.value
    if isinstance(value, set): return sorted(value)
    raise TypeError(type(value).__name__)

def dumps(value):
    return json.dumps(value, default=json_default, ensure_ascii=False, sort_keys=True, allow_nan=False)

class Status(str, Enum):
    PASS = 'APROVADA'
    FAIL = 'REPROVADA'
    UNKNOWN = 'INDETERMINADA'

REQUIRED_CHECKS = ('data_quality', 'sample', 'accounting', 'consistency', 'concentration', 'open_risk', 'automation', 'followability')

@dataclass(frozen=True)
class Event:
    id: str
    wallet: str
    token: str
    timestamp: datetime
    kind: str  # buy/sell/transfer_in/transfer_out/airdrop/liquidity/failed/unknown/wrap
    quantity: Decimal
    usd_value: Decimal | None = None  # valor histórico, nunca preço atual retroativo
    fee_usd: Decimal | None = None
    tx_hash: str = ''
    block: int | None = None
    source: str = 'fixture'
    multi_step: bool = False

@dataclass
class Coverage:
    start: datetime
    end: datetime
    fetched_at: datetime
    source: str = 'fixture'
    complete: bool = False
    truncated: bool = False
    all_tokens: bool = False
    opening_inventory_known: bool = False
    fees_complete: bool = False
    marks_complete: bool = False
    liquidity_complete: bool = False
    execution_complete: bool = False
    coordination_complete: bool = False
    labels_complete: bool = False
    failures: list[str] = field(default_factory=list)

@dataclass
class TokenResult:
    token: str
    realized: Decimal | None = None
    identified_realized: Decimal = Decimal('0')
    costs: Decimal = Decimal('0')
    fees: Decimal = Decimal('0')
    realized_cost: Decimal = Decimal('0')
    open_quantity: Decimal = Decimal('0')
    open_cost: Decimal | None = None
    unrealized: Decimal | None = None
    unknown_cost_quantity: Decimal = Decimal('0')
    entries: list[Decimal] = field(default_factory=list)
    holding_seconds: list[float] = field(default_factory=list)
    weekly: dict[str, Decimal] = field(default_factory=dict)
    reliable: bool = False

@dataclass
class Check:
    name: str
    status: Status
    reason: str
    evidence: dict
    period_start: datetime
    period_end: datetime
    coverage: dict
    evaluated_at: datetime
    criteria_version: str

@dataclass
class Evaluation:
    wallet: str
    evaluated_at: datetime
    criteria_version: str
    coverage: Coverage
    checks: list[Check]
    metrics: dict
    tokens: list[TokenResult] = field(default_factory=list)
    discovery_tokens: list[str] = field(default_factory=list)
    source: str = 'fixture'
    id: str = ''

    @property
    def eligible(self):
        return (len(self.checks) == len(REQUIRED_CHECKS)
                and {c.name for c in self.checks} == set(REQUIRED_CHECKS)
                and all(c.status == Status.PASS for c in self.checks))

    def to_dict(self):
        return asdict(self)
