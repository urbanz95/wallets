"""Completar o PnL do fornecedor com um orçamento explícito de páginas.

A primeira página também ocupa uma vaga no orçamento, mesmo quando o cliente
consegue servi-la da cache. Não se reutilizam páginas de execuções diferentes
nem se transforma uma resposta parcial em contabilidade própria.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy

from .birdeye import BirdeyeError
from .collection import decimal, valid_address
from .profit_prefilter import prefilter_wallet


def _count(value):
    number = decimal(value)
    if number is None or number < 0 or number != number.to_integral_value():
        return None
    return int(number)


def _limits(config, max_pages):
    if type(max_pages) is not int or max_pages < 0:
        raise ValueError('Orçamento de páginas inválido.')
    settings = config.get('profit', config)
    wallets = _count(settings.get('refinement_max_wallets', 2))
    ceiling = _count(settings.get('refinement_max_pages_per_wallet', 10))
    if wallets is None or ceiling is None:
        raise ValueError('Limites de refinamento inválidos.')
    return settings, wallets, min(10, ceiling)


def _plan(candidates, config, max_pages):
    _, wallet_limit, page_limit = _limits(config, max_pages)
    plan = []
    used_pages = used_wallets = 0
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        initial = candidate.get('prefilter7')
        if not isinstance(initial, dict):
            continue
        profit = decimal(initial.get('pnl_usd'))
        if (initial.get('passed') is not True or initial.get('complete') is not False
                or profit is None or profit <= 0):
            continue
        wallet = candidate.get('wallet')
        if (not valid_address(wallet) or initial.get('wallet') != wallet
                or initial.get('days') != 7 or initial.get('_future_snapshot') is True):
            plan.append((candidate, 0, 'refinement_identity_or_time_invalid'))
            continue
        raw = initial.get('raw_metrics')
        raw = raw if isinstance(raw, dict) else {}
        tokens = _count(initial.get('tokens_count'))
        settlement = _count(raw.get('excluded_settlement_tokens_count', 0))
        if tokens is None or tokens == 0 or settlement is None:
            plan.append((candidate, 0, 'refinement_count_unknown'))
            continue
        # Só estimar o necessário; não prometer que uma página móvel adicional
        # mantém o mesmo universo/snapshot. O pré-filtro continua a verificá-lo.
        needed = max(2, (tokens + settlement + 99) // 100)
        if needed > page_limit:
            reason = 'refinement_page_limit'
        elif used_wallets >= wallet_limit:
            reason = 'refinement_wallet_limit'
        elif used_pages + needed > max_pages:
            reason = 'refinement_budget_deferred'
        else:
            reason = None
            used_wallets += 1
            used_pages += needed
        plan.append((candidate, needed, reason))
    return plan


def refinement_reservation(candidates, config, max_pages):
    """Páginas a reservar antes da rede; não altera candidatas nem chama a API."""
    return sum(pages for _, pages, reason in _plan(candidates, config, max_pages)
               if reason is None)


def _valid_complete(result, wallet):
    if not isinstance(result, dict):
        return False
    raw = result.get('raw_metrics')
    raw = raw if isinstance(raw, dict) else {}
    # Uma fotografia nova completa e negativa também é informação válida:
    # deve substituir a decisão atual, conservando a triagem inicial para auditoria.
    known_failure=(result.get('passed') is False and any(reason in
        ('provider_pnl_not_positive','provider_activity_absent','prefilter_sample_insufficient')
        for reason in result.get('reasons',[]) if isinstance(reason,str)))
    return (result.get('wallet') == wallet and result.get('days') == 7
            and result.get('complete') is True and (result.get('passed') is True or known_failure)
            and result.get('_future_snapshot') is False
            and result.get('response_received') is True
            and raw.get('method') == 'wac'
            and raw.get('position_scope') == 'duration_only'
            and raw.get('duration') == '7d')


def refine_candidates(client, candidates, config, now, max_pages):
    """Refinar candidatas já ordenadas; atualizar com resposta completa válida.

    Devolve exclusivamente contadores/códigos públicos. A prova individual fica
    em ``refinement7`` e o pré-filtro inicial em ``prefilter7_initial``. Falhas,
    alterações entre páginas e dados desconhecidos preservam o headline inicial.
    A reserva é conservadora: cache ou interrupções não dão novas vagas a outra
    carteira nesta chamada; o gestor de orçamento reembolsa pedidos não tentados.
    """
    counts = {'attempted': 0, 'complete': 0, 'deferred': 0, 'pages_allocated': 0,
              'reasons': {}}
    reasons = Counter()
    for candidate, pages, deferred in _plan(candidates, config, max_pages):
        proof = {'status': 'deferred' if deferred else 'attempted',
                 'pages_required': pages, 'pages_allocated': 0 if deferred else pages,
                 'reasons': []}
        candidate['refinement7'] = proof
        if deferred:
            proof['reasons'].append(deferred)
            reasons[deferred] += 1
            counts['deferred'] += 1
            continue
        counts['attempted'] += 1
        counts['pages_allocated'] += pages
        candidate.setdefault('prefilter7_initial', deepcopy(candidate['prefilter7']))
        settings = dict(config.get('profit', config), prefilter_max_pages=pages)
        local_config = dict(config, profit=settings)
        try:
            result = prefilter_wallet(client, candidate['wallet'], 7, local_config, now)
        except (BirdeyeError, ValueError, TypeError, KeyError):
            # Exceções de rede/dados não são texto publicável nem aprovação.
            result = None
            failure = 'refinement_unavailable'
        else:
            if _valid_complete(result, candidate['wallet']):
                candidate['prefilter7'] = result
                proof.update(status='complete', result=result)
                counts['complete'] += 1
                continue
            codes = result.get('reasons', []) if isinstance(result, dict) else []
            failure = ('refinement_pagination_changed' if 'pnl_pagination_changed' in codes
                       else 'refinement_incomplete' if isinstance(result, dict)
                       and result.get('complete') is False else 'refinement_invalid_result')
        proof.update(status='incomplete', result=result, reasons=[failure])
        reasons[failure] += 1
    counts['reasons'] = dict(reasons)
    return counts
