"""Pré-filtro barato WAC do fornecedor, separado do FIFO próprio em SOL."""
from __future__ import annotations

from decimal import Decimal

from .birdeye import BirdeyeError
from .collection import decimal, valid_address
from .models import iso, parse_time

ZERO = Decimal(0)
ENDPOINT = '/wallet/v2/pnl/details'


def _integer(value):
    number = decimal(value)
    return int(number) if number is not None and number >= 0 and number == number.to_integral_value() else None


def _nested(row, group, field):
    group_value = row.get(group)
    return decimal(group_value.get(field)) if isinstance(group_value, dict) else None


def prefilter_wallet(client, wallet, days, config, now):
    """Uma aprovação aqui significa apenas passar um pré-filtro do fornecedor.

    PF/concentração só existem com todos os tokens da resposta paginada. Uma
    página parcial pode ordenar provisoriamente por summary e originar nível C;
    não pode receber uma aprovação contabilística nem compensar a falta de dados.
    """
    if not valid_address(wallet) or days not in (7, 30):
        raise ValueError('Carteira ou período do pré-filtro inválidos.')
    settings = config.get('profit', config)
    now = parse_time(now)
    result = {'wallet': wallet, 'days': days, 'tokens_count': None, 'pnl_usd': None,
              'realized_pnl_usd': None, 'unrealized_pnl_usd': None,
              'win_rate': None, 'profit_factor': None, 'top1_share': None,
              'top3_share': None, 'trade_count': None, 'trades_per_day': None,
              'complete': False, 'passed': False, 'reasons': [], 'tokens': [],
              'raw_metrics': {'method': 'wac', 'position_scope': 'duration_only',
                              'duration': str(days)+'d', 'observed_at': iso(now)},
              'limitations': ['Métricas USD da Birdeye para pré-filtro; não são FIFO próprio em SOL, lucro líquido auditado ou prova de execução reproduzível.']}
    maximum_pages = max(1, min(100, int(settings.get('prefilter_max_pages', 1))))
    raw_rows, summary, snapshot_at = [], None, None
    seen_tokens = set()
    completed = False
    try:
        for page in range(maximum_pages):
            response = client.request('POST', ENDPOINT, body={
                'wallet': wallet, 'duration': str(days)+'d', 'pnl_method': 'wac',
                'position_scope': 'duration_only', 'sort_by': 'last_trade',
                'sort_type': 'desc', 'offset': page*100, 'limit': 100,
            })
            data = response.get('data') if isinstance(response, dict) else None
            if not isinstance(data, dict) or not isinstance(data.get('tokens'), list) or not isinstance(data.get('summary'), dict):
                result['reasons'].append('pnl_schema_unknown'); return result
            candidate_summary = data['summary']
            if summary is not None and summary != candidate_summary:
                result['reasons'].append('pnl_pagination_changed'); return result
            summary = candidate_summary
            snapshot_at = (data.get('meta') or {}).get('time') if isinstance(data.get('meta'), dict) else None
            total = _integer(summary.get('unique_tokens'))
            if total is None:
                result['reasons'].append('pnl_token_count_unknown'); return result
            for token_row in data['tokens']:
                if not isinstance(token_row, dict) or not valid_address(token_row.get('address')):
                    result['reasons'].append('pnl_token_schema_unknown'); return result
                token = token_row['address']
                if token in seen_tokens:
                    result['reasons'].append('pnl_duplicate_token'); return result
                seen_tokens.add(token)
                raw_rows.append(token_row)
            if len(raw_rows) > total:
                result['reasons'].append('pnl_pagination_incoherent'); return result
            if len(raw_rows) == total:
                completed = True; break
            if not data['tokens']:
                result['reasons'].append('pnl_empty_nonterminal_page'); break
    except BirdeyeError as exc:
        result['reasons'].append('pnl_unavailable:'+type(exc).__name__)
        return result

    if summary is None:
        result['reasons'].append('pnl_summary_unknown'); return result
    result['tokens_count'] = _integer(summary.get('unique_tokens'))
    counts = summary.get('counts') if isinstance(summary.get('counts'), dict) else {}
    result['trade_count'] = _integer(counts.get('total_trade'))
    result['trades_per_day'] = Decimal(result['trade_count'])/Decimal(days) if result['trade_count'] is not None else None
    result['pnl_usd'] = _nested(summary, 'pnl', 'total_usd')
    result['realized_pnl_usd'] = _nested(summary, 'pnl', 'realized_profit_usd')
    result['unrealized_pnl_usd'] = _nested(summary, 'pnl', 'unrealized_usd')
    result['raw_metrics'].update({
        'snapshot_at': snapshot_at, 'unique_tokens': result['tokens_count'],
        'counts': {key: decimal(counts.get(key)) for key in ('total_buy','total_sell','total_trade','total_win','total_loss','win_rate')},
        'cashflow_usd': {key: _nested(summary,'cashflow_usd',key) for key in ('total_invested','total_sold','current_value')},
        'pnl': {key: _nested(summary,'pnl',key) for key in ('realized_profit_usd','realized_profit_percent','unrealized_usd','total_usd','avg_profit_per_trade_usd')},
        'pages_complete': completed, 'pages_requested': page+1,
        'win_rate_definition': 'Tokens com total_usd positivo / tokens da resposta completa; inclui resultado marcado e não prova lucro líquido.',
        'concentration_definition': 'Contribuição por token na soma dos total_usd positivos; nunca usa lucro líquido total no denominador.',
    })
    for row in raw_rows:
        result['tokens'].append({
            'token': row['address'], 'pnl_usd': _nested(row,'pnl','total_usd'),
            'realized_pnl_usd': _nested(row,'pnl','realized_profit_usd'),
            'unrealized_pnl_usd': _nested(row,'pnl','unrealized_usd'),
            'cost_of_quantity_sold_usd': _nested(row,'cashflow_usd','cost_of_quantity_sold'),
            'total_invested_usd': _nested(row,'cashflow_usd','total_invested'),
            'current_value_usd': _nested(row,'cashflow_usd','current_value'),
            'holding': _nested(row,'quantity','holding'),
            'trade_count': _integer((row.get('counts') or {}).get('total_trade')) if isinstance(row.get('counts'), dict) else None,
        })
    result['complete'] = completed and all(token['pnl_usd'] is not None for token in result['tokens'])
    if result['complete'] and result['tokens']:
        outcomes = [token['pnl_usd'] for token in result['tokens']]
        positive = sum((value for value in outcomes if value > ZERO), ZERO)
        negative = -sum((value for value in outcomes if value < ZERO), ZERO)
        winners = sorted((value for value in outcomes if value > ZERO), reverse=True)
        result['win_rate'] = Decimal(sum(value > ZERO for value in outcomes))/Decimal(len(outcomes))
        result['profit_factor'] = positive/negative if negative > ZERO else None
        result['raw_metrics']['no_provider_token_losses'] = negative == ZERO and positive > ZERO
        result['raw_metrics']['positive_results_sum'] = positive
        if positive > ZERO:
            result['top1_share'] = winners[0]/positive
            result['top3_share'] = sum(winners[:3],ZERO)/positive
    elif not completed:
        result['limitations'].append('Paginação parcial: PF, taxa de sucesso por token e concentração permanecem desconhecidos.')
    else:
        result['limitations'].append('Valores por token em falta; não substituir desconhecidos por zero.')

    if result['pnl_usd'] is None:
        result['reasons'].append('provider_pnl_unknown')
    elif result['pnl_usd'] <= ZERO:
        result['reasons'].append('provider_pnl_not_positive')
    if result['trade_count'] is None:
        result['reasons'].append('provider_activity_unknown')
    elif result['trade_count'] == 0:
        result['reasons'].append('provider_activity_absent')
    elif result['trades_per_day'] > Decimal(str(settings.get('max_trades_per_day',100))):
        result['reasons'].append('activity_over_daily_limit')
    if result['tokens_count'] < max(1,int(settings.get('prefilter_min_tokens',1))):
        result['reasons'].append('prefilter_sample_insufficient')
    # Os indicadores adicionais ordenam o pré-filtro. O motor de evidência e a
    # contabilidade própria decidem depois; um campo desconhecido não é um passe.
    result['passed'] = not result['reasons']
    return result
