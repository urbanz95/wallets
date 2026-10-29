"""Resultados brutos das operações observadas, separados da aprovação estrita.

Só entram na amostra os tokens cujo fluxo observado fecha a quantidade a zero,
com aquisições e vendas USD conhecidas e sem movimentos não reconciliados. Isto
não prova o inventário global nem o desempenho de toda a carteira. Taxas ficam
separadas: ``realized`` dos TokenResult internos representa exclusivamente o
resultado BRUTO antes de taxas; nunca deve substituir Evaluation.tokens/FIFO
estrito ou ser apresentado como lucro líquido.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from .analysis import SETTLEMENT_TOKENS
from .models import Event, TokenResult, UTC, parse_time

ZERO = Decimal('0')


@dataclass
class _ObservedLot:
    quantity: Decimal
    cost: Decimal | None
    timestamp: datetime


def _decimal(value, label):
    if isinstance(value, (float, bool)):
        raise ValueError(f'{label}: usar Decimal, não float/bool')
    try:
        amount = value if isinstance(value, Decimal) else Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f'{label}: valor inválido') from exc
    if not amount.is_finite() or amount < ZERO:
        raise ValueError(f'{label}: valor negativo ou não finito')
    return amount


def _week(timestamp):
    day = timestamp.astimezone(UTC).date()
    return (day - timedelta(days=day.weekday())).isoformat()


def _consume(lots, quantity):
    remaining = quantity
    consumed = []
    while lots and remaining > ZERO:
        lot = lots[0]
        take = min(lot.quantity, remaining)
        basis = None if lot.cost is None else (
            lot.cost if take == lot.quantity else lot.cost * take / lot.quantity)
        consumed.append((take, basis, lot.timestamp))
        lot.quantity -= take
        if basis is not None:
            lot.cost -= basis
        remaining -= take
        if lot.quantity == ZERO:
            lots.popleft()
    return consumed, remaining


def summarize_observed(events, start, end):
    """Resumir fluxos observados sem usar custos desconhecidos como zero.

    ``token_results`` contém apenas tokens CLOSED. OPEN e UNKNOWN ficam no
    denominador e nas exclusões explícitas. ``gross_result_before_fees`` no topo
    é a soma CLOSED, ou None sem amostra; não inclui resultados de tokens abertos.
    ``fees_complete`` refere-se apenas à presença de fee_usd nos eventos dados,
    nunca à totalidade dos custos económicos da carteira.
    """
    start, end = parse_time(start), parse_time(end)
    if start >= end:
        raise ValueError('Janela observada inválida')
    unique = {}
    wallets = set()
    for event in events:
        if not isinstance(event, Event):
            raise ValueError('A contabilidade observada exige eventos normalizados')
        if any(not isinstance(value, str) or not value for value in (event.wallet, event.token, event.id)):
            raise ValueError('Evento sem identidade, carteira ou token')
        timestamp = parse_time(event.timestamp)
        if not start <= timestamp <= end:
            raise ValueError('Evento fora da janela ou com informação futura')
        _decimal(event.quantity, 'Quantidade')
        if event.usd_value is not None:
            _decimal(event.usd_value, 'Valor histórico USD')
        if event.fee_usd is not None:
            _decimal(event.fee_usd, 'Taxa USD')
        identity = (event.wallet, event.id)
        previous = unique.get(identity)
        if previous is not None and previous != event:
            raise ValueError('Evento duplicado com conteúdo divergente')
        unique[identity] = event
        wallets.add(event.wallet)
    if len(wallets) > 1:
        raise ValueError('Não misturar carteiras na contabilidade observada')

    ordered = sorted(unique.values(), key=lambda event: (parse_time(event.timestamp), event.id))
    known_fees = sum((_decimal(event.fee_usd, 'Taxa USD') for event in ordered
                      if event.fee_usd is not None), ZERO)
    fees_complete = bool(ordered) and all(event.fee_usd is not None for event in ordered)
    unclassified = {event.tx_hash or event.id for event in ordered
                    if event.multi_step or event.kind in ('unknown', 'liquidity')}
    grouped = defaultdict(list)
    for event in ordered:
        if event.token not in SETTLEMENT_TOKENS and event.kind not in ('failed', 'wrap'):
            grouped[event.token].append(event)

    closed = []
    per_token = []
    for token, token_events in sorted(grouped.items()):
        lots = deque()
        reasons = []
        gross = ZERO
        bought_cost = ZERO
        sold_cost = ZERO
        entries = []
        holdings = []
        weekly = defaultdict(lambda: ZERO)
        buys = sells = 0
        timestamps = Counter(parse_time(event.timestamp) for event in token_events
                             if event.kind in ('buy', 'sell'))
        if any(count > 1 for count in timestamps.values()):
            reasons.append('Várias operações no mesmo segundo sem ordenação económica demonstrada.')
        for event in token_events:
            quantity = _decimal(event.quantity, 'Quantidade')
            value = None if event.usd_value is None else _decimal(event.usd_value, 'Valor histórico USD')
            timestamp = parse_time(event.timestamp)
            if event.multi_step or event.kind in ('unknown', 'liquidity'):
                reasons.append('Operação não interpretável, liquidez ou swap com várias etapas não reconciliadas.')
                continue
            if event.kind == 'buy':
                buys += 1
                if quantity == ZERO:
                    reasons.append('Compra com quantidade nula.')
                    continue
                if value is None or value <= ZERO:
                    reasons.append('Compra sem custo histórico USD positivo demonstrado.')
                    lots.append(_ObservedLot(quantity, None, timestamp))
                else:
                    bought_cost += value
                    entries.append(value)
                    lots.append(_ObservedLot(quantity, value, timestamp))
            elif event.kind in ('transfer_in', 'airdrop'):
                reasons.append('Receção por transferência/distribuição com custo de aquisição desconhecido.')
                if quantity > ZERO:
                    lots.append(_ObservedLot(quantity, None, timestamp))
            elif event.kind in ('sell', 'transfer_out'):
                if event.kind == 'sell':
                    sells += 1
                else:
                    reasons.append('Transferência de saída: fluxo de aquisição/venda não reconciliado.')
                parts, missing = _consume(lots, quantity)
                if missing:
                    reasons.append('Saída sem inventário observado suficiente; aquisição anterior desconhecida.')
                if event.kind == 'transfer_out':
                    continue
                if quantity == ZERO or value is None:
                    reasons.append('Venda sem quantidade positiva ou valor histórico USD conhecido.')
                    continue
                if any(basis is None for _, basis, _ in parts):
                    reasons.append('Venda de uma aquisição com custo desconhecido.')
                if missing or any(basis is None for _, basis, _ in parts):
                    continue
                basis = sum((basis for _, basis, _ in parts), ZERO)
                sold_cost += basis
                outcome = value - basis
                gross += outcome
                weekly[_week(timestamp)] += outcome
                holdings.extend((timestamp - acquired_at).total_seconds() for _, _, acquired_at in parts)
            else:
                reasons.append('Tipo de operação não suportado na contabilidade observada.')

        open_quantity = sum((lot.quantity for lot in lots), ZERO)
        open_cost = sum((lot.cost for lot in lots), ZERO) if all(lot.cost is not None for lot in lots) else None
        if reasons:
            state = 'UNKNOWN'
            reason = ' | '.join(dict.fromkeys(reasons))
        elif open_quantity > ZERO:
            state = 'OPEN'
            reason = 'Fluxo observado ainda aberto; excluído dos resultados de tokens fechados.'
        elif buys and sells:
            state = 'CLOSED'
            reason = 'Fluxo observado fechado com custo histórico USD conhecido; resultado bruto antes de taxas.'
            closed.append(TokenResult(
                token=token, realized=gross, identified_realized=gross, costs=bought_cost,
                fees=sum((_decimal(event.fee_usd, 'Taxa USD') for event in token_events
                          if event.fee_usd is not None), ZERO),
                realized_cost=sold_cost, open_quantity=ZERO, open_cost=ZERO,
                unrealized=ZERO, weekly=dict(weekly), entries=entries,
                holding_seconds=holdings, reliable=True,
            ))
        else:
            state = 'UNKNOWN'
            reason = 'Sem um ciclo de aquisição e venda observado completo.'
        per_token.append({
            'token': token, 'state': state, 'reason': reason,
            'gross_result_before_fees': None if state == 'UNKNOWN' else gross,
            'realized_cost_usd': None if state == 'UNKNOWN' else sold_cost,
            'open_quantity': None if state == 'UNKNOWN' else open_quantity,
            'open_cost_usd': None if state == 'UNKNOWN' else open_cost,
            'weekly': {} if state == 'UNKNOWN' else dict(weekly),
            'holding_seconds': [] if state == 'UNKNOWN' else holdings,
            'entries': entries,
        })

    counts = Counter(row['state'] for row in per_token)
    observed = len(per_token)
    return {
        'token_results': closed,
        'observed_tokens_count': observed,
        'closed_tokens_count': counts['CLOSED'],
        'open_tokens_count': counts['OPEN'],
        'unknown_tokens_count': counts['UNKNOWN'],
        'closed_fraction': Decimal(counts['CLOSED']) / Decimal(observed) if observed else None,
        'excluded_tokens': [{'token': row['token'], 'state': row['state'], 'reason': row['reason']}
                            for row in per_token if row['state'] != 'CLOSED'],
        'per_token': per_token,
        'gross_result_before_fees': sum((result.realized for result in closed), ZERO) if closed else None,
        'known_fees_usd': known_fees,
        'fees_complete': fees_complete,
        'unclassified_transactions_count': len(unclassified),
        'result_definition': 'Resultado bruto FIFO apenas dos fluxos observados fechados; exclui taxas e tokens abertos/desconhecidos. Não é lucro líquido nem resultado de toda a carteira.',
        'fees_definition': 'Soma de taxas USD identificadas por evento normalizado e deduplicado; presença de todos os campos não prova a totalidade dos custos económicos.',
    }
