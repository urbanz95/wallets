"""Sondagem isolada, de um único pedido, ao contrato Wallet API.

Não descobre novas identidades nem altera a última fotografia de ranking. A
resposta e a carteira permanecem exclusivamente no estado privado cifrado.
"""
from __future__ import annotations

import os

from .birdeye import BirdeyeClient, BirdeyeError
from .collection import valid_address
from .models import dumps, iso, utcnow
from .profit_persistence import latest_run


CURSOR = 'profit:wallet-access-probe'
ENDPOINT = '/wallet/v2/pnl/details'


def _wallet(snapshot):
    if not isinstance(snapshot, dict):
        return None
    for field in ('candidates', 'ranking'):
        rows = snapshot.get(field)
        if not isinstance(rows, list):
            continue
        for candidate in rows:
            if isinstance(candidate, dict) and valid_address(candidate.get('wallet')):
                return candidate['wallet']
    return None


def run_probe(store, config, budget, checkpoint, output, now=None):
    """Um POST sem cache/retentativas, com reserva central de apenas 30 CU."""
    now = now or utcnow()
    snapshot = latest_run(store)
    wallet = _wallet(snapshot)
    record = {'schema': 'wallet-access-probe-v1', 'evaluated_at': iso(now),
              'source_run_started_at': snapshot.get('started_at') if isinstance(snapshot, dict) else None,
              'endpoint': ENDPOINT, 'method': 'POST', 'http_status': None,
              'attempts': 0, 'state': 'not_attempted'}
    if wallet is None:
        record['reason'] = 'no_prior_wallet'
        store.set_cursor(CURSOR, dumps(record))
        checkpoint()
        return {'counts': {'wallet_probe_candidates': 0, 'wallet_probe_attempts': 0,
                           'wallet_probe_success': 0},
                'rejection_histogram': {'wallet_probe_no_prior_wallet': 1}}

    record['wallet'] = wallet
    settings = dict(config['api'])
    settings.update(max_retries=0, max_requests_per_run=1, max_credits_per_run=30)
    client = BirdeyeClient(os.environ.get('BIRDEYE_API_KEY', ''), store, settings,
                           clock=lambda: now)
    success = False
    with budget.stage('wallet_probe', birdeye=30, store=store,
                      clients={'birdeye': client}):
        try:
            response = client.request('POST', ENDPOINT, body={
                'wallet': wallet, 'duration': '7d', 'pnl_method': 'wac',
                'position_scope': 'duration_only', 'sort_by': 'last_trade',
                'sort_type': 'desc', 'offset': 0, 'limit': 100,
            }, cache=False)
            record.update(state='response_received', http_status=200, response=response)
            success = True
        except BirdeyeError as exc:
            status = getattr(client, 'last_http_status', None)
            record.update(state='request_failed', failure_type=type(exc).__name__,
                          http_status=status if type(status) is int and 100 <= status <= 599 else None)
        record['attempts'] = client.requests
        store.set_cursor(CURSOR, dumps(record))
        checkpoint()

    status = record['http_status']
    reason = 'wallet_probe_http_'+str(status) if status is not None else 'wallet_probe_no_http_response'
    return {'counts': {'wallet_probe_candidates': 1, 'wallet_probe_attempts': client.requests,
                       'wallet_probe_success': int(success)},
            'rejection_histogram': {reason: 1}}
