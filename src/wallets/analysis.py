"""Métricas por mint; posições e custos desconhecidos nunca são convertidos em zero.

Resultado agregado = realizado + não realizado das posições abertas, apenas se
ambos conhecidos. As marcas são indicativas, não uma garantia de liquidação.
Concentração usa a soma dos resultados POSITIVOS, nunca o resultado líquido.
"""
from __future__ import annotations
from collections import defaultdict
from decimal import Decimal
from statistics import median
from .models import TokenResult, SOL

SETTLEMENT_TOKENS = {SOL, 'native', 'SOL', '11111111111111111111111111111111',
    'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',
    'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB'}

ZERO = Decimal('0')


def _sum_known(values):
    values = list(values)
    return None if any(x is None for x in values) else sum(values, ZERO)


def _group(tokens):
    """Protege a unidade estatística mesmo se o chamador enviar resultados repetidos."""
    groups = defaultdict(list)
    for result in tokens:
        groups[result.token].append(result)
    grouped = []
    for address, results in sorted(groups.items()):
        weekly = defaultdict(lambda: ZERO)
        for result in results:
            for week, value in result.weekly.items():
                weekly[week] += value
        grouped.append(TokenResult(
            token=address, realized=_sum_known(r.realized for r in results),
            identified_realized=sum((r.identified_realized for r in results), ZERO),
            costs=sum((r.costs for r in results), ZERO),
            fees=sum((r.fees for r in results), ZERO),
            realized_cost=sum((r.realized_cost for r in results), ZERO),
            open_quantity=sum((r.open_quantity for r in results), ZERO),
            open_cost=_sum_known(r.open_cost if r.open_quantity else ZERO for r in results),
            unrealized=_sum_known(r.unrealized if r.open_quantity else ZERO for r in results),
            unknown_cost_quantity=sum((r.unknown_cost_quantity for r in results), ZERO),
            entries=[v for r in results for v in r.entries],
            holding_seconds=[v for r in results for v in r.holding_seconds],
            weekly=dict(weekly), reliable=all(r.reliable for r in results),
        ))
    return grouped


def metrics(tokens, discovery_tokens=()):
    all_grouped = _group(tokens)
    tokens = [r for r in all_grouped if r.token not in SETTLEMENT_TOKENS]
    settlement = [r for r in all_grouped if r.token in SETTLEMENT_TOKENS]
    discovery_tokens = set(discovery_tokens)
    aggregated = {
        r.token: None if r.realized is None or (r.open_quantity and r.unrealized is None)
        else r.realized + (r.unrealized if r.open_quantity else ZERO)
        for r in tokens
    }
    known = bool(tokens) and all(v is not None for v in aggregated.values())
    realized_known = bool(tokens) and all(r.realized is not None for r in tokens)
    weekly = defaultdict(lambda: ZERO)
    for result in tokens:
        for week, value in result.weekly.items():
            weekly[week] += value
    realized_values = [r.realized for r in tokens if r.realized is not None and r.reliable]
    wins = [v for v in realized_values if v > 0]
    losses = [v for v in realized_values if v < 0]
    returns = [r.realized / r.realized_cost for r in tokens
               if r.realized is not None and r.reliable and r.realized_cost > 0]
    entries = [entry for r in tokens for entry in r.entries]
    holds = [value for r in tokens for value in r.holding_seconds]
    ordered = sorted(aggregated, key=lambda token: (-(aggregated[token] or ZERO), token)) if known else []
    positive = sum((max(v, ZERO) for v in aggregated.values()), ZERO) if known else None
    positive_top1 = max(aggregated[ordered[0]], ZERO) if ordered else ZERO
    positive_top3 = sum((max(aggregated[token], ZERO) for token in ordered[:3]), ZERO) if known else None
    unknown_positions = sum(r.unknown_cost_quantity > 0 or r.realized is None or (r.open_quantity and r.open_cost is None)
                            for r in tokens)
    open_losses = _sum_known(min(r.unrealized, ZERO) if r.unrealized is not None else None
                             for r in tokens if r.open_quantity)
    return {
        'tokens_count': len(tokens),
        'settlement_tokens': [r.token for r in settlement],
        'settlement_result': _sum_known(r.realized for r in settlement),
        'analysis_universe': 'Tokens negociados exceto SOL/wSOL e USDC/USDT usados na liquidação; respetivos resultados são separados.',
        'reliable_tokens_count': sum(r.reliable and r.realized is not None for r in tokens),
        'realized_total': sum((r.realized for r in tokens), ZERO) if realized_known else None,
        'identified_realized_total': sum((r.identified_realized for r in tokens), ZERO),
        'identified_costs': sum((r.costs for r in tokens), ZERO),
        'identified_fees': sum((r.fees for r in tokens), ZERO),
        'unrealized_total': _sum_known(r.unrealized for r in tokens if r.open_quantity),
        'total_result': sum(aggregated.values(), ZERO) if known else None,
        'result_definition': 'Realizado + não realizado marcado; não é garantia de liquidação.',
        'per_token': aggregated,
        'best_token': ordered[0] if ordered else None,
        'top3_tokens': ordered[:3],
        'without_discovery': _sum_known(v for token, v in aggregated.items() if token not in discovery_tokens) if tokens else None,
        'without_best': sum((v for token, v in aggregated.items() if token not in ordered[:1]), ZERO) if known else None,
        'without_top3': sum((v for token, v in aggregated.items() if token not in ordered[:3]), ZERO) if known else None,
        'positive_results_sum': positive,
        'top1_share': positive_top1 / positive if positive else None,
        'top3_share': positive_top3 / positive if positive else None,
        'weekly': dict(sorted(weekly.items())),
        'weeks_count': len(weekly),
        'observed_outcome_weeks_count': len(weekly),
        'profitable_week_fraction': Decimal(sum(v > 0 for v in weekly.values())) / Decimal(len(weekly)) if weekly else None,
        'weekly_definition': 'Resultado realizado em semanas ISO UTC não sobrepostas; só semanas com fechos.',
        'average_gain': sum(wins, ZERO) / len(wins) if wins else None,
        'average_loss': sum(losses, ZERO) / len(losses) if losses else None,
        'median_return': median(returns) if returns else None,
        'return_definition': 'Resultado realizado / custo identificado consumido nas vendas, por token.',
        'win_rate': Decimal(len(wins)) / Decimal(len(realized_values)) if realized_values else None,
        'win_rate_definition': 'Tokens com resultado realizado > 0 / todos tokens com resultado realizado fiável (inclui zeros e posições abertas).',
        'profit_factor': sum(wins, ZERO) / abs(sum(losses, ZERO)) if losses else None,
        'no_realized_losses': bool(wins) and not losses,
        'median_entry_usd': median(entries) if entries else None,
        'median_holding_seconds': median(holds) if holds else None,
        'open_positions_count': sum(r.open_quantity != ZERO for r in tokens),
        'open_losses': open_losses,
        'unknown_cost_positions': unknown_positions,
        'unknown_cost_fraction': Decimal(unknown_positions) / Decimal(len(tokens)) if tokens else None,
        'unknown_cost_definition': 'Fração do número de posições/tokens; não soma quantidades incompatíveis.',
        'drawdown': None,
        'drawdown_reason': 'Sem série completa de valor da carteira e fluxos externos não se calcula drawdown.',
    }
