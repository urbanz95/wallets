"""Descoberta por PnL fornecido, sem a confundir com contabilidade auditada.

Contratos oficiais revistos em 30-10-2026: gainers usa ``PnL`` (maiúsculas),
mas top_traders usa ``realized_pnl``. Apenas endpoints Standard confirmados.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from .birdeye import AccessDenied, BirdeyeError, BudgetExceeded, InvalidResponse
from .collection import decimal, valid_address
from .models import iso, parse_time

ZERO = Decimal(0)
EXCLUDED_TAGS = frozenset(('sniper', 'bundler', 'insider'))
TOP_ENDPOINT = '/defi/v2/tokens/top_traders'
GAINERS_ENDPOINT = '/trader/gainers-losers'
TOP_FIELDS = (
    'volume', 'trade', 'tradeBuy', 'tradeSell', 'volumeBuy', 'volumeSell',
    'volumeBuyUSD', 'volumeSellUSD', 'volumeUsd', 'totalPnl', 'realizedPnl',
    'unrealizedPnl', 'holdVolume', 'holdVolumeUsd', 'holdAvgPrice',
    'avgBuyPrice', 'avgSellPrice', 'firstTradeUnixTime', 'lastTradeUnixTime',
)
GAINER_FIELDS = ('pnl', 'realized_pnl', 'unrealized_pnl', 'volume', 'trade_count')


def _settings(config):
    return config.get('profit', config)


def _integer(value):
    number = decimal(value)
    return int(number) if number is not None and number >= 0 and number == number.to_integral_value() else None


def _items(response):
    data = response.get('data') if isinstance(response, dict) else None
    items = data.get('items') if isinstance(data, dict) else None
    if not isinstance(items, list) or any(not isinstance(row, dict) for row in items):
        raise InvalidResponse('Ranking Birdeye sem lista de traders válida.')
    return items


def _tags(value):
    if not isinstance(value, list):
        return [], False
    return sorted({tag.strip().lower() for tag in value if isinstance(tag, str) and tag.strip()}), all(isinstance(tag, str) for tag in value)


def discover(client, store, config, now):
    """Devolver candidatas privadas e motivos; nunca imprimir endereços.

    O cliente é responsável por orçamento, reservas e retentativas. Uma quota
    interrompida devolve o progresso já obtido em vez de perder as candidatas.
    """
    settings = _settings(config)
    now = parse_time(now)
    start = now-timedelta(days=7)
    maximum_daily = Decimal(str(settings.get('max_trades_per_day', 100)))
    candidates, rejected, errors = {}, {}, []
    selected_tokens = []
    ordinal = 0

    def reject(wallet, reasons, evidence=None):
        if not wallet:
            return
        row = rejected.setdefault(wallet, {'wallet': wallet, 'reasons': [], 'evidence': []})
        for reason in reasons:
            if reason not in row['reasons']:
                row['reasons'].append(reason)
        if evidence is not None:
            row['evidence'].append(evidence)
        candidates.pop(wallet, None)

    def add(wallet, evidence, origin=None, tags=()):
        nonlocal ordinal
        if wallet in rejected:
            return
        if wallet not in candidates:
            candidates[wallet] = {
                'wallet': wallet, 'origins': [], 'tags': [], 'exclusions': [],
                'discovery': {'sources': [], 'provider_pnl_usd': None,
                              'pnl_usd': None, 'trade_count': None,
                              'labels_verified': False, 'observed_at': iso(now)},
                '_ordinal': ordinal,
            }
            ordinal += 1
        row = candidates[wallet]
        if origin and origin not in row['origins']:
            row['origins'].append(origin)
        row['tags'] = sorted(set(row['tags']) | set(tags))
        row['discovery']['sources'].append(evidence)
        if evidence['endpoint'] == GAINERS_ENDPOINT:
            row['discovery']['provider_pnl_usd'] = evidence['metrics'].get('pnl')
            row['discovery']['trade_count'] = _integer(evidence['metrics'].get('trade_count'))
            row['discovery']['rank_scope'] = 'wallet_provider_1W'
        elif row['discovery'].get('rank_scope') != 'wallet_provider_1W':
            profit = evidence['metrics'].get('realizedPnl')
            old = row['discovery']['provider_pnl_usd']
            if profit is not None and (old is None or profit > old):
                row['discovery']['provider_pnl_usd'] = profit
            row['discovery']['rank_scope'] = 'best_observed_token_realized_pnl'
        row['discovery']['pnl_usd'] = row['discovery']['provider_pnl_usd']

    def result():
        rows = list(candidates.values())
        rows.sort(key=lambda row: (
            -int(len(row['origins']) >= 2), -len(row['origins']),
            -(row['discovery']['provider_pnl_usd'] or ZERO), row['_ordinal'],
        ))
        for row in rows:
            row['origins'].sort()
            row['discovery']['distinct_token_origins'] = len(row['origins'])
            row['discovery']['pnl_definition'] = 'Métricas do fornecedor para ordenação; não são lucro FIFO nem confirmação de estratégia acompanhável.'
            row.pop('_ordinal', None)
        return {'candidates': rows, 'rejections': list(rejected.values()),
                'errors': errors, 'selected_tokens': selected_tokens}

    try:
        # O mínimo de PnL implícito do contrato é 1000 USD; não inventar min_pnl=0.
        rows = _items(client.request('GET', GAINERS_ENDPOINT, {
            'type': '1W', 'sort_by': 'PnL',
            'max_trade': int(maximum_daily*7), 'offset': 0,
            'limit': min(100, max(1, int(settings.get('discovery_gainers_limit', 100)))),
        }))
        for rank, row in enumerate(rows):
            wallet = row.get('address')
            if not valid_address(wallet):
                continue
            evidence = {'endpoint': GAINERS_ENDPOINT, 'type': '1W', 'sort_by': 'PnL',
                        'observed_at': iso(now), 'rank': rank,
                        'metrics': {key: decimal(row.get(key)) for key in GAINER_FIELDS}}
            if row.get('network', 'solana') != 'solana':
                reject(wallet, ['unsupported_network'], evidence); continue
            trades = _integer(row.get('trade_count'))
            if trades is None or decimal(row.get('pnl')) is None:
                reject(wallet, ['discovery_metrics_unknown'], evidence); continue
            if trades == 0:
                reject(wallet, ['discovery_activity_absent'], evidence); continue
            if Decimal(trades)/7 > maximum_daily:
                reject(wallet, ['activity_over_daily_limit'], evidence); continue
            if decimal(row.get('pnl')) <= ZERO:
                reject(wallet, ['provider_pnl_not_positive'], evidence); continue
            add(wallet, evidence)
    except BirdeyeError as exc:
        errors.append({'stage': 'discovery_gainers', 'reason': type(exc).__name__})
        if isinstance(exc, (AccessDenied, BudgetExceeded)):
            return result()

    maximum_tokens = max(0, min(5, int(settings.get('discovery_max_tokens', 5))))
    if maximum_tokens == 0:
        return result()
    minimum_change = settings.get('discovery_min_price_change_24h_percent')
    params = {
        'sort_by': 'volume_24h_usd', 'sort_type': 'desc', 'offset': 0, 'limit': 100,
        'platform_name': settings.get('discovery_platform_name', 'pump.fun'),
        'min_liquidity': settings.get('discovery_min_liquidity', 100000),
        'min_volume_24h_usd': settings.get('discovery_min_volume_24h_usd', 100000),
        'min_recent_listing_time': int(start.timestamp()),
        'max_recent_listing_time': int(now.timestamp()),
    }
    if minimum_change is not None:
        params['min_price_change_24h_percent'] = minimum_change
    try:
        token_rows = _items(client.request('GET', '/defi/v3/token/list', params))
    except BirdeyeError as exc:
        errors.append({'stage': 'discovery_tokens', 'reason': type(exc).__name__})
        return result()
    seen = set()
    for token in token_rows:
        address = token.get('address')
        if not valid_address(address) or address in seen:
            continue
        try:
            listed = parse_time(token['recent_listing_time'])
            liquidity, volume = decimal(token.get('liquidity')), decimal(token.get('volume_24h_usd'))
            qualifies = (start <= listed <= now and liquidity is not None
                         and liquidity >= Decimal(str(params['min_liquidity']))
                         and volume is not None and volume >= Decimal(str(params['min_volume_24h_usd'])))
            if minimum_change is not None:
                change = decimal(token.get('price_change_24h_percent'))
                qualifies = qualifies and change is not None and change >= Decimal(str(minimum_change))
        except (ValueError, TypeError, KeyError, OverflowError):
            qualifies = False
        if not qualifies:
            continue
        seen.add(address)
        selected_tokens.append({'token': address, 'observed_at': iso(now),
                                'listed_at': iso(listed), 'liquidity_usd': liquidity,
                                'volume_24h_usd': volume})
        store.record_token_selection(address, now, token)
        try:
            rows = _items(client.request('GET', TOP_ENDPOINT, {
                'address': address, 'time_frame': '7d', 'sort_by': 'realized_pnl',
                'sort_type': 'desc', 'offset': 0, 'limit': 10,
            }))
            for rank, trader in enumerate(rows):
                wallet = trader.get('owner')
                if not valid_address(wallet):
                    continue
                tags, tags_valid = _tags(trader.get('tags'))
                evidence = {'endpoint': TOP_ENDPOINT, 'time_frame': '7d',
                            'sort_by': 'realized_pnl', 'rank': rank, 'token': address,
                            'recent_listing_time': iso(listed), 'observed_at': iso(now),
                            'tags': tags, 'tags_available': tags_valid,
                            'metrics': {key: decimal(trader.get(key)) for key in TOP_FIELDS}}
                excluded = sorted(set(tags) & EXCLUDED_TAGS)
                if excluded:
                    reject(wallet, ['excluded_tag:'+tag for tag in excluded], evidence); continue
                trades = _integer(trader.get('trade'))
                if trades is not None and Decimal(trades)/7 > maximum_daily:
                    reject(wallet, ['activity_over_daily_limit'], evidence); continue
                last = trader.get('lastTradeUnixTime')
                if last is not None:
                    try:
                        if not start <= parse_time(last) <= now:
                            reject(wallet, ['discovery_activity_outside_window'], evidence); continue
                    except (ValueError, TypeError, OverflowError):
                        reject(wallet, ['discovery_time_unknown'], evidence); continue
                add(wallet, evidence, address, tags)
                if wallet not in rejected:
                    store.record_discovery(wallet, address, now, {
                        'profile': 'profit-evidence', 'time_frame': '7d',
                        'recent_listing_time': iso(listed), **evidence,
                    })
        except BirdeyeError as exc:
            errors.append({'stage': 'discovery_token_traders', 'reason': type(exc).__name__})
            if isinstance(exc, (AccessDenied, BudgetExceeded)):
                break
        if len(selected_tokens) >= maximum_tokens:
            break
    return result()
