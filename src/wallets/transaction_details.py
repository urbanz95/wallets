"""Evidência opcional por assinatura; nunca confunde taxas observadas com custo total.

Contrato Standard: GET /blockchain/v1/transaction/detail, 10 CU/pedido.
https://data.birdeye.so/docs/blockchain-api/transaction/get-blockchain-v1-transaction-detail
A API conhece apenas as assinaturas fornecidas: não enumera transações falhadas
omitidas do feed, não identifica aqui quem suporta economicamente a taxa e não
fornece uma conversão histórica de lamports para USD.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from .birdeye import BirdeyeError
from .models import parse_time

ENDPOINT = '/blockchain/v1/transaction/detail'


def _lamports(value):
    """Taxas do contrato são inteiros não negativos, nunca float arredondado."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not number.is_finite() or number < 0 or number != number.to_integral_value():
        return None
    return number


def enrich_details(client, events, end, max_signatures=2):
    """Consulta poucas assinaturas únicas até ao limite, aproveitando a cache cliente.

    Retorna só evidência observada, com subtotais em lamports e limitações.
    Nenhuma flag de cobertura contábil é promovida. O formato interno de ``status``
    não é fixado pelo contrato; conserva-se como evidência, sem inferir sucesso ou
    falha da ausência de campos. Não há envio Telegram nem alocação de taxas.
    """
    cutoff = parse_time(end)
    if (not isinstance(max_signatures, int) or isinstance(max_signatures, bool)
            or max_signatures < 0):
        raise ValueError('Limite de detalhes deve ser um inteiro não negativo.')
    result = {
        'source': 'birdeye',
        'endpoint': ENDPOINT,
        'details': [],
        'known_signatures': 0,
        'requested_signatures': 0,
        'capacity_limited': False,
        'rejected_future_events': 0,
        'rejected_details': 0,
        'coverage_complete': False,
        'identified_network_fees': {
            'currency': 'lamports',
            'total_lamports': None,
            'priority_subset_lamports': None,
            'transactions_with_known_fee': 0,
            'transactions_with_known_priority': 0,
            'wallet_fee_attribution': 'unknown',
            'usd_value': None,
            'allocated_to_accounting': False,
            'complete': False,
        },
        'limitations': [
            'Só assinaturas conhecidas: não enumera falhas omitidas pelo histórico de operações.',
            'Subtotal observado por transação; atribuição de taxas à carteira e custos totais não comprovados.',
            'Taxas em lamports sem conversão histórica USD nem alocação automática à contabilidade.',
        ],
    }
    signatures = []
    observed_times = {}
    seen = set()
    for event in events:
        try:
            stamp = parse_time(event.timestamp)
        except (TypeError, ValueError, AttributeError, OverflowError, OSError):
            result['limitations'].append('Evento sem data válida excluído do enriquecimento.')
            continue
        if stamp > cutoff:
            result['rejected_future_events'] += 1
            continue
        signature = getattr(event, 'tx_hash', None)
        if not isinstance(signature, str) or not signature.strip():
            continue
        observed_times.setdefault(signature, set()).add(int(stamp.timestamp()))
        if signature in seen:
            continue
        seen.add(signature)
        signatures.append(signature)
    result['known_signatures'] = len(signatures)
    result['capacity_limited'] = len(signatures) > max_signatures
    if result['capacity_limited']:
        result['limitations'].append('Limite de assinaturas por carteira: enriquecimento parcial.')
    for signature in signatures[:max_signatures]:
        result['requested_signatures'] += 1
        try:
            response = client.request('GET', ENDPOINT, {'signature': signature})
        except BirdeyeError as exc:
            # Mesmo um cliente injetado não deve poder expor cabeçalhos/segredos
            # através do texto remoto de uma exceção.
            result['limitations'].append(
                f'Detalhes interrompidos ({type(exc).__name__}); evidência incompleta.')
            break
        data = response.get('data') if isinstance(response, dict) else None
        if (not isinstance(data, dict) or response.get('success') is not True):
            result['rejected_details'] += 1
            result['limitations'].append('Detalhe Birdeye com formato inválido; não usado.')
            continue
        block_time = data.get('block_time')
        # Contrato block_time é inteiro. Sem data verificável, não atribuir a
        # evidência ao período da avaliação nem aceitar dados possivelmente futuros.
        if not isinstance(block_time, int) or isinstance(block_time, bool):
            result['rejected_details'] += 1
            result['limitations'].append('Detalhe sem block_time verificável; não usado.')
            continue
        try:
            detail_time = parse_time(block_time)
        except (TypeError, ValueError, OverflowError, OSError):
            detail_time = None
        if detail_time is None or detail_time > cutoff:
            result['rejected_details'] += 1
            result['limitations'].append('Detalhe com data inválida ou posterior ao corte; não usado.')
            continue
        if block_time not in observed_times[signature]:
            result['rejected_details'] += 1
            result['limitations'].append('Data do detalhe diverge dos eventos da assinatura; não usado.')
            continue
        reported_signatures = data.get('signatures')
        if (reported_signatures is not None
                and (not isinstance(reported_signatures, list)
                     or signature not in reported_signatures)):
            result['rejected_details'] += 1
            result['limitations'].append('Assinatura devolvida não corresponde ao pedido; detalhe não usado.')
            continue
        fee = _lamports(data.get('fee'))
        priority = _lamports(data.get('priority_fee'))
        if fee is not None and priority is not None and priority > fee:
            priority = None
            result['limitations'].append('Prioridade superior à taxa total: parcela de prioridade desconhecida.')
        status = data.get('status')
        detail = {
            'signature': signature,
            'block_time': detail_time,
            'slot': data.get('slot') if isinstance(data.get('slot'), int) else None,
            'fee_lamports': fee,
            'priority_fee_lamports': priority,
            'execution_status': 'unknown',
            'status_evidence': status if isinstance(status, dict) else None,
        }
        result['details'].append(detail)
        fees = result['identified_network_fees']
        if fee is not None:
            fees['total_lamports'] = (fees['total_lamports'] or Decimal(0)) + fee
            fees['transactions_with_known_fee'] += 1
        else:
            result['limitations'].append('Transação observada sem taxa total válida; custo permanece desconhecido.')
        if priority is not None:
            # Priority fee é uma parcela informativa: não a somar de novo ao total.
            fees['priority_subset_lamports'] = (fees['priority_subset_lamports'] or Decimal(0)) + priority
            fees['transactions_with_known_priority'] += 1
    result['limitations'] = list(dict.fromkeys(result['limitations']))
    return result
