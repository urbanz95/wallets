"""Janela e fila explícitas da experiência recente, sem autorização Telegram."""
from __future__ import annotations

import json

from .models import dumps, iso, parse_time

PROFILE = 'recent-7d'


def validate_recent(config):
    settings = config.get('recent', {})
    if not isinstance(settings, dict) or not isinstance(settings.get('enabled', False), bool):
        raise ValueError('Perfil recente inválido')
    if not settings.get('enabled', False):
        return
    for name, default, maximum in (
        ('window_days', 7, 7), ('require_activity_hours', 24, 24),
        ('min_activity_days', 4, 7), ('min_profitable_days', 3, 7),
    ):
        value = settings.get(name, default)
        if type(value) is not int or not 0 < value <= maximum:
            raise ValueError('Limite do perfil recente inválido')
    if settings.get('window_days', 7) != 7 or config['lookback_days'] != 7:
        raise ValueError('A experiência recente exige uma janela exata de 7 dias')
    discovery = config['discovery']
    if (discovery.get('time_frame') != '7d'
            or not 0 <= discovery.get('min_age_days', -1) <= discovery.get('max_age_days', 8) <= 7):
        raise ValueError('A descoberta recente exige tokens e traders dos últimos 7 dias')
    if config.get('selection', {}).get('mode', 'strict') != 'observed':
        raise ValueError('A experiência recente exige avaliação observada separada')
    if 'collect_optional' in config['api'] and not isinstance(config['api']['collect_optional'], bool):
        raise ValueError('Recolha complementar inválida')
    for name, default, ceiling in (
        ('discovery_credit_budget', 160, config['api']['max_credits_per_run']),
        ('discovery_request_budget', 5, config['api']['max_requests_per_run']),
    ):
        value = settings.get(name, default)
        if type(value) is not int or not 0 < value <= ceiling:
            raise ValueError('Orçamento de descoberta recente inválido')


def enabled(config):
    return config.get('recent', {}).get('enabled', False) is True


def discovery_rows(store, wallet, start, end):
    """Só origens novas deste perfil; não reciclar a descoberta histórica de 30d."""
    result = []
    for row in store.discovery_evidence(wallet, end):
        try:
            stamp = parse_time(row['observed_at'])
            evidence = row['evidence']
            if not start <= stamp <= end or not isinstance(evidence, dict):
                continue
            if evidence.get('profile') != PROFILE:
                continue
            if evidence.get('time_frame', '7d') != '7d':
                continue
            listed = evidence.get('recent_listing_time')
            if listed is None:
                listed = evidence.get('token_listed_at')
            if listed is None:
                # Descobertas Helius antigas podem não conter a listagem. Usar
                # apenas a última seleção Birdeye já conhecida neste corte.
                selection = store.db.execute(
                    'SELECT payload FROM token_selections WHERE token=? AND at<=? ORDER BY at DESC,id DESC LIMIT 1',
                    (row['token'], iso(end)),
                ).fetchone()
                payload = json.loads(selection[0]) if selection else None
                listed = payload.get('recent_listing_time') if isinstance(payload, dict) else None
            if listed is None or not start <= parse_time(listed) <= end:
                continue
            result.append(row)
        except (ValueError, TypeError, KeyError, OverflowError):
            # Evidência antiga ou malformada não prova uma origem recente.
            continue
    return result



def newly_observed_candidates(store, start, end):
    """Recuperar descobertas parciais deste corte na ordem de inserção.

    O coletor pode atingir quota depois de gravar observações e antes de
    devolver a lista. Só a observação exatamente em T prova descoberta nesta
    execução; origens antigas não são promovidas a novas por este caminho.
    """
    end = parse_time(end)
    result, validated = [], {}
    rows = store.db.execute(
        'SELECT wallet,token,payload FROM discovery_observations WHERE at=? ORDER BY rowid',
        (iso(end),),
    ).fetchall()
    for wallet, token, payload in rows:
        if wallet in result:
            continue
        if wallet not in validated:
            validated[wallet] = {
                (row['token'], dumps(row['evidence']))
                for row in discovery_rows(store, wallet, start, end)
                if parse_time(row['observed_at']) == end
            }
        if (token, payload) in validated[wallet]:
            result.append(wallet)
    return result


def select_candidates(store, history, fresh, manual, start, end, limit):
    """Reservar vagas para pendentes recentes, sem deixar novas origens monopolizar."""
    fresh = list(dict.fromkeys(fresh))
    stored = store.wallets()
    origins = {wallet: discovery_rows(store, wallet, start, end) for wallet in stored}
    qualified = {wallet for wallet, rows in origins.items() if rows}
    qualified.update(fresh)
    manual = list(dict.fromkeys(manual))[:limit]
    qualified.difference_update(manual)
    pending = [wallet for wallet in qualified if wallet in history]
    pending.sort(key=lambda wallet: (
        bool(history[wallet].observed_eligible), history[wallet].evaluated_at, wallet,
    ))
    # Repetir o mesmo mint não cria novas oportunidades independentes. Só
    # contam origens validadas nesta janela, nunca PnL ou volume histórico.
    origin_counts = {wallet: len({row['token'] for row in origins.get(wallet, [])})
                     for wallet in qualified}
    fresh_order = {wallet: index for index, wallet in enumerate(fresh)}
    new = sorted(qualified.difference(history), key=lambda wallet: (
        -origin_counts[wallet], fresh_order.get(wallet, len(fresh)), wallet,
    ))
    room = limit - len(manual)
    new_slots = 1 if new and (room > 1 or not pending) else 0
    selected_pending = pending[:max(0, room-new_slots)]
    selected_new = new[:max(0, room-len(selected_pending))]
    result = manual + selected_pending + selected_new
    if len(result) < limit:
        result.extend(pending[len(selected_pending):len(selected_pending)+limit-len(result)])
    return result, {
        'profile': PROFILE, 'eligible_origin_candidates': len(qualified),
        'pending_candidates': len(pending), 'new_candidates': len(new),
        'stored_without_recent_origin': len(set(stored).difference(qualified).difference(manual)),
        'selected_pending': len(selected_pending), 'selected_new': len(selected_new),
        'selected_manual': len(manual), 'selected_total': len(result),
        'selected_new_distinct_origins': [origin_counts[wallet] for wallet in selected_new],
    }
