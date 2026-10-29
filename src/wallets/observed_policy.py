"""Seleção limitada a ciclos observados, sem promover a aprovação estrita.

As verificações usam resultados brutos de tokens com fluxo observado fechado.
Inventário global, custos totais, liquidez e coordenação continuam explicitamente
por verificar. A falta de um histórico observado paginado ou de dados necessários
às métricas continua a bloquear esta seleção.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal

from .analysis import SETTLEMENT_TOKENS, metrics
from .models import (
    Check, EVALUATION_SOURCES, OBSERVED_REQUIRED_CHECKS, OBSERVED_UNVERIFIED_NAMES, Status, UTC,
    allowed_event_sources, parse_time,
)
from .observed_accounting import summarize_observed

SCOPE = (
    'Seleção limitada aos ciclos fechados observados com valores históricos USD '
    'conhecidos. Resultado BRUTO antes de taxas; não é lucro líquido nem resultado '
    'global da carteira. Tokens abertos e indeterminados são contabilizados nas '
    'exclusões e na fração de cobertura da amostra.'
)
ZERO = Decimal('0')


def _number(config, name):
    value = Decimal(str(config[name]))
    if not value.is_finite():
        raise ValueError('Critério numérico não finito')
    return value


def _window_covers(scope, start, end):
    """Validar o âmbito quando o coletor disponibiliza a sua janela explícita."""
    if scope is None:
        return True
    if not isinstance(scope, dict):
        return False
    try:
        return parse_time(scope['start']) <= start < end <= parse_time(scope['end'])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def _sources_complete(provider, start, end):
    pages = provider.get('pages_complete')
    birdeye = (isinstance(pages, dict) and pages.get('trades') is True
               and pages.get('transfers') is True
               and _window_covers(provider.get('history_scope'), start, end))
    helius = provider.get('helius')
    if not isinstance(helius, dict):
        helius = {}
    failures = helius.get('failures')
    helius_complete = (helius.get('pages_complete') is True
                       and helius.get('truncated') is False
                       and isinstance(failures, list) and not failures
                       and _window_covers(helius.get('history_scope'), start, end))
    reconciliation = helius.get('reconciliation', {})
    conflict = (not isinstance(reconciliation, dict)
                or bool(reconciliation.get('conflicting_transactions')))
    return bool(birdeye), bool(helius_complete), conflict


def _empty_summary():
    return {
        'token_results': [], 'observed_tokens_count': 0, 'closed_tokens_count': 0,
        'open_tokens_count': 0, 'unknown_tokens_count': 0, 'closed_fraction': None,
        'excluded_tokens': [], 'per_token': [], 'gross_result_before_fees': None,
        'known_fees_usd': ZERO, 'fees_complete': False,
        'unclassified_transactions_count': 0,
    }


def _unverified(summary):
    # Mesmo campos presentes num evento não certificam custos/posições globais.
    # Estes oito avisos são parte obrigatória desta política limitada.
    reasons = {
        'opening_inventory': 'O inventário inicial de toda a carteira não é validado por esta seleção.',
        'fees_usd': 'Resultados BRUTOS: taxas e custos totais em USD não são deduzidos nem certificados como completos.',
        'global_coverage': 'A paginação do histórico observado não certifica todos os tokens, protocolos ou operações da carteira.',
        'open_positions': (
            f"Excluídos da amostra fechada: {summary['open_tokens_count']} tokens abertos e "
            f"{summary['unknown_tokens_count']} indeterminados; perdas abertas globais por apurar."
        ),
        'liquidity': 'Não foi validada a capacidade de liquidar posições completas ao preço indicado.',
        'execution': 'A duração observada não valida latência, slippage ou preços de entrada reproduzíveis.',
        'coordination': 'A ausência de coordenação, arbitragem ou MEV não foi demonstrada.',
        'labels': 'Identidade humana e completude das etiquetas não foram demonstradas.',
    }
    return [{'name': name, 'reason': reasons[name]} for name in OBSERVED_UNVERIFIED_NAMES]


def evaluate_observed(wallet, events, coverage, provider, discovery_tokens, config, now):
    """Avaliar oito critérios observáveis, sem converter desconhecidos em provas.

    O retorno é persistível em Evaluation.metrics['selection']. Não altera
    Evaluation.checks, Evaluation.tokens nem as flags de cobertura estritas.
    """
    from .checks import automation_signals  # evita ciclo com o motor estrito

    events = list(events)
    provider = provider if isinstance(provider, dict) else {}
    now = parse_time(now)
    coverage_info = asdict(coverage)
    checks = []

    def add(name, status, reason, evidence):
        checks.append(asdict(Check(
            name, status, reason, evidence, coverage.start, coverage.end,
            coverage_info, now, config['criteria_version'],
        )))

    time_valid = True
    bad_event = False
    try:
        start, end, fetched = (parse_time(coverage.start), parse_time(coverage.end),
                               parse_time(coverage.fetched_at))
        hours = (now - fetched).total_seconds() / 3600
        end_hours = (now - end).total_seconds() / 3600
        time_valid = (start < end <= fetched <= now
                      and 0 <= hours <= config['max_data_age_hours']
                      and 0 <= end_hours <= config['max_data_age_hours'])
    except (TypeError, ValueError, OverflowError):
        start, end = coverage.start, coverage.end
        hours = end_hours = None
        time_valid = False
    try:
        allowed_sources = allowed_event_sources(coverage.source)
        identities = [(event.wallet, event.id) for event in events]
        bad_event = (len(set(identities)) != len(identities)
                     or any(event.wallet != wallet or event.source not in allowed_sources
                            or not isinstance(event.id, str) or not event.id
                            or not isinstance(event.token, str) or not event.token
                            or not start <= parse_time(event.timestamp) <= end
                            or parse_time(event.timestamp) > now for event in events))
    except (TypeError, ValueError, OverflowError, AttributeError):
        bad_event = True

    try:
        birdeye_complete, helius_complete, conflicts = _sources_complete(provider, start, end)
    except (TypeError, ValueError, OverflowError):
        birdeye_complete = helius_complete = False
        conflicts = True
    accounting_error = False
    try:
        summary = summarize_observed(events, start, end)
    except (ValueError, TypeError, ArithmeticError, AttributeError):
        summary = _empty_summary()
        accounting_error = True
    live = coverage.source in EVALUATION_SOURCES
    data_ok = (live and time_valid and not bad_event and not conflicts
               and not accounting_error and (birdeye_complete or helius_complete))
    integrity_error = bad_event or conflicts or accounting_error
    data_status = Status.PASS if data_ok else (Status.FAIL if integrity_error else Status.UNKNOWN)
    add('observed_data', data_status,
        ('Histórico observado paginado e atual, sem conflitos de integridade.' if data_ok else
         'Histórico, atualidade ou interpretação insuficientes; esta seleção permanece bloqueada.'),
        {'birdeye_history_pages_complete': birdeye_complete,
         'helius_history_pages_complete': helius_complete,
         'fetched_age_hours': hours, 'period_end_age_hours': end_hours,
         'invalid_or_duplicate_or_future_event': bad_event,
         'conflicting_transactions': conflicts, 'accounting_input_error': accounting_error,
         'live_source': live, 'global_coverage_proven': False})

    token_results = summary['token_results']
    measured = metrics(token_results, discovery_tokens)
    measured.update({key: value for key, value in summary.items()
                     if key not in ('token_results', 'per_token')})
    measured['observed_per_token'] = summary['per_token']
    measured['scope'] = SCOPE
    measured['gross_result_before_fees'] = summary['gross_result_before_fees']
    measured['result_definition'] = SCOPE
    measured['cost_treatment'] = 'FIFO de ciclos observados fechados, BRUTO antes de taxas; custos de aquisição desconhecidos excluem o token.'
    measured['open_positions_count'] = summary['open_tokens_count']
    measured['open_losses'] = None
    measured['unrealized_total'] = None
    measured['global_wallet_performance_verified'] = False
    included = {result.token for result in token_results}
    included_events = [event for event in events if getattr(event, 'token', None) in included
                       and getattr(event, 'kind', None) in ('buy', 'sell')]
    measured['activity_days'] = len({parse_time(event.timestamp).astimezone(UTC).date()
                                    for event in included_events})
    if time_valid:
        week = (start - timedelta(days=start.weekday())).date()
        last_week = (end - timedelta(days=end.weekday())).date()
        weekly = dict(measured['weekly'])
        while week <= last_week:
            weekly.setdefault(week.isoformat(), ZERO)
            week += timedelta(days=7)
        measured['weekly'] = dict(sorted(weekly.items()))
        measured['weeks_count'] = len(weekly)
        measured['profitable_week_fraction'] = (Decimal(sum(value > 0 for value in weekly.values()))
                                                / Decimal(len(weekly))) if weekly else None
    else:
        measured['profitable_week_fraction'] = None
    measured['weekly_definition'] = 'Resultado bruto de ciclos fechados em semanas ISO UTC não sobrepostas, incluindo semanas inativas da janela.'
    measured['win_rate_definition'] = 'Tokens fechados observados com resultado bruto positivo / todos os tokens fechados incluídos, incluindo resultados zero.'
    sample_ok = (measured['tokens_count'] >= config['min_tokens']
                 and measured['activity_days'] >= config['min_activity_days']
                 and measured['observed_outcome_weeks_count'] >= config['min_weeks'])
    add('observed_sample', Status.PASS if data_ok and sample_ok else
        (Status.FAIL if data_ok else Status.UNKNOWN),
        'Amostra mínima de tokens fechados, dias e semanas cumprida.' if sample_ok else
        'Amostra insuficiente de tokens fechados, dias ou semanas independentes.',
        {key: measured[key] for key in ('tokens_count', 'activity_days', 'observed_outcome_weeks_count', 'weeks_count')})
    accounting_ok = bool(token_results) and all(result.reliable and result.realized is not None
                                                for result in token_results)
    add('observed_accounting', Status.PASS if data_ok and accounting_ok else Status.UNKNOWN,
        ('Aquisições e vendas dos ciclos incluídos reconciliadas em USD histórico; resultado BRUTO.'
         if accounting_ok else 'Sem ciclos fechados com custos históricos suficientes para calcular o resultado bruto.'),
        {'closed_tokens_count': summary['closed_tokens_count'], 'unknown_tokens_count': summary['unknown_tokens_count'],
         'known_fees_usd': summary['known_fees_usd'], 'fees_deducted': False,
         'global_opening_inventory_verified': False, 'net_profit_verified': False})

    known = data_ok and accounting_ok and measured['total_result'] is not None
    pf = measured['profit_factor']
    pf_ok = ((pf is not None and pf >= _number(config, 'min_profit_factor'))
             or measured['no_realized_losses'])
    consistent = (sample_ok and known and measured['total_result'] > ZERO
                  and measured['win_rate'] is not None and measured['win_rate'] >= _number(config, 'min_win_rate')
                  and pf_ok and measured['median_return'] is not None
                  and measured['median_return'] >= _number(config, 'min_median_return')
                  and measured['profitable_week_fraction'] is not None
                  and measured['profitable_week_fraction'] >= _number(config, 'min_profitable_week_fraction'))
    add('observed_consistency', Status.PASS if consistent else (Status.FAIL if known else Status.UNKNOWN),
        'Consistência bruta mínima entre tokens fechados e semanas cumprida.' if consistent else
        'Consistência bruta ou amostra abaixo dos limites, ou métricas indisponíveis.',
        {key: measured[key] for key in ('win_rate', 'profit_factor', 'no_realized_losses', 'median_return', 'weekly', 'profitable_week_fraction')})
    concentration_known = known and all(measured[key] is not None for key in
        ('without_discovery', 'without_best', 'without_top3', 'top1_share', 'top3_share'))
    concentrated_ok = (concentration_known and sample_ok and measured['without_discovery'] > ZERO
                       and measured['without_best'] > ZERO and measured['without_top3'] > ZERO
                       and measured['top1_share'] <= _number(config, 'max_top1_share')
                       and measured['top3_share'] <= _number(config, 'max_top3_share'))
    add('observed_concentration', Status.PASS if concentrated_ok else
        (Status.FAIL if concentration_known else Status.UNKNOWN),
        'Resultados brutos mantêm-se sem origens, melhor token e três melhores.' if concentrated_ok else
        'Concentração, resultado residual ou informação insuficientes para seleção.',
        {key: measured[key] for key in ('without_discovery', 'without_best', 'without_top3', 'top1_share', 'top3_share', 'positive_results_sum')})
    fraction = summary['closed_fraction']
    minimum = Decimal(str(config.get('selection', {}).get('min_closed_token_fraction', '0.8')))
    if not minimum.is_finite() or not ZERO < minimum <= 1:
        raise ValueError('Fração mínima de ciclos fechados inválida')
    scope_known = data_ok and fraction is not None
    scope_ok = scope_known and fraction >= minimum
    add('observed_scope', Status.PASS if scope_ok else (Status.FAIL if scope_known else Status.UNKNOWN),
        'Fração mínima do universo observado incluída, com exclusões explícitas.' if scope_ok else
        'Demasiados tokens abertos/indeterminados ou universo observado insuficiente.',
        {'observed_tokens_count': summary['observed_tokens_count'], 'closed_tokens_count': summary['closed_tokens_count'],
         'open_tokens_count': summary['open_tokens_count'], 'unknown_tokens_count': summary['unknown_tokens_count'],
         'closed_fraction': fraction, 'minimum_closed_fraction': minimum,
         'excluded_tokens': summary['excluded_tokens'], 'global_wallet_universe_verified': False})

    safe_events = [] if bad_event or accounting_error else events
    labels = provider.get('discovery_labels', [])
    if not isinstance(labels, list):
        labels = []
    signals = automation_signals(safe_events, token_results, config, labels)
    measured['automation'] = signals
    strong_signals = len(signals['signals']) >= max(2, int(config['min_bot_signals']))
    automation_status = (Status.FAIL if data_ok and strong_signals else
                         Status.PASS if data_ok and sample_ok else Status.UNKNOWN)
    add('observed_automation', automation_status,
        ('Vários sinais observados de estratégia automatizada inadequada.' if automation_status == Status.FAIL else
         'Risco dentro das heurísticas na atividade observada; identidade humana e coordenação não verificadas.'
         if automation_status == Status.PASS else 'Amostra ou histórico insuficiente para avaliar os sinais observáveis.'),
        dict(signals, assessment_scope='observed_activity_only', human_confirmed=False,
             global_bot_absence_verified=False))
    hold = measured['median_holding_seconds']
    follow_known = data_ok and sample_ok and hold is not None
    follow_ok = (follow_known and automation_status == Status.PASS
                 and hold >= config['min_followable_hold_seconds']
                 and signals['rapid_close_fraction'] <= _number(config, 'max_rapid_close_fraction'))
    add('observed_followability', Status.PASS if follow_ok else
        (Status.FAIL if follow_known else Status.UNKNOWN),
        'Duração e rapidez observadas dentro dos limites; liquidez e execução real permanecem por verificar.' if follow_ok else
        'Duração/rapidez inadequadas ou dados insuficientes para os critérios observáveis.',
        {'median_holding_seconds': hold, 'rapid_close_fraction': signals['rapid_close_fraction'],
         'liquidity_verified': False, 'execution_price_verified': False, 'simulation': False})
    assert tuple(check['name'] for check in checks) == OBSERVED_REQUIRED_CHECKS
    return {'policy': 'observed-v1', 'checks': checks, 'metrics': measured,
            'unverified': _unverified(summary), 'scope': SCOPE}
