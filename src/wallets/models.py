"""Contrato interno: valores monetários Decimal, tempo UTC e desconhecido explícito."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
import json

UTC = timezone.utc
LIVE_SOURCES = frozenset({'birdeye','helius','birdeye+helius'})
EVALUATION_SOURCES = frozenset({'birdeye','birdeye+helius'})

def allowed_event_sources(source):
    return LIVE_SOURCES if source=='birdeye+helius' else {source}

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
OBSERVED_REQUIRED_CHECKS = (
    'observed_data', 'observed_sample', 'observed_accounting',
    'observed_consistency', 'observed_concentration', 'observed_scope',
    'observed_automation', 'observed_followability',
)
OBSERVED_UNVERIFIED_NAMES = (
    'opening_inventory', 'fees_usd', 'global_coverage', 'open_positions',
    'liquidity', 'execution', 'coordination', 'labels',
)
_CHECK_FIELDS = frozenset((
    'name', 'status', 'reason', 'evidence', 'period_start', 'period_end',
    'coverage', 'evaluated_at', 'criteria_version',
))

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
    def strict_eligible(self):
        """Resultado das oito verificações integrais, preservado para auditoria."""
        return (len(self.checks) == len(REQUIRED_CHECKS)
                and {c.name for c in self.checks} == set(REQUIRED_CHECKS)
                and all(c.status == Status.PASS for c in self.checks))

    @property
    def selection_policy(self):
        """Snapshots antigos continuam estritos; uma política inválida não herda isso."""
        if not isinstance(self.metrics, dict):
            return 'invalid'
        if 'selection' not in self.metrics:
            return 'strict-v1'
        selection = self.metrics['selection']
        policy = selection.get('policy') if isinstance(selection, dict) else None
        return policy if isinstance(policy, str) and policy else 'invalid'

    @property
    def decision_checks(self):
        """Verificações da política aplicada, sem aceitar flags de decisão guardadas."""
        if isinstance(self.metrics, dict) and 'selection' not in self.metrics:
            return list(self.checks)
        if self.selection_policy != 'observed-v1':
            return []
        selection = self.metrics['selection']
        rows = selection.get('checks')
        if not isinstance(rows, list):
            return []
        try:
            start = parse_time(self.coverage.start)
            end = parse_time(self.coverage.end)
            fetched = parse_time(self.coverage.fetched_at)
            evaluated = parse_time(self.evaluated_at)
            if not start < end <= fetched <= evaluated:
                return []
            checks = []
            for row in rows:
                if not isinstance(row, dict) or set(row) != _CHECK_FIELDS:
                    return []
                if any(not isinstance(row[key], str) or not row[key].strip()
                       for key in ('name', 'reason', 'criteria_version')):
                    return []
                if not isinstance(row['evidence'], dict) or not isinstance(row['coverage'], dict):
                    return []
                dumps(row['evidence'])  # A evidência tem de sobreviver a persistência JSON.
                # Estes campos são datas do contrato Check; números não são
                # uma representação admitida para decisões persistidas.
                if any(not isinstance(row[key], (str, datetime))
                       for key in ('period_start', 'period_end', 'evaluated_at')):
                    return []
                check = Check(
                    row['name'], Status(row['status']), row['reason'], dict(row['evidence']),
                    parse_time(row['period_start']), parse_time(row['period_end']),
                    dict(row['coverage']), parse_time(row['evaluated_at']), row['criteria_version'],
                )
                if (check.period_start != start or check.period_end != end
                        or check.evaluated_at != evaluated
                        or check.criteria_version != self.criteria_version
                        or dumps(check.coverage) != dumps(asdict(self.coverage))):
                    return []
                checks.append(check)
            return checks
        except (ValueError, TypeError, KeyError, OverflowError, AttributeError, RecursionError):
            return []

    @property
    def unverified_checks(self):
        """Limitações explícitas do modo observado, nunca implícitas em um passe."""
        if self.selection_policy != 'observed-v1':
            return []
        rows = self.metrics['selection'].get('unverified')
        if not isinstance(rows, list) or not rows:
            return []
        result = []
        names = set()
        for row in rows:
            if (not isinstance(row, dict)
                    or not isinstance(row.get('name'), str) or not row['name'].strip()
                    or not isinstance(row.get('reason'), str) or not row['reason'].strip()
                    or row['name'] in names):
                return []
            names.add(row['name'])
            result.append({'name': row['name'], 'reason': row['reason']})
        if names != set(OBSERVED_UNVERIFIED_NAMES):
            return []
        return result

    @property
    def eligible(self):
        # O envio continua a exigir as oito verificações integrais. A seleção
        # observada é separada para investigação e pré-visualização local.
        return self.strict_eligible

    @property
    def observed_eligible(self):
        """Seleção para investigação local; não autoriza notificações."""
        if any(check.status == Status.FAIL for check in self.checks):
            return False
        if not isinstance(self.metrics, dict):
            return False
        if 'selection' not in self.metrics:
            return False
        if self.selection_policy != 'observed-v1':
            return False
        selection = self.metrics['selection']
        scope = selection.get('scope')
        if (not isinstance(selection.get('metrics'), dict)
                or not isinstance(scope, str) or not scope.strip()
                or not self.unverified_checks):
            return False
        try:
            dumps(selection['metrics'])
        except (ValueError, TypeError, OverflowError, RecursionError):
            return False
        checks = self.decision_checks
        return (len(checks) == len(OBSERVED_REQUIRED_CHECKS)
                and {check.name for check in checks} == set(OBSERVED_REQUIRED_CHECKS)
                and all(check.status == Status.PASS for check in checks))

    def to_dict(self):
        return asdict(self)
