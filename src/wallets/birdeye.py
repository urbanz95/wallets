"""Cliente estritamente limitado aos contratos Standard documentados em capabilities.md."""
from __future__ import annotations
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
import json
import math
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, HTTPRedirectHandler, build_opener
from .models import dumps, utcnow

# Não aceitar URLs arbitrários, RPC, endpoints privados ou upgrades automáticos.
ENDPOINTS = {
    ('GET', '/defi/v3/token/list'): 50,
    ('GET', '/defi/v2/tokens/top_traders'): 30,
    ('GET', '/trader/txs/seek_by_time'): 12,
    ('POST', '/wallet/v2/pnl/details'): 30,
    ('POST', '/wallet/v2/transfer'): 10,
    ('GET', '/wallet/v2/current-net-worth'): 30,
    ('GET', '/blockchain/v1/transaction/detail'): 10,
}

# Só nomes de contrato, nunca valores enviados ou mensagens livres da API.
VALIDATION_FIELDS = frozenset({
    'address', 'wallet', 'after_time', 'before_time', 'tx_type', 'ui_amount_mode',
    'offset', 'limit', 'time_from', 'time_to', 'duration', 'pnl_method',
    'position_scope', 'sort_by', 'sort_type', 'flags', 'cursor', 'token_addresses',
    'platform_name', 'min_liquidity', 'min_volume_24h_usd',
    'min_price_change_24h_percent', 'min_recent_listing_time',
    'max_recent_listing_time', 'time_frame', 'signature',
})
VALIDATION_BOUND_FIELDS = frozenset({
    'ge', 'gt', 'le', 'lt', 'minimum', 'maximum', 'min_length', 'max_length',
    'min_items', 'max_items', 'limit_value',
})


def validation_diagnostic(error, request_fields, secret):
    """Extrai exclusivamente localização/código/limites de validação estruturada."""
    try:
        raw = error.read(16385)
        if len(raw) > 16384:
            return ''
        body = json.loads(raw)
        details = body.get('detail') if isinstance(body, dict) else None
        if not isinstance(details, list):
            return ''
        allowed = (set(request_fields) & VALIDATION_FIELDS) | {'query', 'body', 'index'}
        safe = []
        for detail in details[:8]:
            if not isinstance(detail, dict):
                continue
            entry = {}
            loc = detail.get('loc')
            if isinstance(loc, (list, tuple)) and 0 < len(loc) <= 8:
                if all((isinstance(part, str) and part in allowed)
                       or (type(part) is int and 0 <= part <= 1000000) for part in loc):
                    entry['loc'] = list(loc)
            kind = detail.get('type')
            if isinstance(kind, str) and re.fullmatch('[a-z_]{1,64}', kind):
                if not secret or secret not in kind:
                    entry['type'] = kind
            ctx = detail.get('ctx')
            if isinstance(ctx, dict):
                bounds = {key: value for key, value in ctx.items()
                          if key in VALIDATION_BOUND_FIELDS and type(value) in (int, float)
                          and math.isfinite(value) and abs(value) <= 1000000000000}
                if bounds:
                    entry['ctx'] = bounds
            if entry:
                safe.append(entry)
        return ' Validação: '+dumps(safe)+'.' if safe else ''
    except (OSError, ValueError, TypeError, OverflowError):
        return ''


class BirdeyeError(RuntimeError):
    """Mensagem segura: nunca contém credenciais, cabeçalhos ou corpo remoto."""

class BudgetExceeded(BirdeyeError):
    pass

class AccessDenied(BirdeyeError):
    pass

class InvalidResponse(BirdeyeError):
    pass

class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        raise AccessDenied('Redirecionamento Birdeye recusado para proteger a chave API.')

