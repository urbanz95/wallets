"""Cliente Helius Free, só leitura, com orçamento conservador por tentativa.

Contrato verificado em docs/helius-capabilities.md. O endereço RPC é fixo e nunca
é aceite um URL fornecido pelo utilizador. Não há envio/assinatura de transações,
endpoints pagos nem repetição ilimitada de pedidos.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
import json
import math
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .models import dumps, utcnow

HOST = 'https://mainnet.helius-rpc.com/'
METHODS = frozenset({'getTransactionsForAddress'})
MAX_RESPONSE_BYTES = 32 * 1024 * 1024


class HeliusError(RuntimeError):
    """Mensagens locais; não incluem URL autenticado, cabeçalhos ou corpo remoto."""


class BudgetExceeded(HeliusError):
    pass


class AccessDenied(HeliusError):
    pass


class InvalidResponse(HeliusError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        raise AccessDenied('Redirecionamento Helius recusado para proteger a chave API.')


def request_cost(method, params):
    """Reserva máxima: resultados completos são cobrados em lotes de100.

    Reservar antes da rede é necessário mesmo quando há timeout ou o processo
    termina antes de guardar a resposta. Não há devolução automática de créditos.
    """
    if method not in METHODS:
        raise AccessDenied('Método fora da lista Helius gratuita e de leitura confirmada.')
    if (not isinstance(params, (list, tuple)) or not 1 <= len(params) <= 2
            or not isinstance(params[0], str) or not params[0]):
        raise ValueError('Parâmetros Helius inválidos.')
    options = params[1] if len(params) == 2 else {}
    if not isinstance(options, dict):
        raise ValueError('Configuração da consulta Helius inválida.')
    limit = options.get('limit', 1000)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError('Helius limit deve ser inteiro entre1 e1000.')
    detail = options.get('transactionDetails', 'signatures')
    if detail not in ('signatures', 'full'):
        raise ValueError('Helius transactionDetails deve ser signatures ou full.')
    if options.get('sortOrder', 'desc') not in ('asc', 'desc'):
        raise ValueError('Ordenação Helius inválida.')
    return 10 if detail == 'signatures' else max(10, 10 * math.ceil(limit / 100))


class HeliusClient:
    def __init__(self, api_key, store, config, *, transport=None, clock=utcnow, sleep=time.sleep):
        self.api_key, self.store, self.config = api_key, store, dict(config)
        self.transport = transport or self._http
        self.clock, self.sleep = clock, sleep
        self.requests, self.credits = 0, 0
        self.network_verified = False
        self._last_call = None
        self._halted = False
        self._validate_config()

    def _validate_config(self):
        self.config=validate_config(self.config)

    @staticmethod
    def _http(request):
        with build_opener(_NoRedirect()).open(request, timeout=30) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise InvalidResponse('Resposta Helius demasiado grande; análise incompleta.')
            return json.loads(raw, parse_float=Decimal)

    def _reserve(self, cost):
        if self.requests + 1 > self.config['max_requests_per_run']:
            raise BudgetExceeded('Limite de pedidos Helius por execução atingido; análise adiada.')
        if self.credits + cost > self.config['max_credits_per_run']:
            raise BudgetExceeded('Limite de créditos Helius por execução atingido; análise adiada.')
        if not self.store.reserve_budget(self.clock().strftime('%Y-%m'), cost,
                                         self.config['monthly_credit_ceiling'], namespace='helius'):
            raise BudgetExceeded('Limite mensal local Helius atingido; análise adiada.')
        self.requests += 1
        self.credits += cost
        interval = 1 / self.config['requests_per_second']
        if self._last_call is not None:
            self.sleep(max(0, interval - (self.clock() - self._last_call).total_seconds()))
        self._last_call = self.clock()

    @staticmethod
    def _validate_result(result, params):
        if not isinstance(result, dict) or not isinstance(result.get('data'), list):
            raise InvalidResponse('Resultado Helius sem lista de transações válida; cobertura não confirmada.')
        options = params[1] if len(params) == 2 else {}
        if len(result['data']) > options.get('limit', 1000):
            raise InvalidResponse('Helius devolveu mais registos do que o limite pedido; cobertura não confirmada.')
        if any(not isinstance(row, dict) for row in result['data']):
            raise InvalidResponse('Registo Helius malformado; cobertura não confirmada.')
        token = result.get('paginationToken')
        if token is not None and (not isinstance(token, str) or not token):
            raise InvalidResponse('Cursor Helius inválido; cobertura não confirmada.')
        return result

    def request(self, method, params, *, cache=True):
        if self._halted:
            raise AccessDenied('Cliente Helius interrompido após restrição de acesso/quota/contrato.')
        cost = request_cost(method, params)
        if not isinstance(self.api_key, str) or not self.api_key:
            raise AccessDenied('HELIUS_API_KEY não configurada; nenhuma consulta efetuada.')
        # Não incluir credenciais, mesmo em hashes ou chaves de cache persistentes.
        key = 'helius:' + sha256(dumps([method, params]).encode()).hexdigest()
        if cache:
            cached = self.store.cache_get(key, self.clock())
            if cached is not None:
                return self._validate_result(cached, params)
        payload = dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode()
        request = Request(HOST + '?' + urlencode({'api-key': self.api_key}), data=payload,
                          headers={'Accept': 'application/json', 'Content-Type': 'application/json'},
                          method='POST')
        for attempt in range(self.config['max_retries'] + 1):
            self._reserve(cost)
            try:
                response = self.transport(request)
                if (not isinstance(response, dict) or response.get('jsonrpc') != '2.0'
                        or response.get('id') != 1):
                    raise InvalidResponse('Resposta JSON-RPC Helius inválida; cobertura não confirmada.')
                if response.get('error') is not None:
                    # O conteúdo remoto pode conter dados de autenticação. Não o
                    # apresentar nem experimentar outros métodos após este erro.
                    self._halted = True
                    raise AccessDenied('Helius recusou a consulta RPC; acesso, quota ou parâmetros não confirmados.')
                if 'result' not in response:
                    raise InvalidResponse('Resposta Helius sem result; cobertura não confirmada.')
                result = self._validate_result(response['result'], params)
                self.network_verified = True
                if cache:
                    self.store.cache_put(key, result, self.clock() + timedelta(
                        seconds=self.config['cache_ttl_seconds']))
                return result
            except HTTPError as exc:
                status = exc.code
                if status in (400, 401, 402, 403, 404):
                    self._halted = True
                    raise AccessDenied(f'Helius recusou acesso/contrato ({status}); sem upgrade ou alternativa de fornecedor.') from None
                if status == 429 or status >= 500:
                    if attempt < self.config['max_retries']:
                        retry = exc.headers.get('Retry-After', '') if exc.headers else ''
                        try:
                            delay = float(retry)
                            if not math.isfinite(delay): delay = 2 ** attempt
                        except (TypeError, ValueError):
                            delay = 2 ** attempt
                        self.sleep(min(30, max(1, delay)))
                        continue
                if status == 429:
                    self._halted = True
                raise HeliusError(f'Falha HTTP Helius ({status}); análise incompleta.') from None
            except (URLError, TimeoutError, OSError, json.JSONDecodeError, UnicodeDecodeError):
                if attempt < self.config['max_retries']:
                    self.sleep(min(30, 2 ** attempt))
                    continue
                raise HeliusError('Falha de rede/resposta Helius; análise incompleta.') from None
        raise HeliusError('Helius indisponível; análise incompleta.')


def validate_config(config):
    """Validar limites sem chave, armazenamento ou consultas remotas."""
    validated=dict(config)
    limits = {
        'max_requests_per_run': (150, 1, 1000000),
        'max_credits_per_run': (5000, 1, 1000000),
        'monthly_credit_ceiling': (900000, 1, 1000000),
        'max_retries': (2, 0, 3),
        'cache_ttl_seconds': (900, 0, 3600),
    }
    for key, (default, low, high) in limits.items():
        value = validated.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError('Limite de configuração Helius inválido.')
        validated[key] = value
    if validated['max_credits_per_run'] > validated['monthly_credit_ceiling']:
        raise ValueError('Orçamento Helius por execução superior ao teto mensal.')
    rate = validated.get('requests_per_second', 5)
    if (isinstance(rate, bool) or not isinstance(rate, (int, float))
            or not math.isfinite(rate) or not 0 < rate <= 5):
        raise ValueError('Limite Helius deve estar entre 0 e 5 pedidos por segundo.')
    validated['requests_per_second'] = rate
    return validated
