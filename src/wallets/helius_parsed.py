"""Parsed Events Free, batches limitados, mesma reserva Helius e nenhuma mensagem remota."""
import json
from urllib.request import Request
from urllib.parse import urlencode
from urllib.error import HTTPError,URLError
from .helius import HOST,AccessDenied,InvalidResponse,HeliusError
from .models import dumps


def parse_transactions(client,signatures):
    signatures=list(signatures)
    if not signatures or len(signatures)>100 or len(set(signatures))!=len(signatures) or any(not isinstance(s,str) or not s or len(s)>128 for s in signatures):
        raise ValueError('Batch de assinaturas inválido')
    if client._halted or not client.api_key:raise AccessDenied('Helius indisponível')
    request=Request(HOST+'v1/parsed-events/transactions?'+urlencode({'api-key':client.api_key}),
        data=dumps({'transactions':signatures,'rawTransaction':'jsonParsed'}).encode(),
        headers={'Content-Type':'application/json','Accept':'application/json'},method='POST')
    client._reserve(10)
    try:
        result=client.transport(request)
        if not isinstance(result,list) or len(result)!=len(signatures):raise InvalidResponse('Parsed Events sem correspondência de resultados')
        for sig,item in zip(signatures,result):
            if not isinstance(item,dict) or item.get('signature')!=sig or item.get('parserStatus') not in ('OK','ERROR'):
                raise InvalidResponse('Identidade de Parsed Events incoerente')
        client.network_verified=True
        return result
    except HTTPError as exc:
        if exc.code in (400,401,402,403,404,429):client._halted=True
        raise HeliusError('Parsed Events indisponível (HTTP '+str(exc.code)+')') from None
    except (URLError,TimeoutError,OSError,json.JSONDecodeError,UnicodeDecodeError):
        raise HeliusError('Parsed Events sem resposta verificável') from None