class BirdeyeClient:
    def __init__(self, api_key, store, config, *, transport=None, clock=utcnow, sleep=time.sleep):
        self.api_key, self.store, self.config = api_key, store, config
        self.clock, self.sleep = clock, sleep
        self.transport = transport or self._http
        self.requests, self.credits = 0, 0
        self.network_verified = False
        self._last_call = None
        self._halted = False

    @staticmethod
    def _http(request):
        with build_opener(_NoRedirect()).open(request, timeout=30) as response:
            return json.loads(response.read(8 * 1024 * 1024), parse_float=Decimal)

    def _reserve(self, cost):
        if self.requests + 1 > self.config.get('max_requests_per_run', 40):
            raise BudgetExceeded('Limite de pedidos por execução atingido; análise adiada.')
        if self.credits + cost > self.config.get('max_credits_per_run', 2000):
            raise BudgetExceeded('Limite de créditos por execução atingido; análise adiada.')
        if not self.store.reserve_budget(self.clock().strftime('%Y-%m'), cost,
                                         self.config.get('monthly_credit_ceiling', 28000)):
            raise BudgetExceeded('Limite mensal local de créditos atingido; análise adiada.')
        # Reservar ANTES da rede: timeout e retentativas também podem gastar CU.
        self.requests += 1
        self.credits += cost
        now = self.clock()
        interval = max(1.0, 1 / min(1.0, max(0.01, self.config.get('requests_per_second', 1))))
        if self._last_call is not None:
            self.sleep(max(0, interval - (now - self._last_call).total_seconds()))
        self._last_call = self.clock()

    def request(self, method, path, params=None, body=None, *, cache=True):
        if self._halted:
            raise AccessDenied('Cliente interrompido após restrição de acesso/quota; sem novas tentativas nesta execução.')
        method = method.upper()
        cost = ENDPOINTS.get((method, path))
        if cost is None:
            raise AccessDenied('Endpoint fora da lista Standard gratuita confirmada.')
        if not self.api_key:
            raise AccessDenied('BIRDEYE_API_KEY não configurada; nenhuma consulta efetuada.')
        query = urlencode(sorted((params or {}).items()))
        key = sha256(dumps([method, path, params, body]).encode()).hexdigest()
        now = self.clock()
        if cache:
            cached = self.store.cache_get(key, now)
            if cached is not None:
                return cached
        data = None if body is None else dumps(body).encode()
        request = Request('https://public-api.birdeye.so' + path + ('?' + query if query else ''),
                          data=data, method=method, headers={
                              'X-API-KEY': self.api_key, 'x-chain': 'solana',
                              'Accept': 'application/json', 'Content-Type': 'application/json'})
        for attempt in range(self.config.get('max_retries', 2) + 1):
            self._reserve(cost)
            try:
                result = self.transport(request)
                optional_success = (method, path) == ('POST', '/wallet/v2/pnl/details')
                if (not isinstance(result, dict) or 'data' not in result
                        or result.get('success') is False
                        or (not optional_success and result.get('success') is not True)):
                    raise InvalidResponse('Resposta Birdeye sem success/data válidos; cobertura não confirmada.')
                self.network_verified = True
                if cache:
                    self.store.cache_put(key, result, self.clock() + timedelta(
                        seconds=min(3600, max(0, self.config.get('cache_ttl_seconds', 900)))))
                return result
            except HTTPError as exc:
                status = exc.code
                if status in (401, 402, 403):
                    self._halted = True
                    raise AccessDenied(f'Birdeye recusou acesso ({status}) em {method} {path}; sem alternativa de fornecedor/plano.') from None
                if status == 429 or status >= 500:
                    if attempt < self.config.get('max_retries', 2):
                        retry = exc.headers.get('Retry-After', '') if exc.headers else ''
                        try:
                            delay = float(retry)
                        except (TypeError, ValueError):
                            delay = 2 ** attempt
                        self.sleep(min(30, max(1, delay)))
                        continue
                if status == 429:
                    self._halted = True
                diagnostic = validation_diagnostic(exc, set(params or {}) | set(body or {}), self.api_key) if status in (400, 422) else ''
                raise BirdeyeError(f'Falha HTTP Birdeye ({status}) em {method} {path}; análise incompleta.'+diagnostic) from None
            except (URLError, TimeoutError, OSError, json.JSONDecodeError, UnicodeDecodeError):
                if attempt < self.config.get('max_retries', 2):
                    self.sleep(min(30, 2 ** attempt))
                    continue
                raise BirdeyeError('Falha de rede/resposta Birdeye; análise incompleta.') from None
        raise BirdeyeError('Birdeye indisponível; análise incompleta.')
