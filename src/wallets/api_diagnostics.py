"""Diagnóstico privado: guardar apenas no cofre cifrado.

O resultado NUNCA deve ir para logs, relatórios públicos ou Telegram. Mesmo após
redação de credenciais, uma mensagem do fornecedor pode conter dados privados.
Este módulo não faz pedidos de rede nem imprime conteúdo.
"""
from __future__ import annotations

import base64
import json
import math
import re
import unicodedata
from urllib.parse import quote, quote_plus

MAX_BODY_BYTES = 16 * 1024
MAX_MESSAGE_CHARS = 2048
REDACTED = '[REDIGIDO]'
_URL = re.compile(r'https?://[^\s<>"\']+', re.IGNORECASE)
_AUTH = re.compile(
    r'\b(?:authorization|proxy-authorization|x-api-key|api[-_ ]?key|cookie|set-cookie)'
    r'["\']?\s*[:=]\s*[^\r\n]*', re.IGNORECASE,
)
_BEARER = re.compile(r'\b(?:bearer|basic)\s+[a-z0-9._~+/%=\-]+', re.IGNORECASE)
_CODE = re.compile(r'[a-zA-Z0-9_.:\-]{1,64}')


def _secret_variants(api_key):
    raw = api_key.encode('utf-8')
    encoded = {
        api_key,
        base64.b64encode(raw).decode('ascii'),
        base64.urlsafe_b64encode(raw).decode('ascii'),
    }
    encoded.update(value.rstrip('=') for value in tuple(encoded))
    encoded.update(quote(value, safe='') for value in tuple(encoded))
    encoded.update(quote_plus(value, safe='') for value in tuple(encoded))
    return sorted((value for value in encoded if value), key=len, reverse=True)


def _redact(value, variants):
    # Remover controlos primeiro impede que um carácter invisível fragmente a
    # chave e volte a expô-la depois de limpar a apresentação.
    value = ''.join(ch for ch in value if unicodedata.category(ch) not in ('Cc', 'Cf'))
    value = value.replace('\\/', '/')
    for variant in variants:
        value = re.sub(re.escape(variant), REDACTED, value, flags=re.IGNORECASE)
    value = _URL.sub(REDACTED, value)
    value = _AUTH.sub(REDACTED, value)
    return _BEARER.sub(REDACTED, value)


def extract_private_diagnostic(raw: bytes, api_key: str) -> dict:
    """Extrair só message/error.code; resultado PRIVADO, nunca para logs/envios.

    Rejeita corpos maiores que 16 KiB e formatos inesperados. Ignora headers,
    request, input e data. Não inclui a resposta HTTP completa nem a chave.
    """
    if (not isinstance(raw, bytes) or len(raw) > MAX_BODY_BYTES
            or not isinstance(api_key, str) or not api_key):
        return {}
    try:
        body = json.loads(raw)
        if not isinstance(body, dict) or not isinstance(body.get('message'), str):
            return {}
        variants = _secret_variants(api_key)
        message = _redact(body['message'], variants)[:MAX_MESSAGE_CHARS]
        if not message.strip():
            return {}
        result = {'message': message}
        error = body.get('error')
        if isinstance(error, dict):
            code = error.get('code')
            if isinstance(code, str):
                safe_code = _redact(code, variants)
                if safe_code == code and _CODE.fullmatch(safe_code):
                    result['error'] = {'code': safe_code}
            elif type(code) in (int, float):
                if abs(code) <= 10 ** 12 and math.isfinite(code):
                    result['error'] = {'code': code}
        return result
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        return {}
