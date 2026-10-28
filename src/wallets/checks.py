"""Oito verificações obrigatórias; ausência de prova nunca aprova."""
from __future__ import annotations
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
from statistics import median
from .analysis import metrics, SETTLEMENT_TOKENS
from .models import Check, Evaluation, REQUIRED_CHECKS, Status, UTC, dumps, iso, utcnow, allowed_event_sources


def _decimal(config, key):
    return Decimal(str(config[key]))


def evaluate(wallet, events, coverage, token_results, discovery_tokens, config, now=None, provider_metrics=None):
    now = now or utcnow()
    iso(now)  # rejeita relógios sem fuso
    events = list(events)
    token_results = list(token_results)
    provider_metrics = provider_metrics or {}
    version = config['criteria_version']
    measured = metrics(token_results, discovery_tokens)
    measured['_events_hash'] = sha256(dumps([asdict(e) for e in sorted(events,key=lambda e:(e.timestamp,e.id))]).encode()).hexdigest()
    measured['_criteria_config'] = deepcopy(config)
    measured['_criteria_hash'] = sha256(dumps(config).encode()).hexdigest()
    # Inclui semanas inativas do período; não seleciona apenas semanas lucrativas.
    week = (coverage.start - timedelta(days=coverage.start.weekday())).date()
    last_week = (coverage.end - timedelta(days=coverage.end.weekday())).date()
    weekly = dict(measured['weekly'])
    while week <= last_week:
        weekly.setdefault(week.isoformat(), Decimal('0'))
        week += timedelta(days=7)
    measured['weekly'] = dict(sorted(weekly.items()))
    measured['weeks_count'] = len(weekly)
    measured['profitable_week_fraction'] = Decimal(sum(v > 0 for v in weekly.values())) / Decimal(len(weekly)) if weekly else None
    measured['weekly_definition'] = 'Resultado realizado em semanas ISO UTC não sobrepostas, incluindo semanas inativas e fronteiras parciais.'
    trades = [event for event in events if event.kind in ('buy', 'sell') and event.token not in SETTLEMENT_TOKENS]
    measured['activity_days'] = len({event.timestamp.astimezone(UTC).date() for event in trades})
    measured['provider_metrics'] = provider_metrics
    measured['cost_treatment'] = ('FIFO recalculado, custos identificados separados; não chamar lucro líquido enquanto houver custos por apurar.')
    checks = []
    coverage_info = asdict(coverage)

    def add(name, status, reason, evidence):
        checks.append(Check(name, status, reason, evidence, coverage.start, coverage.end,
                            coverage_info, now, version))

    age = (now - coverage.fetched_at).total_seconds() / 3600
    end_age = (now - coverage.end).total_seconds() / 3600
    future = any(event.timestamp > coverage.end or event.timestamp > now for event in events)
    before = any(event.timestamp < coverage.start for event in events)
    mixed_wallets = any(event.wallet != wallet for event in events)
    duplicate_ids = len({event.id for event in events}) != len(events)
    mixed_sources = any(event.source not in allowed_event_sources(coverage.source) for event in events)
    data_ok = (coverage.complete and not coverage.truncated and coverage.all_tokens
               and not coverage.failures and 0 <= age <= config['max_data_age_hours']
               and 0 <= end_age <= config['max_data_age_hours']
               and coverage.start < coverage.end <= coverage.fetched_at <= now
               and not future and not before and not mixed_wallets and not duplicate_ids and not mixed_sources)
    add('data_quality', Status.PASS if data_ok else Status.UNKNOWN,
        'Cobertura completa, sem falhas e atual.' if data_ok else 'Dados incompletos, desatualizados, incoerentes ou fora do período; não permitem aprovar.',
        {'fetched_age_hours': age, 'period_end_age_hours': end_age, 'future_information': future,
         'before_period': before, 'mixed_wallets': mixed_wallets, 'duplicate_ids': duplicate_ids, 'mixed_sources': mixed_sources})

    sample_ok = (measured['tokens_count'] >= config['min_tokens']
                 and measured['activity_days'] >= config['min_activity_days']
                 and measured['observed_outcome_weeks_count'] >= config['min_weeks'])
    add('sample', Status.PASS if sample_ok and data_ok else (Status.FAIL if data_ok else Status.UNKNOWN),
        'Amostra mínima cumprida.' if sample_ok else 'Amostra insuficiente de tokens, dias ou semanas independentes.',
        {key: measured[key] for key in ('tokens_count', 'activity_days', 'weeks_count', 'observed_outcome_weeks_count')})

    accounting_ok = (data_ok and coverage.opening_inventory_known and coverage.fees_complete
                     and bool(token_results) and all(r.reliable and r.realized is not None for r in token_results)
                     and measured['unknown_cost_fraction'] is not None
                     and measured['unknown_cost_fraction'] <= _decimal(config, 'max_unknown_cost_fraction')
                     and not any(event.kind in ('unknown', 'liquidity') or event.multi_step for event in events))
    add('accounting', Status.PASS if accounting_ok else Status.UNKNOWN,
        'Inventário inicial, preços históricos e custos suficientemente conhecidos.' if accounting_ok else 'Inventário, interpretação, preço histórico, custos ou taxas por apurar.',
        {'unknown_cost_fraction': measured['unknown_cost_fraction'], 'method': 'FIFO',
         'provider_pnl_is_recalculated': False, 'fees_complete': coverage.fees_complete})

    pf = measured['profit_factor']
    pf_ok = (pf is not None and pf >= _decimal(config, 'min_profit_factor')) or measured['no_realized_losses']
    consistency_known = accounting_ok and measured['total_result'] is not None
    consistency_ok = (consistency_known and sample_ok and measured['total_result'] > 0
                      and measured['win_rate'] is not None and measured['win_rate'] >= _decimal(config, 'min_win_rate')
                      and pf_ok and measured['median_return'] is not None
                      and measured['median_return'] >= _decimal(config, 'min_median_return')
                      and measured['profitable_week_fraction'] is not None
                      and measured['profitable_week_fraction'] >= _decimal(config, 'min_profitable_week_fraction'))
    add('consistency', Status.PASS if consistency_ok else (Status.FAIL if consistency_known and data_ok else Status.UNKNOWN),
        'Consistência mínima entre tokens e semanas cumprida.' if consistency_ok else 'Consistência insuficiente ou dados insuficientes para a medir.',
        {key: measured[key] for key in ('win_rate', 'profit_factor', 'no_realized_losses', 'median_return', 'weekly', 'total_result')})

    concentration_known = accounting_ok and all(measured[key] is not None for key in
                        ('without_discovery', 'without_best', 'without_top3', 'top1_share', 'top3_share'))
    concentration_ok = (concentration_known and sample_ok
                        and measured['without_discovery'] > 0 and measured['without_best'] > 0
                        and measured['without_top3'] > 0
                        and measured['top1_share'] <= _decimal(config, 'max_top1_share')
                        and measured['top3_share'] <= _decimal(config, 'max_top3_share'))
    add('concentration', Status.PASS if concentration_ok else (Status.FAIL if concentration_known and data_ok else Status.UNKNOWN),
        'Resultados mantêm-se sem origens, melhor token e três melhores.' if concentration_ok else 'Dependência de vencedores acima da heurística, resultado residual insuficiente ou informação incompleta.',
        {key: measured[key] for key in ('without_discovery', 'without_best', 'without_top3', 'top1_share', 'top3_share', 'positive_results_sum')})

    has_open = measured['open_positions_count'] > 0
    risk_known = (accounting_ok and coverage.all_tokens and measured['open_losses'] is not None
                  and (not has_open or (coverage.marks_complete and coverage.liquidity_complete)))
    loss_fraction = (abs(measured['open_losses']) / measured['identified_costs']
                     if risk_known and measured['identified_costs'] > 0 else None)
    risk_ok = risk_known and (not has_open or (loss_fraction is not None and loss_fraction <= _decimal(config, 'max_open_loss_fraction')))
    measured['open_loss_fraction_of_identified_costs'] = loss_fraction
    add('open_risk', Status.PASS if risk_ok else (Status.FAIL if risk_known and data_ok else Status.UNKNOWN),
        'Perdas abertas dentro da heurística; marcas não garantem liquidação.' if risk_ok else 'Perdas abertas excessivas ou cobertura de posições/preços/liquidez insuficiente.',
        {'open_positions': measured['open_positions_count'], 'open_losses': measured['open_losses'],
         'loss_fraction_of_identified_costs': loss_fraction, 'liquidation_guaranteed': False})

    signals = automation_signals(events, token_results, config, provider_metrics.get('discovery_labels',[]))
    measured['automation'] = signals
    bot_coverage = (coverage.complete and coverage.execution_complete and coverage.coordination_complete and coverage.labels_complete)
    # A ausência de rótulos ou indícios não equivale a dados suficientes.
    bot_risk = len(signals['signals']) >= max(2, int(config['min_bot_signals']))
    automation_status = (Status.UNKNOWN if not (bot_coverage and data_ok) else
                         Status.FAIL if bot_risk else Status.PASS)
    add('automation', automation_status,
        ('Vários sinais independentes de execução inadequada.' if automation_status == Status.FAIL else
         'Cobertura suficiente e risco dentro das heurísticas; identidade humana não confirmada.' if automation_status == Status.PASS else
         'Sem cobertura suficiente de execução, etiquetas e coordenação; ausência de indícios não prova ausência de automatização.'),
        signals | {'coverage_sufficient': bot_coverage, 'human_confirmed': False})

    holdings = measured['median_holding_seconds']
    follow_known = (coverage.execution_complete and coverage.liquidity_complete and coverage.complete
                    and holdings is not None and bot_coverage)
    follow_ok = (follow_known and data_ok and automation_status == Status.PASS
                 and holdings >= config['min_followable_hold_seconds']
                 and signals['rapid_close_fraction'] <= _decimal(config, 'max_rapid_close_fraction'))
    add('followability', Status.PASS if follow_ok else (Status.FAIL if follow_known and data_ok else Status.UNKNOWN),
        'Horizonte e cobertura de execução compatíveis com os critérios de acompanhamento.' if follow_ok else 'Dependência de velocidade ou dados insuficientes para avaliar acompanhamento.',
        {'median_holding_seconds': holdings, 'rapid_close_fraction': signals['rapid_close_fraction'],
         'liquidity_complete': coverage.liquidity_complete, 'simulation': False})
    assert tuple(check.name for check in checks) == REQUIRED_CHECKS
    result = Evaluation(wallet, now, version, coverage, checks, measured, token_results,
                        sorted(set(discovery_tokens)), coverage.source)
    result.id = sha256(dumps(result.to_dict()).encode()).hexdigest()
    return result


