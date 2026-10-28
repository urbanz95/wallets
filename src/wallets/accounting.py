"""Contabilidade FIFO conservadora, por endereço do token.

Event.quantity usa unidades humanas (o coletor aplica os decimais uma única vez).
Event.usd_value é o valor TOTAL histórico da operação, nunca uma cotação atual.
`identified_realized` deduz apenas custos/frações e taxas identificados; não é
lucro líquido quando `reliable` é falso. `realized` só é preenchido quando toda a
contabilidade e a cobertura do token são suficientemente conhecidas.

marks = {mint: {'price_usd': Decimal(...), 'timestamp': datetime UTC}}. São
cotações indicativas no instante de avaliação, não preços executáveis para vender
uma posição inteira. Não se usam marks para recalcular operações passadas.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Iterable, Mapping

from .models import Coverage, Event, TokenResult, UTC, parse_time

ZERO = Decimal('0')


@dataclass
class _Lot:
    quantity: Decimal
    basis: Decimal | None
    acquired_at: datetime
    complete: bool = True


def _amount(value, label):
    if isinstance(value, float):
        raise ValueError(f'{label}: use Decimal, não float')
    result = value if isinstance(value, Decimal) else Decimal(value)
    if not result.is_finite() or result < ZERO:
        raise ValueError(f'{label}: valor negativo ou não finito')
    return result


def _week(timestamp):
    day = timestamp.astimezone(UTC).date()
    return (day - timedelta(days=day.weekday())).isoformat()


def _consume(lots, quantity):
    """Retira FIFO sem inventar inventário anterior; devolve fatias consumidas."""
    remaining = quantity
    consumed = []
    while remaining > ZERO and lots:
        lot = lots[0]
        take = min(remaining, lot.quantity)
        basis = None if lot.basis is None else (lot.basis if take == lot.quantity else lot.basis * take / lot.quantity)
        consumed.append(_Lot(take, basis, lot.acquired_at, lot.complete))
        lot.quantity -= take
        if lot.basis is not None:
            lot.basis -= basis
        remaining -= take
        if lot.quantity == ZERO:
            lots.popleft()
    return consumed, remaining


def account(events: Iterable[Event], coverage: Coverage, marks: Mapping | None = None) -> list[TokenResult]:
    """Calcula FIFO sem informação futura e sem confundir transferências com trades.

    A entrada descreve UMA carteira. Duplicados idênticos de (wallet,id) são
    removidos. Um identificador repetido com conteúdo diferente é um erro, pois
    escolher uma versão arbitrária poderia alterar o resultado. Eventos fora da
    janela provocam erro em vez de entrarem numa avaliação retroativa.
    """
    start, end = parse_time(coverage.start), parse_time(coverage.end)
    if start > end:
        raise ValueError('Período de cobertura invertido')
    unique = {}
    wallets = set()
    for event in events:
        stamp = parse_time(event.timestamp)
        if stamp < start or stamp > end:
            raise ValueError('Evento fora do período; informação futura não permitida')
        if not event.id or not event.wallet or not event.token:
            raise ValueError('Evento sem identidade, carteira ou token')
        identity = (event.wallet, event.id)
        if identity in unique and unique[identity] != event:
            raise ValueError('Duplicado contraditório')
        unique[identity] = event
        wallets.add(event.wallet)
    if len(wallets) > 1:
        raise ValueError('A contabilidade recebe uma carteira de cada vez')

    grouped = defaultdict(list)
    for event in sorted(unique.values(), key=lambda item: parse_time(item.timestamp)):
        grouped[event.token].append(event)

    results = []
    for token, token_events in sorted(grouped.items()):
        result = TokenResult(token=token)
        lots = deque()
        reliable = (coverage.complete and not coverage.truncated
                    and coverage.all_tokens and coverage.opening_inventory_known
                    and coverage.fees_complete and not coverage.failures)
        unresolved = False
        economic_events = False
        timestamps = defaultdict(int)
        for event in token_events:
            quantity = _amount(event.quantity, 'Quantidade')
            value = None if event.usd_value is None else _amount(event.usd_value, 'Valor USD')
            fee = None if event.fee_usd is None else _amount(event.fee_usd, 'Taxa USD')
            stamp = parse_time(event.timestamp)
            if fee is not None:
                result.fees += fee
            # A mesma identidade económica com várias pernas só pode ser agregada
            # a montante quando todos os fluxos e custos forem conhecidos.
            if event.multi_step or event.kind in ('unknown', 'liquidity'):
                unresolved = True
                reliable = False
                continue
            if event.kind == 'wrap':
                # SOL e wrapped SOL são a mesma exposição; conversão não gera lucro.
                # A taxa conhecida é despesa, se existir.
                if fee is None:
                    reliable = False
                else:
                    result.identified_realized -= fee
                    result.weekly[_week(stamp)] = result.weekly.get(_week(stamp), ZERO) - fee
                continue
            if event.kind == 'failed':
                # Uma transação falhada não cria nem vende tokens; pode pagar taxas.
                if fee is None:
                    reliable = False
                else:
                    result.identified_realized -= fee
                    result.weekly[_week(stamp)] = result.weekly.get(_week(stamp), ZERO) - fee
                continue
            economic_events = True
            if event.kind in ('buy', 'sell'):
                timestamps[stamp] += 1
            if fee is None:
                reliable = False
            if event.kind == 'buy':
                if quantity == ZERO:
                    reliable = False
                    unresolved = True
                    continue
                if value is not None:
                    result.costs += value
                    result.entries.append(value)
                else:
                    reliable = False
                basis = None if value is None else value + (fee or ZERO)
                lots.append(_Lot(quantity, basis, stamp, value is not None and fee is not None))
            elif event.kind in ('transfer_in', 'airdrop'):
                # O preço de mercado de uma distribuição não revela custo de aquisição.
                if quantity > ZERO:
                    lots.append(_Lot(quantity, None, stamp, False))
                reliable = False
                if fee is not None:
                    result.identified_realized -= fee
                    result.weekly[_week(stamp)] = result.weekly.get(_week(stamp), ZERO) - fee
            elif event.kind in ('sell', 'transfer_out'):
                slices, missing = _consume(lots, quantity)
                if missing > ZERO:
                    reliable = False
                if event.kind == 'transfer_out':
                    reliable = False
                    if fee is not None:
                        result.identified_realized -= fee
                        result.weekly[_week(stamp)] = result.weekly.get(_week(stamp), ZERO) - fee
                    continue
                known_quantity = sum((part.quantity for part in slices if part.basis is not None), ZERO)
                known_basis = sum((part.basis for part in slices if part.basis is not None), ZERO)
                if quantity == ZERO or value is None or known_quantity != quantity or any(not part.complete for part in slices):
                    reliable = False
                result.realized_cost += known_basis
                if value is not None and quantity > ZERO and known_quantity > ZERO:
                    identified = (value - (fee or ZERO)) * known_quantity / quantity - known_basis
                    result.identified_realized += identified
                    result.weekly[_week(stamp)] = result.weekly.get(_week(stamp), ZERO) + identified
                for part in slices:
                    result.holding_seconds.append((stamp - part.acquired_at).total_seconds())
            else:
                reliable = False
                unresolved = True

        # Sem ordenação intrassegundo, aquisições simultâneas com custos
        # diferentes também tornam ambíguo o FIFO de vendas parciais futuras.
        # Os sinais de rapidez continuam disponíveis.
        if any(count > 1 for count in timestamps.values()):
            reliable = False
        result.open_quantity = sum((lot.quantity for lot in lots), ZERO)
        result.unknown_cost_quantity = sum((lot.quantity for lot in lots if lot.basis is None or not lot.complete), ZERO)
        complete_open_basis = not unresolved and all(lot.basis is not None and lot.complete for lot in lots)
        result.open_cost = sum((lot.basis for lot in lots), ZERO) if complete_open_basis else None
        if result.open_quantity and not coverage.opening_inventory_known:
            result.open_cost = None
        mark = (marks or {}).get(token)
        if mark is not None:
            mark_time = parse_time(mark['timestamp'])
            if mark_time > end:
                raise ValueError('Cotação futura não permitida')
            price = _amount(mark['price_usd'], 'Cotação USD')
            if result.open_cost is not None:
                result.unrealized = result.open_quantity * price - result.open_cost
        elif result.open_quantity == ZERO and not unresolved:
            result.unrealized = ZERO
        result.reliable = reliable and not unresolved and economic_events
        if result.reliable:
            result.realized = result.identified_realized
        results.append(result)
    return results
