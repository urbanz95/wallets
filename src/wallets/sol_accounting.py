"""FIFO em SOL para swaps cuja contrapartida é demonstrada na transação.

Não é uma conversão USD nem uma estimativa pelo preço atual. Rendas recuperáveis
são reconciliadas separadamente; transferências, rotas ambíguas e quote assets
não SOL mantêm custo desconhecido. Os tokens abertos conhecidos entram no pior
caso como perda do custo restante. Receipts normalizados permitem repetir o FIFO
sem conservar o payload RPC inteiro.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from datetime import timedelta
from decimal import Decimal, InvalidOperation, localcontext
from hashlib import sha256
from statistics import median

from .analysis import SETTLEMENT_TOKENS
from .models import SOL, dumps, iso, parse_time
from .helius_normalization import (
    normalize_transaction, _account_keys, _balance_records, _instructions,
    _parsed, _integer, _quantity, _trade_interference, AUXILIARY_PROGRAMS,
    SYSTEM_PROGRAM, TOKEN_PROGRAMS, ATA_PROGRAM,
)

ZERO = Decimal(0)
LAMPORTS = Decimal(1000000000)
SCHEMA = 'sol-receipt-v1'
LIMITATIONS = [
    'USD e conversão histórica SOL/USD: NÃO VERIFICADO.',
    'Resultado em SOL do universo observado; inventário inicial global e cobertura de todos os protocolos: NÃO VERIFICADO.',
    'Marca de posições abertas não demonstra liquidez, impacto no preço ou preço reproduzível de saída: NÃO VERIFICADO.',
    'Pior caso de posições abertas: perda do custo restante; não é uma cotação de mercado zero.',
]


def _decimal(value, *, positive=False):
    if isinstance(value, (bool, float)):
        raise ValueError('Valores SOL/quantidades exigem Decimal ou representação exata')
    try:
        value = value if isinstance(value, Decimal) else Decimal(value)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError('Valor monetário inválido') from exc
    if not value.is_finite() or value < ZERO or (positive and value <= ZERO):
        raise ValueError('Valor negativo, nulo ou não finito')
    return value


def _system_parsed(instruction):
    kind, info = _parsed(instruction)
    if kind:
        return kind, info
    data = instruction.get('data')
    accounts = instruction.get('accounts', [])
    if isinstance(data, bytes) and len(data) >= 4:
        opcode = int.from_bytes(data[:4], 'little')
        if opcode == 2 and len(data) == 12 and len(accounts) >= 2:
            return 'transfer', {'source': accounts[0], 'destination': accounts[1],
                                'lamports': int.from_bytes(data[4:12], 'little')}
        if opcode == 0 and len(data) == 52 and len(accounts) >= 2:
            return 'createaccount', {'source': accounts[0], 'newAccount': accounts[1],
                                     'lamports': int.from_bytes(data[4:12], 'little')}
    return '', {}


def _sol_counterparty(row, wallet, parsed, swap):
    """Demonstrar o fluxo pago/recebido, excluindo só renda atribuída com prova."""
    evidence = parsed['evidence']
    reasons = []
    if swap.get('quote_mint') != SOL:
        return None, {}, ['O swap não tem SOL/wSOL como ativo de contrapartida.']
    meta = row['meta']; message = row['transaction']['message']
    keys = _account_keys(message, meta)
    accounts, deltas, decimals, complete = _balance_records(meta, keys, wallet, [])
    instructions, instruction_complete = _instructions(message, meta, keys)
    native = evidence.get('native_delta_excluding_identified_fee_lamports')
    if not complete or not instruction_complete or not isinstance(native, int) or isinstance(native, bool):
        return None, {}, ['Saldos, instruções ou taxa SOL insuficientes para reconciliar a contrapartida.']
    if SOL in decimals and decimals[SOL] != 9:
        return None, {}, ['Decimais wSOL incompatíveis com lamports.']
    root_calls = [i for i in instructions if i.get('program') == swap.get('program')
                  and i.get('index') == swap.get('instruction_index')
                  and i.get('top_level') == swap.get('top_level')]
    if not root_calls:
        return None, {}, ['Não foi encontrada a chamada de swap reconhecida.']
    protocol_accounts = set(root_calls[0]['accounts'])
    owned = {account: state for account, state in accounts.items() if state['owner'] == wallet}
    creation_payers = {}; closures = {}
    for instruction in instructions:
        if instruction['program'] == ATA_PROGRAM:
            kind, info = _parsed(instruction)
            account = info.get('account') or info.get('associatedAccount')
            payer = info.get('source') or info.get('payer') or info.get('funder')
            if kind in ('create', 'createidempotent') and account in owned and isinstance(payer, str):
                creation_payers[account] = payer
        elif instruction['program'] in TOKEN_PROGRAMS:
            kind, info = _parsed(instruction)
            if kind == 'closeaccount' and info.get('account') in owned:
                closures[info['account']] = info.get('destination')
        elif instruction['program'] == SYSTEM_PROGRAM:
            kind, info = _system_parsed(instruction)
            if kind in ('createaccount', 'createaccountwithseed'):
                account = info.get('newAccount') or info.get('newAccountPubkey')
                payer = info.get('source') or info.get('from')
                amount = _integer(info.get('lamports'))
                if account in owned and isinstance(payer, str) and amount is not None:
                    creation_payers[account] = payer
                elif payer == wallet:
                    reasons.append('Criação de conta com SOL da carteira fora das contas próprias reconciliadas.')
            elif kind in ('transfer', 'transferwithseed'):
                source, destination = info.get('source'), info.get('destination')
                amount = _integer(info.get('lamports'))
                if source != wallet and destination != wallet:
                    continue
                counterpart = destination if source == wallet else source
                internal = counterpart in owned
                protocol_flow = (not instruction['top_level']
                                 and instruction['index'] == swap['instruction_index']
                                 and counterpart in protocol_accounts)
                if amount is None or not (internal or protocol_flow):
                    reasons.append('Transferência SOL externa ou tip não atribuível exclusivamente ao swap.')
            elif not kind:
                reasons.append('Instrução System sem interpretação suficiente para excluir fluxos adicionais.')
    pre, post = meta.get('preBalances'), meta.get('postBalances')
    if not isinstance(pre, list) or not isinstance(post, list) or len(pre) != len(keys) or len(post) != len(keys):
        return None, {}, ['Saldos nativos incompletos.']
    rent_delta = 0; sponsored_rent = 0
    for account, state in owned.items():
        index = keys.index(account)
        before, after = pre[index], post[index]
        if _integer(before) is None or _integer(after) is None:
            reasons.append('Saldo de renda de conta de token desconhecido.')
            continue
        # Em wSOL os lamports contêm principal + reserva de renda. Nos restantes
        # SPL só o saldo lamports da conta é capital de renda recuperável.
        before_rent = before - (state['before_raw'] if state['mint'] == SOL else 0)
        after_rent = after - (state['after_raw'] if state['mint'] == SOL else 0)
        if before_rent < 0 or after_rent < 0:
            reasons.append('Saldo wSOL superior aos lamports da conta; representação incoerente.')
            continue
        delta = after_rent - before_rent
        if not delta:
            continue
        if before == 0 and after > 0 and delta > 0:
            payer = creation_payers.get(account)
            if payer == wallet:
                rent_delta += delta
            elif isinstance(payer, str):
                sponsored_rent += delta
            else:
                reasons.append('Renda de nova conta sem pagador demonstrado.')
        elif after == 0 and before > 0 and delta < 0:
            if closures.get(account) == wallet:
                rent_delta += delta
            else:
                reasons.append('Renda de conta fechada sem reembolso demonstrado à carteira.')
        else:
            reasons.append('Mudança de lamports de conta existente não reconciliada como renda.')
    wrapped = deltas.get(SOL, 0)
    combined = (Decimal(native) + Decimal(wrapped) + Decimal(rent_delta)) / LAMPORTS
    expected = 1 if swap['kind'] == 'buy' else -1
    if combined * expected >= ZERO:
        reasons.append('O fluxo líquido SOL não tem direção/quantidade coerente com o swap.')
    components = {'native_excluding_network_fee_sol': Decimal(native)/LAMPORTS,
                  'wsol_delta_sol': Decimal(wrapped)/LAMPORTS,
                  'wallet_rent_adjustment_sol': Decimal(rent_delta)/LAMPORTS,
                  'sponsored_rent_sol': Decimal(sponsored_rent)/LAMPORTS}
    return (None if reasons else abs(combined)), components, list(dict.fromkeys(reasons))


def _correlated_swap(row, wallet, parsed, birdeye_events):
    """Corroborar só uma troca simples, com as duas pernas históricas Birdeye.

    A atribuição não depende de preços USD, nomes de DEX ou do fee payer. Rotas
    múltiplas, saldos divergentes e transferências adicionais ficam desconhecidos.
    """
    proof = parsed['evidence']
    matches = [e for e in birdeye_events if e.source == 'birdeye'
               and e.wallet == wallet and e.tx_hash == proof['signature']]
    if (len(matches) != 2 or any(e.multi_step or e.timestamp != proof['block_time']
            or e.kind not in ('buy', 'sell') for e in matches)):
        return None
    base = [e for e in matches if e.token not in SETTLEMENT_TOKENS]
    quote = [e for e in matches if e.token == SOL]
    if len(base) != 1 or len(quote) != 1 or base[0].kind == quote[0].kind:
        return None
    event, sol_event = base[0], quote[0]
    keys = _account_keys(row['transaction']['message'], row['meta'])
    accounts, deltas, decimals, complete = _balance_records(row['meta'], keys, wallet, [])
    instructions, decoded = _instructions(row['transaction']['message'], row['meta'], keys)
    if not complete or not decoded or any(v and mint not in (event.token, SOL) for mint, v in deltas.items()):
        return None
    raw = deltas.get(event.token, 0)
    if (not raw or (raw > 0) != (event.kind == 'buy')
            or _quantity(abs(raw), decimals[event.token]) != event.quantity):
        return None
    states = [a for a in accounts.values() if a['owner'] == wallet and a['mint'] == event.token and a['delta_raw']]
    if len(states) != 1 or states[0]['delta_raw'] != raw:
        return None
    # O normalizador Birdeye só produz buy/sell com índices explícitos. Ambas
    # as pernas têm de identificar o mesmo índice raiz da transação RPC.
    try:
        prefixes = [e.id.removeprefix(proof['signature']+':').split(':') for e in matches]
        root_index = int(prefixes[0][0])
        if any(int(value[0]) != root_index for value in prefixes):
            return None
    except (ValueError, IndexError):
        return None
    calls = [i for i in instructions if i['top_level'] and i['program'] not in AUXILIARY_PROGRAMS]
    if len(calls) != 1 or calls[0]['index'] != root_index:
        return None
    call = calls[0]
    swap = {'program':call['program'], 'instruction_index':root_index, 'top_level':True,
            'mint':event.token, 'quote_mint':SOL, 'kind':event.kind,
            'base_account':states[0]['account']}
    if _trade_interference(instructions, swap):
        return None
    amount, components, reasons = _sol_counterparty(row, wallet, parsed, swap)
    if reasons or amount is None or amount != sol_event.quantity:
        return None
    return event, amount, components, matches


def normalize_sol_receipt(row, wallet, *, birdeye_events=()):
    """Normalizar uma transação real para prova mínima e pernas contabilizáveis."""
    if isinstance(row, dict) and row.get('schema') == SCHEMA:
        return _validate_receipt(row, wallet)
    parsed = normalize_transaction(row, wallet)
    evidence = parsed['evidence']; signature = evidence['signature']
    fee_known = evidence.get('fee_lamports')
    attributed = evidence.get('fee_attributed_to_wallet') is True
    fee = (Decimal(fee_known)/LAMPORTS if isinstance(fee_known, int) and attributed else
           ZERO if not attributed else None)
    receipt = {'schema': SCHEMA, 'wallet': wallet, 'signature': signature,
               'timestamp': evidence['block_time'], 'slot': evidence.get('slot'),
               'transaction_index': evidence.get('transaction_index'),
               'raw_hash': evidence['raw_hash'],
               'instruction_hash': sha256(dumps({'top':row['transaction']['message'].get('instructions'),
                   'inner':row['meta'].get('innerInstructions')}).encode()).hexdigest(),
               'programs': evidence.get('programs', []),
               'status': 'unknown', 'fee_sol': fee, 'fee_attributed_to_wallet': attributed,
               'sol_quote_verified': False, 'classification_complete': False,
               'legs': [], 'reasons': [], 'components': {}}
    if evidence.get('execution_status') == 'failed':
        receipt['status'] = 'failed'; receipt['classification_complete'] = True
        return receipt
    token_accounts, _, _, _ = _balance_records(row['meta'],
        _account_keys(row['transaction']['message'],row['meta']),wallet,[])
    token_balances = {}
    for state in token_accounts.values():
        if state['owner'] != wallet:
            continue
        record = token_balances.setdefault(state['mint'],[ZERO,ZERO])
        record[0] += _quantity(state['before_raw'],state['decimals'])
        record[1] += _quantity(state['after_raw'],state['decimals'])
    recognized = evidence.get('recognized_swaps', [])
    if len(recognized) == 1:
        swap = recognized[0]
        cost, components, reasons = _sol_counterparty(row, wallet, parsed, swap)
        events = [e for e in parsed['events'] if e.token == swap['mint'] and e.kind == swap['kind']]
        if len(events) == 1 and cost is not None and fee is not None:
            receipt.update(status='trade', sol_quote_verified=True, classification_complete=True,
                           components=components)
            receipt['legs'] = [{'token': events[0].token, 'kind': events[0].kind,
                                'quantity': events[0].quantity, 'value_sol': cost,
                                'fee_sol': fee, 'reason': '',
                                'balance_before':token_balances[events[0].token][0],
                                'balance_after':token_balances[events[0].token][1]}]
            return receipt
        receipt['reasons'].extend(reasons or ['Taxas ou perna de swap não reconciliadas.'])
    if not recognized and evidence.get('execution_status') == 'succeeded' and fee is not None:
        correlated = _correlated_swap(row, wallet, parsed, birdeye_events)
        if correlated is not None:
            event, cost, components, matches = correlated
            receipt.update(status='trade', sol_quote_verified=True, classification_complete=True,
                           components=components, classification='birdeye_rpc_correlated_single_swap',
                           birdeye_proof=[{'id':e.id,'token':e.token,'kind':e.kind,
                               'quantity':e.quantity,'timestamp':e.timestamp,'source':e.source}
                               for e in matches])
            receipt['legs'] = [{'token':event.token,'kind':event.kind,'quantity':event.quantity,
                'value_sol':cost,'fee_sol':fee,'reason':'',
                'balance_before':token_balances[event.token][0],
                'balance_after':token_balances[event.token][1]}]
            return receipt
    nonsettlement = [e for e in parsed['events'] if e.token not in SETTLEMENT_TOKENS]
    if nonsettlement:
        for event in nonsettlement:
            kind = event.kind if event.kind in ('transfer_in', 'transfer_out', 'airdrop', 'liquidity') else 'unknown'
            receipt['legs'].append({'token': event.token, 'kind': kind, 'quantity': event.quantity,
                                    'value_sol': None, 'fee_sol': ZERO,
                                    'reason': 'Aquisição, contrapartida SOL ou interpretação não demonstrada.',
                                    'balance_before':token_balances.get(event.token,[None,None])[0],
                                    'balance_after':token_balances.get(event.token,[None,None])[1]})
        receipt['status'] = 'transfer' if all(leg['kind'] in ('transfer_in', 'transfer_out', 'airdrop') for leg in receipt['legs']) else 'unknown'
        receipt['classification_complete'] = receipt['status'] == 'transfer'
    elif parsed['events'] and all(e.kind in ('transfer_in','transfer_out') for e in parsed['events']):
        receipt.update(status='transfer',classification_complete=True)
        receipt['reasons'].append('Transferência de capital SOL/stablecoin; não é resultado de trading.')
    elif any(e.kind == 'wrap' for e in parsed['events']):
        receipt.update(status='wrap', classification_complete=True)
    else:
        receipt['reasons'].extend(parsed.get('limitations', []))
    if receipt['status']=='unknown' and not receipt['reasons']:
        receipt['reasons'].append('Operação sem contrapartida SOL demonstrada; não convertida em resultado.')
    receipt['reasons'] = list(dict.fromkeys(receipt['reasons']))
    return receipt


def _validate_receipt(row, wallet):
    if row.get('wallet') != wallet or not isinstance(row.get('signature'), str) or not row['signature']:
        raise ValueError('Receipt com identidade/carteira inválida')
    stamp = parse_time(row.get('timestamp'))
    if row.get('status') not in ('trade', 'failed', 'wrap', 'transfer', 'unknown', 'invalid', 'conflict'):
        raise ValueError('Estado de receipt inválido')
    if not isinstance(row.get('legs'), list) or not isinstance(row.get('reasons', []), list):
        raise ValueError('Pernas/razões do receipt inválidas')
    fee = None if row.get('fee_sol') is None else _decimal(row['fee_sol'])
    legs = []
    for leg in row['legs']:
        if not isinstance(leg, dict) or not isinstance(leg.get('token'), str) or not leg['token']:
            raise ValueError('Perna de receipt sem token')
        if leg.get('kind') not in ('buy', 'sell', 'transfer_in', 'transfer_out', 'airdrop', 'liquidity', 'unknown'):
            raise ValueError('Tipo de perna inválido')
        quantity = _decimal(leg.get('quantity'))
        value = None if leg.get('value_sol') is None else _decimal(leg['value_sol'])
        leg_fee = _decimal(leg.get('fee_sol', 0))
        if leg['kind'] in ('buy', 'sell') and (value is None or quantity <= ZERO or row.get('sol_quote_verified') is not True):
            raise ValueError('Trade SOL sem contrapartida demonstrada')
        before = None if leg.get('balance_before') is None else _decimal(leg['balance_before'])
        after = None if leg.get('balance_after') is None else _decimal(leg['balance_after'])
        if leg['kind'] in ('buy','sell') and (before is None or after is None):
            raise ValueError('Trade SOL sem saldos que demonstrem o inventário observado')
        if leg['kind']=='buy' and after-before != quantity or leg['kind']=='sell' and before-after != quantity:
            raise ValueError('Quantidade da perna diverge dos saldos do receipt')
        legs.append(dict(leg, quantity=quantity, value_sol=value, fee_sol=leg_fee,
                         balance_before=before,balance_after=after))
    if sum((leg['fee_sol'] for leg in legs if leg['kind'] in ('buy', 'sell')), ZERO) > (fee or ZERO):
        raise ValueError('Taxa atribuída mais de uma vez')
    return dict(row, timestamp=stamp, fee_sol=fee, legs=legs)


def _unknown_receipt(row, wallet, reason):
    identity = sha256(dumps(row).encode()).hexdigest()
    try:
        signature = row['transaction']['signatures'][0]
    except (KeyError, IndexError, TypeError):
        signature = 'invalid:'+identity
    try:
        stamp = parse_time(row.get('blockTime', row.get('timestamp')))
    except (ValueError, TypeError, OverflowError):
        stamp = None
    return {'schema': SCHEMA, 'wallet': wallet, 'signature': signature, 'timestamp': stamp,
            'slot': row.get('slot') if isinstance(row, dict) else None,
            'transaction_index': None, 'raw_hash': identity, 'programs': [],
            'status': 'invalid', 'fee_sol': None, 'fee_attributed_to_wallet': False,
            'classification_complete': False, 'sol_quote_verified': False,
            'legs': [], 'reasons': [reason], 'components': {}}


def _consume(lots, quantity):
    remaining = quantity; cost = ZERO; unknown = False; holds = []
    while remaining > ZERO and lots:
        lot = lots[0]; take = min(remaining, lot['quantity'])
        basis = None if lot['cost'] is None else lot['cost']*take/lot['quantity']
        if basis is None:
            unknown = True
        else:
            cost += basis; lot['cost'] -= basis
        holds.append((take, lot['timestamp']))
        lot['quantity'] -= take; remaining -= take
        if lot['quantity'] == ZERO:
            lots.popleft()
    return cost, unknown or remaining > ZERO, holds


def analyze_sol(transactions, wallet, start, end, discovery_tokens=(), marks_sol=None, *, birdeye_events=()):
    """Resultados em SOL do universo observado, incluindo abertos no pior caso.

    ``worst_case_sol`` é um subtotal dos tokens de custo conhecido, após taxas
    identificadas fora desses swaps. ``all_observed_tokens_worst_case_sol`` só é
    conhecido se nenhum token/transação estiver por interpretar. Não certifica
    inventário global nem resultado de toda a carteira.
    """
    start, end = parse_time(start), parse_time(end)
    if start >= end or end-start > timedelta(days=366):
        raise ValueError('Janela contabilística SOL inválida')
    with localcontext() as context:
        context.prec = 80
        return _analyze(transactions, wallet, start, end, discovery_tokens, marks_sol or {}, birdeye_events)


def _analyze(transactions, wallet, start, end, discovery_tokens, marks_sol, birdeye_events):
    unique = {}; invalid = outside = duplicate = 0
    for row in transactions:
        try:
            receipt = normalize_sol_receipt(row, wallet, birdeye_events=birdeye_events)
        except (ValueError, TypeError, KeyError, ArithmeticError, OverflowError):
            receipt = _unknown_receipt(row, wallet, 'Transação sem normalização SOL verificável.')
        stamp = receipt['timestamp']
        if stamp is None or not start <= stamp < end:
            outside += stamp is not None; invalid += 1
            # Não usar pernas de outra janela nem informação futura.
            receipt = dict(receipt, status='invalid', legs=[], classification_complete=False,
                           reasons=receipt['reasons']+['Transação fora da janela ou sem data verificável.'])
        signature = receipt['signature']
        previous = unique.get(signature)
        if previous is not None:
            duplicate += 1
            if previous['raw_hash'] != receipt['raw_hash'] or dumps(previous) != dumps(receipt):
                invalid += 1
                tokens = {leg['token'] for value in (previous, receipt) for leg in value['legs']}
                receipt = dict(previous, status='conflict', classification_complete=False,
                    legs=[{'token': token, 'kind': 'unknown', 'quantity': ZERO,
                           'value_sol': None, 'fee_sol': ZERO,
                           'reason': 'Assinatura duplicada com conteúdo divergente.'} for token in sorted(tokens)],
                    reasons=['Assinatura duplicada com conteúdo divergente.'], fee_sol=None)
            else:
                continue
        unique[signature] = receipt
    receipts = sorted(unique.values(), key=lambda r:(r['timestamp'] or start,
        r.get('slot') if isinstance(r.get('slot'), int) else -1,
        r.get('transaction_index') if isinstance(r.get('transaction_index'), int) else -1,r['signature']))
    days = max(1, int((end-start).total_seconds()//86400)+(1 if (end-start).total_seconds()%86400 else 0))
    daily = {iso(start+timedelta(days=i)):ZERO for i in range(days)}
    def bucket(stamp):
        return iso(start+timedelta(days=min(days-1,max(0,int((stamp-start).total_seconds()//86400)))))
    grouped = defaultdict(list); known_fees = ZERO; unallocated_fees = ZERO
    fees_complete = True; trades = []
    for receipt in receipts:
        stamp = receipt['timestamp']
        in_window = stamp is not None and start <= stamp < end and receipt['status'] not in ('invalid','conflict')
        if not in_window:
            continue
        if receipt['fee_sol'] is None:
            fees_complete = False
        else:
            known_fees += receipt['fee_sol']
        assigned = ZERO
        for leg in receipt['legs']:
            if leg['token'] in SETTLEMENT_TOKENS:
                continue
            grouped[leg['token']].append((receipt, leg))
            if leg['kind'] in ('buy','sell'):
                trades.append((receipt, leg));assigned += leg['fee_sol']
        overhead = max(ZERO,(receipt['fee_sol'] or ZERO)-assigned)
        unallocated_fees += overhead;daily[bucket(stamp)] -= overhead
    token_rows = []; outcomes = {}; returns = []; all_holds = []; entries = []
    for token, operations in sorted(grouped.items()):
        lots = deque(); reasons = []; realized = ZERO; bought_cost = ZERO; sold_cost = ZERO
        token_daily = defaultdict(lambda:ZERO); token_holds = []; token_entries = []
        # Slots e índices permitem ordenar operações no mesmo segundo. Se a
        # ordem económica continua ambígua, não usar ordem lexical como prova.
        positions = defaultdict(list)
        for receipt, leg in operations:
            if leg['kind'] in ('buy','sell'):
                positions[(receipt['timestamp'],receipt.get('slot'))].append(receipt)
        for same in positions.values():
            if len(same)>1 and any(r.get('transaction_index') is None for r in same):
                reasons.append('Operações no mesmo bloco sem índice suficiente para ordenar o FIFO.')
        for receipt, leg in operations:
            quantity = leg['quantity']; stamp = receipt['timestamp'];kind = leg['kind']
            before = leg.get('balance_before')
            tracked = sum((lot['quantity'] for lot in lots),ZERO)
            if before is not None and before != tracked:
                reasons.append('Inventário inicial ou movimento intermédio não demonstrado; custo desconhecido.')
                if before > tracked:
                    lots.append({'quantity':before-tracked,'cost':None,'timestamp':start})
                else:
                    _consume(lots,tracked-before)
            if kind == 'buy':
                if leg['value_sol'] is None or quantity <= ZERO:
                    reasons.append('Compra sem contrapartida SOL conhecida.')
                    lots.append({'quantity':quantity,'cost':None,'timestamp':stamp})
                    continue
                cost = leg['value_sol']+leg['fee_sol'];bought_cost += cost
                token_entries.append(cost);lots.append({'quantity':quantity,'cost':cost,'timestamp':stamp})
            elif kind == 'sell':
                cost, missing, held = _consume(lots,quantity)
                if missing or leg['value_sol'] is None:
                    reasons.append('Venda com aquisição anterior ou custo SOL desconhecido.')
                    continue
                outcome = leg['value_sol']-leg['fee_sol']-cost
                realized += outcome;sold_cost += cost;token_daily[bucket(stamp)] += outcome
                token_holds.extend((stamp-acquired).total_seconds() for _,acquired in held)
            elif kind in ('transfer_in','airdrop'):
                reasons.append('Transferência/distribuição recebida com custo desconhecido; não é compra gratuita.')
                if quantity>ZERO:lots.append({'quantity':quantity,'cost':None,'timestamp':stamp})
            elif kind == 'transfer_out':
                _consume(lots,quantity);reasons.append('Transferência enviada não é venda; fluxo de aquisição/saída por reconciliar.')
            else:
                reasons.append(leg.get('reason') or 'Operação não interpretada em SOL.')
        remaining = sum((lot['quantity'] for lot in lots),ZERO)
        remaining_cost = None if any(lot['cost'] is None for lot in lots) else sum((lot['cost'] for lot in lots),ZERO)
        known = not reasons and bought_cost>ZERO and remaining_cost is not None
        worst = realized-remaining_cost if known else None
        marked = None; mark_value = None; mark_stamp = None
        mark = marks_sol.get(token)
        if isinstance(mark,dict) and known and remaining>ZERO:
            try:
                mark_stamp = parse_time(mark['timestamp']);price=_decimal(mark['price_sol'])
                if max(start,end-timedelta(hours=24)) <= mark_stamp <= end:
                    mark_value=remaining*price;marked=realized+mark_value-remaining_cost
            except (ValueError,TypeError,KeyError,ArithmeticError):
                pass
        if known:
            outcomes[token]=worst;returns.append(worst/bought_cost)
            token_daily[iso(start+timedelta(days=days-1))] -= remaining_cost
            for key,value in token_daily.items():daily[key] += value
            all_holds.extend(token_holds);entries.extend(token_entries)
        token_rows.append({'token':token,'known_cost':known,'unknown_reason':' | '.join(dict.fromkeys(reasons)),
            'closed':known and remaining==ZERO,'open_quantity':remaining,
            'realized_sol':realized if known else None,'identified_realized_sol':realized,
            'remaining_cost_sol':remaining_cost if known else None,'acquisition_cost_sol':bought_cost,
            'worst_case_result_sol':worst,'marked_result_sol':marked,'marked_value_sol':mark_value,
            'mark_timestamp':mark_stamp if marked is not None else None,'mark_executable_verified':False,
            'holding_seconds':token_holds,'entries':token_entries,'daily':dict(token_daily) if known else {},
            'result_definition':'Realizado líquido das taxas atribuídas menos custo restante no pior caso; marca é cenário separado.'})
    known = len(outcomes); unknown = len(token_rows)-known
    values=list(outcomes.values());positive=sum((v for v in values if v>ZERO),ZERO)
    token_losses=abs(sum((v for v in values if v<ZERO),ZERO))
    loss_expense=token_losses+unallocated_fees
    total=sum(values,ZERO)-unallocated_fees if known else None
    order=sorted(outcomes,key=lambda token:(-outcomes[token],token))
    no_losses=positive>ZERO and loss_expense==ZERO
    unknown_transactions=sum(r['classification_complete'] is not True for r in receipts)
    global_known=bool(token_rows) and unknown==0 and invalid==0 and unknown_transactions==0
    duration_days=Decimal(str((end-start).total_seconds()))/Decimal(86400)
    active={bucket(r['timestamp']) for r,leg in trades}
    rapid=sum(value<=60 for value in all_holds)
    metrics={
        'currency':'SOL','tokens_count':len(token_rows),'known_tokens_count':known,'unknown_tokens_count':unknown,
        'closed_tokens_count':sum(row['closed'] for row in token_rows),
        'open_tokens_count':sum(row['known_cost'] and not row['closed'] for row in token_rows),
        'unknown_cost_fraction':Decimal(unknown)/Decimal(len(token_rows)) if token_rows else None,
        'realized_sol':sum((row['realized_sol'] for row in token_rows if row['known_cost']),ZERO)-unallocated_fees if known else None,
        'identified_realized_sol':sum((row['identified_realized_sol'] for row in token_rows),ZERO)-unallocated_fees,
        'worst_case_sol':total,'identified_worst_case_sol':total,
        'all_observed_tokens_worst_case_sol':total if global_known else None,
        'open_cost_sol':sum((row['remaining_cost_sol'] for row in token_rows if row['known_cost']),ZERO) if known else None,
        'known_fees_sol':known_fees,'unallocated_fees_sol':unallocated_fees,'fees_complete':fees_complete,
        'win_rate':Decimal(sum(v>ZERO for v in values))/Decimal(known) if known else None,
        'profit_factor':positive/loss_expense if loss_expense>ZERO and known else None,
        'token_profit_factor':positive/token_losses if token_losses>ZERO and known else None,
        'no_observed_losses':no_losses,'no_realized_losses':no_losses,'positive_results_sum':positive if known else None,
        'median_return':median(returns) if returns else None,
        'average_gain_sol':sum((v for v in values if v>ZERO),ZERO)/sum(v>ZERO for v in values) if any(v>ZERO for v in values) else None,
        'average_loss_sol':sum((v for v in values if v<ZERO),ZERO)/sum(v<ZERO for v in values) if any(v<ZERO for v in values) else None,
        'top1_share':max(outcomes[order[0]],ZERO)/positive if positive and order else None,
        'top3_share':sum((max(outcomes[t],ZERO) for t in order[:3]),ZERO)/positive if positive else None,
        'best_token':order[0] if order else None,'top3_tokens':order[:3],
        'without_discovery_sol':sum((v for token,v in outcomes.items() if token not in set(discovery_tokens)),ZERO)-unallocated_fees if known else None,
        'without_best_sol':sum((v for token,v in outcomes.items() if token not in order[:1]),ZERO)-unallocated_fees if known else None,
        'without_top3_sol':sum((v for token,v in outcomes.items() if token not in order[:3]),ZERO)-unallocated_fees if known else None,
        'activity_days':len(active),'profitable_days':sum(v>ZERO for v in daily.values()),'days_count':days,'daily':daily,
        'median_holding_seconds':median(all_holds) if all_holds else None,
        'rapid_close_fraction':Decimal(rapid)/Decimal(len(all_holds)) if all_holds else None,
        'trades_count':len(trades),'trades_per_day':Decimal(len(trades))/duration_days,
        'median_entry_sol':median(entries) if entries else None,
        'last_activity_at':max((r['timestamp'] for r,leg in trades),default=None),
        'result_scope':'Subtotal de tokens observados com custo SOL conhecido; tokens UNKNOWN não são zero nem incluídos neste subtotal.',
        'global_result_verified':False,'usd_conversion_verified':False,'drawdown':None,
        'profit_factor_definition':'Ganhos por token no pior caso / (perdas por token no pior caso + taxas SOL identificadas não atribuídas a swaps).',
        'daily_definition':'Blocos não sobrepostos de24h ancorados no início; custo aberto perdido no bloco final é cenário, não venda real.',
    }
    return {'currency':'SOL','tokens':token_rows,'metrics':metrics,'receipts':receipts,
            'limitations':list(LIMITATIONS),
            'coverage':{'input_count':len(receipts)+duplicate,'receipt_count':len(receipts),
                        'duplicate_count':duplicate,'invalid_count':invalid,'outside_window_count':outside,
                        'unknown_transactions':unknown_transactions,
                        'all_transactions_classified':unknown_transactions==0 and invalid==0}}