def automation_signals(events, tokens, config, labels=()):
    trades = sorted((event for event in events if event.kind in ('buy', 'sell')),
                    key=lambda event: (event.timestamp, event.id))
    raw_trades = trades
    by_execution = {}
    for event in trades:
        by_execution.setdefault(event.tx_hash or event.id, event)
    trades = sorted(by_execution.values(), key=lambda event: (event.timestamp, event.id))
    holds = [seconds for token in tokens if token.token not in SETTLEMENT_TOKENS for seconds in token.holding_seconds]
    rapid = Decimal(sum(seconds <= config['rapid_seconds'] for seconds in holds)) / len(holds) if holds else Decimal('0')
    intervals = [(b.timestamp - a.timestamp).total_seconds() for a, b in zip(trades, trades[1:])]
    daily = Counter(event.timestamp.astimezone(UTC).date() for event in trades)
    by_transaction = defaultdict(set)
    by_block = defaultdict(set)
    for event in raw_trades:
        if event.tx_hash: by_transaction[(event.token, event.tx_hash)].add(event.kind)
        if event.block is not None: by_block[(event.token, event.block)].add(event.kind)
    same_transaction = sum(kinds == {'buy', 'sell'} for kinds in by_transaction.values())
    same_block = sum(kinds == {'buy', 'sell'} for kinds in by_block.values())
    # Correlated fast execution clues count as ONE family, not three independent votes.
    signals = []
    observed_tags=set()
    for observation in labels:
        if not isinstance(observation,dict):continue
        evidence=observation.get('evidence',{})
        tags=evidence.get('tags') if isinstance(evidence,dict) else None
        if isinstance(tags,list):observed_tags.update(tag for tag in tags if isinstance(tag,str))
    if observed_tags & {'sniper','bundler'}:
        signals.append('rotulos_contextuais_birdeye')
    if (holds and rapid > _decimal(config, 'max_rapid_close_fraction')) or same_transaction or same_block:
        signals.append('execucao_demasiado_rapida')
    if daily and max(daily.values()) > config['max_trades_per_day']:
        signals.append('intensidade_elevada')
    if len(intervals) >= 20:
        positive = [value for value in intervals if value > 0]
        if positive and len(positive) >= 20:
            typical = median(positive)
            close = sum(abs(value - typical) <= max(1, typical * .05) for value in positive) / len(positive)
            if close >= .9:
                signals.append('intervalos_repetitivos')
    active_hours = defaultdict(set)
    for event in trades:
        active_hours[event.timestamp.astimezone(UTC).date()].add(event.timestamp.astimezone(UTC).hour)
    if sum(len(hours) >= 20 for hours in active_hours.values()) >= 3:
        signals.append('atividade_quase_continua')
    return {'signals': signals, 'observed_birdeye_tags':sorted(observed_tags), 'rapid_close_fraction': rapid,
            'max_trades_per_day': max(daily.values(), default=0),
            'median_interval_seconds': median(intervals) if intervals else None,
            'same_transaction_round_trips': same_transaction, 'same_block_round_trips': same_block,
            'arbitrage_or_mev_proven': False,
            'interpretation': 'Heurísticas combinadas; não identifica pessoas nem prova MEV, sniping ou fraude.'}
