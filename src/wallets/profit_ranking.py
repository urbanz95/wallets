"""Ranking privado por evidência; níveis A/B/C não autorizam Telegram.

PnL Birdeye serve de pré-filtro. A decisão observável usa o cenário conservador
FIFO em SOL; métricas USD e SOL nunca são somadas. A pontuação ordena candidatos
mas nenhuma pontuação converte falhas ou falta de prova em nível A.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from decimal import Decimal

from .collection import valid_address
from .models import Status, parse_time

ZERO = Decimal('0')
ONE = Decimal('1')
UNVERIFIED = (
    ('global_inventory', 'Inventário inicial e resultado global da carteira NÃO VERIFICADOS.'),
    ('global_coverage', 'Cobertura global de todos os protocolos e tokens NÃO VERIFICADA.'),
    ('usd_conversion', 'Conversão histórica dos resultados SOL para USD NÃO VERIFICADA.'),
    ('unidentified_costs', 'Taxas, rendas e outros custos não identificados NÃO VERIFICADOS.'),
    ('executable_liquidity', 'Liquidez de saída integral, slippage e impacto no preço NÃO VERIFICADOS.'),
    ('copy_execution', 'Latência, preços e execução reproduzíveis por um seguidor NÃO VERIFICADOS.'),
    ('independent_persistence', 'Persistência em períodos independentes NÃO VERIFICADA; 7d e 30d sobrepõem-se.'),
    ('statistical_independence', 'Independência entre tokens e validade de inferência estatística NÃO VERIFICADAS.'),
    ('coordination_identity', 'Coordenação, MEV, ausência de fraude e identidade humana NÃO VERIFICADOS.'),
    ('labels', 'Completude e atualidade de todas as etiquetas NÃO VERIFICADAS.'),
)


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (ValueError, TypeError, ArithmeticError):
        return None
    return result if result.is_finite() else None


def _count(value):
    number = _number(value)
    return int(number) if number is not None and number >= ZERO and number == number.to_integral_value() else None


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _fraction(value):
    value = _number(value)
    return value if value is not None and ZERO <= value <= ONE else None


def _config(config):
    profile = _mapping(config.get('profit', config))
    defaults = {
        'min_tokens': 40, 'min_profit_factor': Decimal('1.3'), 'min_win_rate': Decimal('0.4'),
        'max_top1_share': Decimal('0.35'), 'max_top3_share': Decimal('0.65'),
        'max_unknown_cost_fraction': Decimal('0.2'), 'min_followable_hold_seconds': 300,
        'max_rapid_close_fraction': Decimal('0.2'), 'max_trades_per_day': 100,
        'max_data_age_hours': 24,
    }
    result = {key: _number(profile.get(key, default)) for key, default in defaults.items()}
    if any(value is None for value in result.values()):
        raise ValueError('Critério numérico de ranking inválido')
    if result['min_tokens'] <= ZERO or result['min_tokens'] != result['min_tokens'].to_integral_value():
        raise ValueError('Amostra mínima de ranking inválida')
    for key in ('min_profit_factor', 'min_followable_hold_seconds', 'max_trades_per_day', 'max_data_age_hours'):
        if result[key] <= ZERO:
            raise ValueError('Limite de ranking inválido')
    for key in ('min_win_rate', 'max_top1_share', 'max_top3_share', 'max_unknown_cost_fraction', 'max_rapid_close_fraction'):
        if not ZERO <= result[key] <= ONE:
            raise ValueError('Fração de ranking inválida')
    return result


def _origin_count(origins):
    if not isinstance(origins, (list, tuple, set)):
        return 0
    mints = set()
    for origin in origins:
        if isinstance(origin, str) and origin:
            mints.add(origin)
        elif isinstance(origin, dict):
            mint = origin.get('token', origin.get('mint', origin.get('address')))
            if isinstance(mint, str) and mint:
                mints.add(mint)
    return len(mints)


def _positive_prefilter(prefilter):
    prefilter = _mapping(prefilter)
    pnl = _number(prefilter.get('pnl_usd'))
    complete = prefilter.get('complete') is True
    trades = _count(prefilter.get('trade_count'))
    # O headline de summary pode cobrir o âmbito pedido mesmo quando faltam
    # páginas de tokens. A contabilidade SOL própria continua obrigatória para A.
    known = pnl is not None and (complete or trades is not None)
    active = trades > 0 if trades is not None else complete
    passed = prefilter.get('passed') is True or prefilter.get('provisional_passed') is True
    return pnl, known, bool(known and active and pnl > ZERO and passed)


def _temporal_evidence(deep, now, max_age):
    coverage = _mapping(deep.get('coverage'))
    try:
        start = parse_time(deep.get('period_start', coverage.get('start')))
        end = parse_time(deep.get('period_end', coverage.get('end')))
        fetched = parse_time(deep.get('fetched_at', coverage.get('fetched_at')))
        coherent = start < end <= fetched <= now and end-start == timedelta(days=7)
        fresh = coherent and now-fetched <= timedelta(hours=float(max_age)) and now-end <= timedelta(hours=float(max_age))
        return bool(fresh), {'period_start': start, 'period_end': end, 'fetched_at': fetched}
    except (ValueError, TypeError, OverflowError):
        return False, {'period_start': None, 'period_end': None, 'fetched_at': None}



def _provider_temporal_evidence(prefilter, now, max_age):
    raw = _mapping(prefilter.get('raw_metrics'))
    receipts = raw.get('responses')
    receipts = [raw]+receipts if isinstance(receipts, list) else [raw]
    evidence = []
    known = True
    valid = prefilter.get('_future_snapshot') is not True
    for receipt in receipts:
        row = _mapping(receipt)
        try:
            snapshot = parse_time(row.get('snapshot_at'))
            fetched = parse_time(row.get('fetched_at'))
        except (ValueError, TypeError, OverflowError, OSError):
            known = False
            valid = False
            evidence.append({'snapshot_at': None, 'fetched_at': None, 'current': False})
            continue
        coherent = snapshot <= fetched <= now
        fresh = coherent and now-snapshot <= timedelta(hours=float(max_age)) and now-fetched <= timedelta(hours=float(max_age))
        valid = valid and fresh
        evidence.append({'snapshot_at': snapshot, 'fetched_at': fetched, 'current': bool(fresh)})
    return known, bool(known and valid), evidence


def _score_component(components, name, maximum, fraction):
    fraction = ONE if fraction is True else ZERO if fraction is False else _number(fraction)
    fraction = max(ZERO, min(ONE, fraction)) if fraction is not None else ZERO
    value = (Decimal(str(maximum))*fraction).quantize(Decimal('0.01'))
    components.append({'name': name, 'points': float(value), 'maximum': maximum})


def _rank_one(candidate, criteria, now):
    result = deepcopy(candidate)
    pre7 = _mapping(candidate.get('prefilter7'))
    pre30 = _mapping(candidate.get('prefilter30'))
    deep = _mapping(candidate.get('deep'))
    metrics = _mapping(deep.get('metrics'))
    checks = []

    def add(name, known, passed, reason, evidence=None):
        state = Status.PASS if passed and known else Status.FAIL if known else Status.UNKNOWN
        checks.append({'name': name, 'status': state.value, 'reason': reason, 'evidence': evidence or {}})
        return state

    pnl7, known7, positive7 = _positive_prefilter(pre7)
    pnl30, known30, positive30 = _positive_prefilter(pre30)
    add('provider_7d', known7, positive7,
        'Pré-filtro Birdeye 7d: resultado positivo e critérios de triagem cumpridos.' if positive7 else
        'Pré-filtro Birdeye 7d indisponível, incompleto ou sem resultado/critério positivo.',
        {'pnl_usd': pnl7, 'complete': pre7.get('complete') is True, 'passed': pre7.get('passed') is True})
    add('provider_30d', known30, positive30,
        'Pré-filtro Birdeye 30d positivo; proxy sobreposto, não persistência independente.' if positive30 else
        'Pré-filtro Birdeye 30d indisponível, incompleto ou sem resultado/critério positivo.',
        {'pnl_usd': pnl30, 'complete': pre30.get('complete') is True, 'passed': pre30.get('passed') is True})


    dates7_known, dates7_ok, dates7 = _provider_temporal_evidence(pre7, now, criteria['max_data_age_hours'])
    dates30_known, dates30_ok, dates30 = _provider_temporal_evidence(pre30, now, criteria['max_data_age_hours'])
    provider_dates_ok = dates7_ok and dates30_ok
    add('provider_dates', dates7_known and dates30_known, provider_dates_ok,
        'Snapshots Birdeye 7d/30d anteriores à receção, atuais e disponíveis na avaliação.' if provider_dates_ok else
        'Datas dos snapshots/receções Birdeye ausentes, inválidas, antigas ou futuras; não permitem nível A.',
        {'prefilter7': dates7, 'prefilter30': dates30})

    period_ok, period = _temporal_evidence(deep, now, criteria['max_data_age_hours'])
    complete = deep.get('complete') is True and period_ok
    invalid_count = _count(_mapping(deep.get('coverage')).get('invalid_count', 0))
    integrity_ok = invalid_count == 0
    add('deep_coverage', complete, complete and integrity_ok,
        'Histórico de sete dias completo e atual, sem entradas inválidas.' if complete and integrity_ok else
        'Histórico de sete dias, atualidade ou integridade por concluir.',
        dict(period, complete=deep.get('complete') is True, invalid_count=invalid_count))

    try:
        last_activity = parse_time(metrics.get('last_activity_at'))
        active_recently = now-timedelta(hours=24) <= last_activity <= now
        activity_date_known = last_activity <= now
    except (ValueError, TypeError, OverflowError):
        last_activity = None;active_recently = False;activity_date_known = False
    add('recent_activity', activity_date_known, active_recently,
        'Operação observada nas últimas 24 horas.' if active_recently else
        'Atividade nas últimas 24 horas não demonstrada.', {'last_activity_at':last_activity})

    currency_ok = metrics.get('currency', deep.get('currency')) == 'SOL'
    fees_complete = metrics.get('fees_complete') is True
    add('sol_costs', fees_complete, fees_complete,
        'Taxas de rede SOL conhecidas no histórico analisado.' if fees_complete else
        'Existem taxas SOL por apurar no histórico analisado.')
    known_tokens = _count(metrics.get('known_tokens_count'))
    tokens_count = _count(metrics.get('tokens_count'))
    unknown_count = _count(metrics.get('unknown_tokens_count'))
    if known_tokens is None and tokens_count is not None and unknown_count is not None and unknown_count <= tokens_count:
        known_tokens = tokens_count-unknown_count
    sample_ok = known_tokens is not None and known_tokens >= criteria['min_tokens']
    add('sol_sample', currency_ok and known_tokens is not None, sample_ok,
        'Amostra mínima de tokens com custo SOL conhecido, incluindo abertos no pior caso.' if sample_ok else
        'Amostra SOL conhecida insuficiente; a contagem não é uma prova estatística de rentabilidade.',
        {'known_tokens_count': known_tokens, 'minimum': criteria['min_tokens'], 'currency': metrics.get('currency')})
    unknown = _fraction(metrics.get('unknown_cost_fraction'))
    scope_ok = currency_ok and unknown is not None and unknown <= criteria['max_unknown_cost_fraction']
    add('known_cost_scope', currency_ok and unknown is not None, scope_ok,
        'Proporção de tokens com custo desconhecido dentro do teto.' if scope_ok else
        'Demasiados custos desconhecidos ou cobertura SOL não demonstrada.',
        {'unknown_cost_fraction': unknown, 'maximum': criteria['max_unknown_cost_fraction']})

    worst = _number(metrics.get('worst_case_sol'))
    positive_worst = currency_ok and worst is not None and worst > ZERO
    add('sol_worst_case', currency_ok and worst is not None, positive_worst,
        'Subtotal SOL conhecido positivo após perda total do custo das posições abertas.' if positive_worst else
        'Subtotal SOL conhecido não positivo ou impossível de calcular.',
        {'worst_case_sol': worst, 'whole_wallet_result_verified': False})
    pf = _number(metrics.get('profit_factor'))
    positive_sum = _number(metrics.get('positive_results_sum'))
    no_losses = (metrics.get('no_observed_losses') is True and positive_sum is not None and positive_sum > ZERO)
    pf_known = currency_ok and (pf is not None and pf >= ZERO or no_losses)
    pf_ok = pf_known and (pf is not None and pf >= criteria['min_profit_factor'] or no_losses)
    add('profit_factor', pf_known, pf_ok,
        'Profit factor mínimo ou condição explícita sem perdas observadas cumprida.' if pf_ok else
        'Profit factor insuficiente ou por apurar no cenário conservador SOL.',
        {'profit_factor': pf, 'minimum': criteria['min_profit_factor'], 'no_observed_losses': no_losses})
    residual_best = _number(metrics.get('without_best_sol'))
    residual_top3 = _number(metrics.get('without_top3_sol'))
    residual_known = currency_ok and residual_best is not None and residual_top3 is not None
    residual_ok = residual_known and residual_best > ZERO and residual_top3 > ZERO
    add('winner_removal', residual_known, residual_ok,
        'Saldo conservador continua positivo sem melhor token e sem os três melhores.' if residual_ok else
        'Dependência dos vencedores ou resultados residuais por apurar.',
        {'without_best_sol': residual_best, 'without_top3_sol': residual_top3})
    top1 = _fraction(metrics.get('top1_share'))
    top3 = _fraction(metrics.get('top3_share'))
    concentration_known = currency_ok and top1 is not None and top3 is not None and top1 <= top3
    concentration_ok = concentration_known and top1 <= criteria['max_top1_share'] and top3 <= criteria['max_top3_share']
    add('concentration', concentration_known, concentration_ok,
        'Concentração na soma dos ganhos positivos dentro dos tetos.' if concentration_ok else
        'Concentração excessiva ou não demonstrada.',
        {'top1_share': top1, 'top3_share': top3, 'denominator': 'soma dos resultados positivos por token no pior caso'})

    trades = _number(metrics.get('trades_per_day'))
    rapid = _fraction(metrics.get('rapid_close_fraction'))
    activity_known = trades is not None and trades >= ZERO and rapid is not None
    activity_ok = activity_known and trades <= criteria['max_trades_per_day'] and rapid <= criteria['max_rapid_close_fraction']
    add('execution_intensity', activity_known, activity_ok,
        'Frequência e rapidez observadas dentro do universo acompanhável.' if activity_ok else
        'Estratégia demasiado intensa/rápida ou dados insuficientes.',
        {'trades_per_day': trades, 'rapid_close_fraction': rapid})
    holding = _number(metrics.get('median_holding_seconds'))
    hold_known = holding is not None and holding >= ZERO
    hold_ok = hold_known and holding >= criteria['min_followable_hold_seconds']
    add('holding_horizon', hold_known, hold_ok,
        'Mediana de manutenção observada compatível com o horizonte mínimo.' if hold_ok else
        'Manutenção demasiado curta ou por apurar; execução de seguidor não verificada.',
        {'median_holding_seconds': holding, 'minimum': criteria['min_followable_hold_seconds']})

    wallet_ok = valid_address(candidate.get('wallet'))
    add('identity', True, wallet_ok, 'Endereço Solana válido.' if wallet_ok else 'Endereço Solana inválido.')
    excluded = candidate.get('excluded') is True
    if excluded:
        add('cohort_exclusion', True, False, 'Candidata excluída pelos critérios da população ou pré-triagem.')
    all_pass = bool(checks) and all(row['status'] == Status.PASS.value for row in checks)
    has_deep = bool(deep) and bool(metrics)
    # B e C descrevem a evidência disponível, não uma aprovação financeira.
    level = 'A' if all_pass else 'B' if has_deep and positive7 else 'C'
    hard_names = {'provider_7d', 'provider_30d', 'sol_worst_case', 'profit_factor', 'winner_removal',
                  'concentration', 'execution_intensity', 'holding_horizon', 'identity', 'cohort_exclusion'}
    hard_rejected = excluded or any(row['name'] in hard_names and row['status'] == Status.FAIL.value for row in checks)
    origin_count = _origin_count(candidate.get('origins', []))
    wr = _fraction(metrics.get('win_rate'))
    components = []
    _score_component(components, 'PnL fornecedor 7d e pré-filtro', 6, positive7)
    _score_component(components, 'PnL fornecedor 30d e pré-filtro', 6, positive30)
    _score_component(components, 'Cobertura profunda atual', 10, complete and integrity_ok)
    _score_component(components, 'Amostra de tokens conhecidos', 10,
                     Decimal(known_tokens)/criteria['min_tokens'] if known_tokens is not None and currency_ok else None)
    _score_component(components, 'Resultado conservador SOL positivo', 10, positive_worst)
    _score_component(components, 'Profit factor', 12,
                     ONE if no_losses else pf/criteria['min_profit_factor'] if pf is not None and currency_ok else None)
    _score_component(components, 'Sem melhor token', 6, currency_ok and residual_best is not None and residual_best > ZERO)
    _score_component(components, 'Sem três melhores tokens', 6, currency_ok and residual_top3 is not None and residual_top3 > ZERO)
    _score_component(components, 'Concentração', 8, concentration_ok)
    _score_component(components, 'Cobertura de custos', 8,
                     ONE-unknown if unknown is not None and currency_ok else None)
    _score_component(components, 'Horizonte e rapidez observados', 10, hold_ok and activity_ok)
    _score_component(components, 'Win rate secundário', 4,
                     wr/criteria['min_win_rate'] if wr is not None and currency_ok and criteria['min_win_rate'] else ZERO)
    _score_component(components, 'Repetição em tokens de origem', 4, min(origin_count, 2)/2)
    score = round(sum(item['points'] for item in components), 2)
    reason_rows = [row for row in checks if row['status'] != Status.PASS.value]
    original_reasons = []
    for source in (candidate, pre7, pre30, deep):
        for value in source.get('reasons', []) if isinstance(source.get('reasons', []), list) else []:
            if isinstance(value, str):
                original_reasons.append(value)
            elif isinstance(value, dict):
                message = value.get('message', value.get('reason'))
                if isinstance(message, str):
                    original_reasons.append(message)
    unverified = [{'name': name, 'reason': text} for name, text in UNVERIFIED]
    if unknown is None or unknown > ZERO:
        unverified.append({'name': 'unknown_token_costs', 'reason': 'Custos e resultados dos tokens UNKNOWN NÃO VERIFICADOS; não são convertidos em zero.'})
    if (known7 and pre7.get('complete') is not True) or (known30 and pre30.get('complete') is not True):
        unverified.append({'name': 'provider_partial_tokens', 'reason': 'Paginação de tokens do fornecedor NÃO VERIFICADA; os headlines 7d/30d são proxies separados da contabilidade SOL.'})
    if not provider_dates_ok:
        unverified.append({'name': 'provider_dates', 'reason': 'Atualidade e coerência temporal dos pré-filtros 7d/30d NÃO VERIFICADAS.'})
    if not complete:
        unverified.append({'name': 'complete_history', 'reason': 'Histórico integral de sete dias NÃO VERIFICADO.'})
    secondary_notes = []
    if wr is not None and wr < criteria['min_win_rate']:
        secondary_notes.append('Win rate abaixo da referência secundária; payoff e perdas permanecem explícitos.')
    result.update({
        'evidence_level': level, 'tier': level, 'score': score, 'score_components': components,
        'assessment_passed': all_pass, 'ranking_checks': checks, 'hard_rejected': hard_rejected,
        'ranking_status': 'CRITÉRIOS OBSERVÁVEIS CUMPRIDOS' if all_pass else
                          'REPROVADA NOS CRITÉRIOS DO RANKING' if hard_rejected else 'EVIDÊNCIA INCOMPLETA',
        'reason_codes': [row['name'] for row in reason_rows],
        'reasons': list(dict.fromkeys(original_reasons+[row['reason'] for row in reason_rows])),
        'unverified': unverified, 'distinct_origin_tokens': origin_count,
        'evaluated_at': now, 'telegram_eligible': False,
        'score_definition': 'Pontuação fixa de organização da evidência, não probabilidade de lucro. Falhas não são compensadas por pontos.',
        'result_scope': 'Subtotal conservador dos tokens com custo SOL conhecido; não resultado global ou garantia de rentabilidade.',
        'median_return_informational': metrics.get('median_return'), 'secondary_notes': secondary_notes,
    })
    return result


def rank_candidates(candidates, config, now):
    """Devolver até três candidatas, incluindo evidência parcial e motivos.

    Reprovações financeiras/estratégicas conhecidas ficam depois das candidatas
    sem essas reprovações; nível e score não autorizam envio. Empates preservam a
    ordem de entrada, sem depender do endereço ou de ajustamentos a exemplos.
    """
    now = parse_time(now)
    criteria = _config(_mapping(config))
    ranked = []
    seen = set()
    for position, candidate in enumerate(candidates or []):
        if not isinstance(candidate, dict):
            continue
        wallet = candidate.get('wallet')
        if isinstance(wallet, str) and wallet in seen:
            continue
        if isinstance(wallet, str):
            seen.add(wallet)
        value = _rank_one(candidate, criteria, now)
        ranked.append((value, position))
    ranked.sort(key=lambda pair: (pair[0]['hard_rejected'],
        {'A': 0, 'B': 1, 'C': 2}[pair[0]['evidence_level']], -pair[0]['score'], pair[1]))
    result = []
    for rank, (value, _) in enumerate(ranked[:3], 1):
        value['rank'] = rank
        result.append(value)
    return result
