"""Normalização conservadora de transações Helius para UMA carteira.

O saldo de uma conta de token não é o saldo SOL da carteira e o pagador da taxa
não é necessariamente o comprador. Valores nativos são guardados em lamports;
nenhum stablecoin é automaticamente convertido para USD e nenhuma taxa USD é
inventada. O protocolo é identificado por programa + discriminador + papéis de
contas do IDL oficial, nunca só por duas transferências em sentidos opostos.

IDLs oficiais Pump/PumpSwap, consultados em 28-10-2026, fixados em:
https://github.com/pump-fun/pump-public-docs/tree/639b87fb45c3adeffa6f7c4a1bf0cb85e392bf22/idl
Eventos Anchor self-CPI seguem https://solana.com/docs/programs/anchor-events;
auxiliares exigem tag, discriminador IDL e autoridade de eventos da chamada.
A cobertura destes programas NÃO demonstra cobertura global de primeiros
compradores, agregadores, variantes desconhecidas ou de todos os custos.
"""
from __future__ import annotations

import base64
from decimal import Decimal
from hashlib import sha256

from .models import Event, SOL, dumps, parse_time

PUMP_PROGRAM = '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P'
PUMP_AMM_PROGRAM = 'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA'
SYSTEM_PROGRAM = '11111111111111111111111111111111'
TOKEN_PROGRAM = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
TOKEN_2022_PROGRAM = 'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb'
ATA_PROGRAM = 'ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL'
COMPUTE_PROGRAM = 'ComputeBudget111111111111111111111111111111'
MEMO_PROGRAM = 'MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr'
AUXILIARY_PROGRAMS = {SYSTEM_PROGRAM, TOKEN_PROGRAM, TOKEN_2022_PROGRAM,
                      ATA_PROGRAM, COMPUTE_PROGRAM, MEMO_PROGRAM}
TOKEN_PROGRAMS = {TOKEN_PROGRAM, TOKEN_2022_PROGRAM}
ANCHOR_EVENT_TAG = bytes.fromhex('e445a52e51cb9a1d')
COMMON_EVENT_DISCRIMINATORS = {
    bytes([134,36,13,72,232,101,130,216]),  # InitUserVolumeAccumulatorEvent
    bytes([197,122,167,124,116,81,91,255]),  # SyncUserVolumeAccumulatorEvent
    bytes([97,97,215,144,93,146,22,124]),  # ExtendAccountEvent
}
EVENT_DISCRIMINATORS = {
    PUMP_PROGRAM: COMMON_EVENT_DISCRIMINATORS | {
        bytes([189,219,127,211,78,230,97,238]),  # TradeEvent
        bytes([111,176,109,139,49,108,213,251]),  # PostCompleteBuyEvent
        bytes([95,114,97,156,212,46,152,8]),  # CompleteEvent
        bytes([189,233,93,185,92,148,234,148]),  # CompletePumpAmmMigrationEvent
    },
    PUMP_AMM_PROGRAM: COMMON_EVENT_DISCRIMINATORS | {
        bytes([103,244,82,31,44,245,119,119]),  # BuyEvent
        bytes([62,47,55,10,165,3,220,42]),  # SellEvent
        bytes([120,248,61,83,31,142,107,144]),  # DepositEvent
        bytes([22,9,133,26,160,44,71,192]),  # WithdrawEvent
    },
}
BASE58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
ZERO = Decimal(0)

# (direção, base mint, quote mint ou None para SOL nativo, user, user base ATA,
#  user quote ATA). Os índices são posições zero-based na instrução do protocolo.
LEGACY_BUY = bytes([102, 6, 61, 18, 1, 218, 235, 234])
LEGACY_SELL = bytes([51, 230, 133, 164, 1, 127, 131, 173])
PUMP_SPECS = {
    LEGACY_BUY: ('buy', 2, None, 6, 5, None),
    bytes([56, 252, 116, 8, 158, 223, 205, 95]): ('buy', 2, None, 6, 5, None),
    LEGACY_SELL: ('sell', 2, None, 6, 5, None),
    bytes([184, 23, 238, 97, 103, 197, 211, 61]): ('buy', 1, 2, 13, 14, 15),
    bytes([194, 171, 28, 70, 104, 77, 91, 47]): ('buy', 1, 2, 13, 14, 15),
    bytes([93, 246, 130, 60, 231, 233, 64, 178]): ('sell', 1, 2, 13, 14, 15),
    bytes([7, 5, 29, 196, 245, 23, 101, 80]): ('buy', 1, 2, 8, 9, 10),
    bytes([225, 247, 80, 30, 213, 179, 132, 136]): ('buy', 1, 2, 8, 9, 10),
    bytes([28, 146, 222, 119, 38, 196, 105, 213]): ('sell', 1, 2, 8, 9, 10),
}
AMM_SPECS = {
    discriminator: (kind, 3, 4, 1, 5, 6)
    for discriminator, kind in (
        (LEGACY_BUY, 'buy'), (LEGACY_SELL, 'sell'),
        (bytes([198, 46, 21, 82, 180, 217, 232, 112]), 'buy'),
        (bytes([184, 23, 238, 97, 103, 197, 211, 61]), 'buy'),
        (bytes([194, 171, 28, 70, 104, 77, 91, 47]), 'buy'),
        (bytes([93, 246, 130, 60, 231, 233, 64, 178]), 'sell'),
    )
}
SPECS = {PUMP_PROGRAM: PUMP_SPECS, PUMP_AMM_PROGRAM: AMM_SPECS}
LIQUIDITY_SPECS = {bytes([242,35,198,137,82,225,242,182]): 'deposit',
                   bytes([183,18,70,156,148,109,161,34]): 'withdraw'}


def _integer(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _quantity(raw, decimals):
    # Decimal(int) é exato; scaleb/divisão no contexto por defeito poderia arredondar.
    return Decimal((int(raw < 0), tuple(int(ch) for ch in str(abs(raw))), -decimals))


def _b58decode(value):
    if not isinstance(value, str) or len(value) > 4096:
        return None
    number = 0
    try:
        for character in value:
            number = number * 58 + BASE58.index(character)
    except ValueError:
        return None
    prefix = len(value) - len(value.lstrip('1'))
    return b'\0' * prefix + (number.to_bytes((number.bit_length()+7)//8, 'big') if number else b'')


def _data(value):
    if isinstance(value, str):
        return _b58decode(value)
    if (isinstance(value, list) and len(value) == 2 and value[1] == 'base64'
            and isinstance(value[0], str) and len(value[0]) <= 8192):
        try:
            return base64.b64decode(value[0], validate=True)
        except ValueError:
            return None
    return None


def _account_keys(message, meta):
    raw = message.get('accountKeys')
    if not isinstance(raw, list) or not raw:
        raise ValueError('Transação Helius sem contas verificáveis')
    parsed = any(isinstance(value, dict) for value in raw)
    if parsed and not all(isinstance(value, dict) for value in raw):
        raise ValueError('Contas Helius com formato misto')
    keys = [value.get('pubkey') if parsed else value for value in raw]
    if any(not isinstance(value, str) or not value for value in keys):
        raise ValueError('Chave de conta Helius inválida')
    if not parsed:
        loaded = meta.get('loadedAddresses')
        if loaded is not None:
            if not isinstance(loaded, dict):
                raise ValueError('Contas carregadas Helius inválidas')
            for name in ('writable', 'readonly'):
                addresses = loaded.get(name, [])
                if not isinstance(addresses, list) or any(not isinstance(x, str) or not x for x in addresses):
                    raise ValueError('Contas carregadas Helius inválidas')
                keys.extend(addresses)
    return keys


def _resolve(value, keys):
    if isinstance(value, str):
        return value if value in keys else None
    if _integer(value) is not None and value < len(keys):
        return keys[value]
    return None


def _instructions(message, meta, keys):
    top = message.get('instructions')
    if not isinstance(top, list):
        raise ValueError('Instruções Helius indisponíveis')
    groups = meta.get('innerInstructions')
    complete = isinstance(groups, list)
    if groups is None:
        groups = []
    if not isinstance(groups, list):
        raise ValueError('Instruções internas Helius inválidas')
    output = []
    for index, instruction in enumerate(top):
        output.append((instruction, index, True))
    for group in groups:
        if not isinstance(group, dict) or _integer(group.get('index')) is None or not isinstance(group.get('instructions'), list):
            raise ValueError('Grupo de instruções internas Helius inválido')
        if group['index'] >= len(top):
            raise ValueError('Índice de instrução interna Helius inválido')
        output.extend((instruction, group['index'], False) for instruction in group['instructions'])
    normalized = []
    for instruction, index, top_level in output:
        if not isinstance(instruction, dict):
            raise ValueError('Instrução Helius malformada')
        program = instruction.get('programId') or _resolve(instruction.get('programIdIndex'), keys)
        if not isinstance(program, str) or not program or program not in keys:
            complete = False
            program = ''
        accounts = instruction.get('accounts', [])
        if not isinstance(accounts, list):
            raise ValueError('Contas da instrução Helius inválidas')
        resolved = [_resolve(value, keys) for value in accounts]
        if any(value is None for value in resolved):
            complete = False
        normalized.append({'program': program, 'accounts': resolved, 'data': _data(instruction.get('data')),
                           'parsed': instruction.get('parsed'), 'index': index, 'top_level': top_level})
    return normalized, complete


def _balance_records(meta, keys, wallet, limitations):
    phases = []
    complete = True
    for field in ('preTokenBalances', 'postTokenBalances'):
        rows = meta.get(field)
        if not isinstance(rows, list):
            rows = []
            complete = False
            limitations.append('Lista de saldos de tokens ausente; ausência não prova saldo zero.')
        phase = {}
        for row in rows:
            if not isinstance(row, dict):
                complete = False
                continue
            index = _integer(row.get('accountIndex'))
            mint, owner = row.get('mint'), row.get('owner')
            amount = row.get('uiTokenAmount')
            if (index is None or index >= len(keys) or not isinstance(mint, str) or not mint
                    or not isinstance(amount, dict)):
                complete = False
                continue
            raw, decimals = amount.get('amount'), _integer(amount.get('decimals'))
            if not isinstance(raw, str) or not raw.isascii() or not raw.isdigit() or decimals is None or decimals > 30:
                complete = False
                continue
            if not isinstance(owner, str) or not owner:
                owner = None
                complete = False
            value = {'mint': mint, 'owner': owner, 'raw': int(raw), 'decimals': decimals, 'account': keys[index]}
            if index in phase:
                complete = False
                continue
            phase[index] = value
        phases.append(phase)
    before, after = phases
    token_accounts = {}
    deltas = {}
    decimals_by_mint = {}
    pre_native, post_native = meta.get('preBalances'), meta.get('postBalances')
    for index in sorted(set(before) | set(after)):
        pre, post = before.get(index), after.get(index)
        sample = pre or post
        if pre and post and (pre['mint'], pre['decimals'], pre['owner']) != (post['mint'], post['decimals'], post['owner']):
            complete = False
            limitations.append('Mint, decimais ou proprietário mudam entre saldos; movimento não reconciliado.')
            continue
        if sample['owner'] is None:
            complete = False
            continue
        # Ausência de uma linha só significa zero numa conta criada/fechada,
        # demonstrada por saldo de lamports zero na fase correspondente.
        if pre is None:
            if not isinstance(pre_native, list) or index >= len(pre_native) or pre_native[index] != 0:
                complete = False
                limitations.append('Saldo inicial de conta de token não demonstrado.')
                continue
            pre_raw = 0
        else:
            pre_raw = pre['raw']
        if post is None:
            if not isinstance(post_native, list) or index >= len(post_native) or post_native[index] != 0:
                complete = False
                limitations.append('Saldo final de conta de token não demonstrado.')
                continue
            post_raw = 0
        else:
            post_raw = post['raw']
        state = dict(sample, before_raw=pre_raw, after_raw=post_raw, delta_raw=post_raw-pre_raw)
        token_accounts[keys[index]] = state
        if sample['owner'] != wallet:
            continue
        mint, decimals = sample['mint'], sample['decimals']
        if mint in decimals_by_mint and decimals_by_mint[mint] != decimals:
            complete = False
            limitations.append('Casas decimais contraditórias para o mesmo mint.')
            continue
        decimals_by_mint[mint] = decimals
        deltas[mint] = deltas.get(mint, 0) + state['delta_raw']
    return token_accounts, deltas, decimals_by_mint, complete


def transaction_wallets(row):
    """Proprietários observados de contas de token, nunca o fee payer por defeito."""
    meta = row.get('meta') if isinstance(row, dict) else None
    if not isinstance(meta, dict):
        return []
    owners = set()
    for field in ('preTokenBalances', 'postTokenBalances'):
        rows = meta.get(field)
        if isinstance(rows, list):
            owners.update(row['owner'] for row in rows if isinstance(row, dict)
                          and isinstance(row.get('owner'), str) and row['owner'])
    return sorted(owners)


def _known_anchor_event(instruction, instructions):
    """Ignora só eventos IDL emitidos por self-CPI com a autoridade correta."""
    data=instruction['data']; program=instruction['program']
    if (instruction['top_level'] or program not in SPECS or data is None or len(data)<16
            or data[:8]!=ANCHOR_EVENT_TAG or data[8:16] not in EVENT_DISCRIMINATORS[program]
            or len(instruction['accounts'])!=1):
        return False
    authority=instruction['accounts'][0]
    for caller in instructions:
        caller_data=caller['data']
        if (caller is instruction or caller['program']!=program or caller_data is None
                or caller['index']!=instruction['index'] or len(caller_data)<8):
            continue
        discriminator=caller_data[:8]
        spec=SPECS[program].get(discriminator)
        if spec is not None:
            if program==PUMP_AMM_PROGRAM:position=15
            elif spec[3]==6:position=10
            elif spec[3]==13:position=25 if spec[0]=='buy' else 24
            else:position=15
        elif program==PUMP_AMM_PROGRAM and discriminator in LIQUIDITY_SPECS:
            position=13
        else:
            continue
        if len(caller['accounts'])>position and caller['accounts'][position]==authority:
            return True
    return False


def _swap_instructions(instructions, wallet):
    recognized, unsupported = [], False
    for instruction in instructions:
        if _known_anchor_event(instruction,instructions):continue
        specs = SPECS.get(instruction['program'])
        if specs is None:
            continue
        data = instruction['data']
        spec = specs.get(data[:8]) if data is not None else None
        if spec is None or len(data) < 24:
            # Não interpretar variantes novas nem instruções create/migrate/LP
            # por semelhança de transferências. Conservador mesmo se outra conta
            # da transação também chamar o programa.
            unsupported = True
            continue
        kind, base_index, quote_index, user_index, base_ata_index, quote_ata_index = spec
        positions = [base_index, user_index, base_ata_index]
        positions += [i for i in (quote_index, quote_ata_index) if i is not None]
        accounts = instruction['accounts']
        if max(positions) >= len(accounts) or any(accounts[index] is None for index in positions):
            unsupported = True
            continue
        if accounts[user_index] != wallet:
            continue
        recognized.append({'kind': kind, 'protocol': 'pump' if instruction['program'] == PUMP_PROGRAM else 'pump_swap',
                           'program': instruction['program'], 'mint': accounts[base_index],
                           'quote_mint': accounts[quote_index] if quote_index is not None else SOL,
                           'user': accounts[user_index], 'base_account': accounts[base_ata_index],
                           'quote_account': accounts[quote_ata_index] if quote_ata_index is not None else None,
                           'instruction_index': instruction['index'], 'top_level': instruction['top_level']})
    return recognized, unsupported


def _liquidity_instructions(instructions, wallet):
    result = []
    for instruction in instructions:
        data = instruction['data']
        if instruction['program'] != PUMP_AMM_PROGRAM or data is None or len(data) < 32:
            continue
        kind = LIQUIDITY_SPECS.get(data[:8])
        accounts = instruction['accounts']
        if kind and len(accounts) >= 9 and accounts[2] == wallet:
            result.append({'kind':kind,'pool':accounts[0],'user':accounts[2],
                           'mints':accounts[3:6],'user_accounts':accounts[6:9],
                           'instruction_index':instruction['index']})
    return result


def _parsed(instruction):
    parsed = instruction['parsed']
    if not isinstance(parsed, dict) or not isinstance(parsed.get('info'), dict):
        return '', {}
    return str(parsed.get('type', '')).lower(), parsed['info']


def _trade_interference(instructions, swap):
    base_account = swap['base_account']
    for instruction in instructions:
        if instruction['program'] not in TOKEN_PROGRAMS:
            continue
        kind, info = _parsed(instruction)
        touched = base_account in instruction['accounts'] or base_account in (
            info.get('source'), info.get('destination'), info.get('account'), info.get('mint'))
        if not touched:
            continue
        if kind in ('mintto', 'minttochecked', 'burn', 'burnchecked', 'setauthority'):
            return True
        if instruction['index'] != swap['instruction_index'] and kind not in (
                'initializeaccount', 'initializeaccount2', 'initializeaccount3', 'closeaccount'):
            return True
        if instruction['top_level'] and not kind:
            return True
    return False


def _matched_usd(event, swap_evidence):
    # Opcional: só aceitar exatamente a mesma perna histórica já normalizada
    # Birdeye. Não usar o preço do ativo recebido nem uma paridade USDC=1.
    matches = [value for value in (swap_evidence or ()) if isinstance(value, Event)
               and value.source == 'birdeye' and not value.multi_step
               and value.wallet == event.wallet and value.tx_hash == event.tx_hash
               and value.token == event.token and value.kind == event.kind
               and value.quantity == event.quantity and value.timestamp == event.timestamp]
    if len(matches) != 1:
        return None
    value = matches[0].usd_value
    if not isinstance(value, Decimal) or not value.is_finite() or value < ZERO:
        return None
    return value


def normalize_transaction(row, wallet, *, target_token=None, swap_evidence=None):
    """Devolve events, evidence e limitations; formatos sem identidade dão ValueError.

    Compras reconhecidas exigem execução bem-sucedida, protocolo documentado,
    papéis do beneficiário e saldos reais coerentes. Em falta de prova, retornam
    movimentos ou eventos unknown, com todos os custos desconhecidos preservados.
    """
    if not isinstance(row, dict) or not isinstance(wallet, str) or not wallet:
        raise ValueError('Transação/carteira Helius inválida')
    transaction, meta = row.get('transaction'), row.get('meta')
    if not isinstance(transaction, dict) or not isinstance(meta, dict):
        raise ValueError('Detalhes de transação Helius indisponíveis')
    signatures, message = transaction.get('signatures'), transaction.get('message')
    if (not isinstance(signatures, list) or not signatures or not isinstance(signatures[0], str)
            or not signatures[0] or not isinstance(message, dict)):
        raise ValueError('Identidade de transação Helius indisponível')
    stamp = parse_time(row.get('blockTime'))
    signature = signatures[0]
    block = _integer(row.get('slot'))
    keys = _account_keys(message, meta)
    limitations = []
    fee = _integer(meta.get('fee'))
    payer = keys[0]
    token_accounts, deltas, decimals, balances_complete = _balance_records(meta, keys, wallet, limitations)
    instructions, instructions_complete = _instructions(message, meta, keys)
    pre, post = meta.get('preBalances'), meta.get('postBalances')
    native_valid = (isinstance(pre, list) and isinstance(post, list) and len(pre) == len(keys)
                    and len(post) == len(keys) and all(_integer(v) is not None for v in pre+post))
    native_delta = None
    if native_valid:
        native_delta = post[keys.index(wallet)]-pre[keys.index(wallet)] if wallet in keys else 0
    else:
        balances_complete = False
        limitations.append('Saldos SOL incompletos; não se assume ausência de custos/movimentos.')
    adjusted_native = (native_delta + (fee if payer == wallet else 0)
                       if native_delta is not None and (payer != wallet or fee is not None) else None)
    if fee is None:
        limitations.append('Taxa de rede em lamports desconhecida.')
    programs = sorted({instruction['program'] for instruction in instructions if instruction['program']})
    unexpected_programs = set(programs) - AUXILIARY_PROGRAMS - set(SPECS)
    evidence = {'signature': signature, 'slot': block, 'transaction_index': _integer(row.get('transactionIndex')),
                'block_time': stamp, 'fee_lamports': fee, 'fee_payer': payer,
                'fee_attributed_to_wallet': payer == wallet, 'fee_usd': None,
                'native_delta_lamports': native_delta, 'native_delta_excluding_identified_fee_lamports': adjusted_native,
                'programs': programs, 'recognized_swaps': [], 'recognized_buys': [], 'liquidity_operations': [],
                'balance_coverage_complete': balances_complete, 'instruction_coverage_complete': False,
                'token_deltas': [{'mint': mint, 'raw_delta': str(raw), 'decimals': decimals[mint],
                                  'quantity': _quantity(raw, decimals[mint])} for mint, raw in sorted(deltas.items())],
                'raw_hash': sha256(dumps(row).encode()).hexdigest(),
                'scope': 'Transação observada e contas tocadas; não prova cobertura integral da carteira.'}
    events = []

    def event(token, kind, quantity=ZERO, multi=False, *, native=False):
        identity = f'helius:{signature}:{token}:{kind}'
        # SOL nativo e o saldo SPL de wSOL são componentes diferentes. Quando
        # ambos ficam desconhecidos, a identidade legada colidia e revertia
        # todo o lote persistido. Só desambiguar o componente nativo em colisão;
        # os IDs históricos sem colisão permanecem iguais.
        if (native and token == SOL and kind == 'unknown'
                and any(previous.id == identity for previous in events)):
            identity += ':native-sol'
            limitations.append('Movimentos wSOL e SOL nativo mantidos separados; custos e interpretação continuam desconhecidos.')
        item = Event(identity, wallet, token, stamp, kind, quantity,
                     tx_hash=signature, block=block, source='helius', multi_step=multi)
        if kind in ('buy', 'sell'):
            value = _matched_usd(item, swap_evidence)
            if value is not None:
                from dataclasses import replace
                item = replace(item, usd_value=value)
        events.append(item)

    if 'err' not in meta:
        limitations.append('Estado de execução ausente; não se presume sucesso.')
        event(target_token or next(iter(deltas), SOL), 'unknown', multi=True)
    elif meta['err'] is not None:
        event(SOL, 'failed')
        evidence['execution_status'] = 'failed'
    else:
        evidence['execution_status'] = 'succeeded'
        liquidity = _liquidity_instructions(instructions,wallet)
        pump_calls = [i for i in instructions if i['program'] in SPECS and not _known_anchor_event(i,instructions)]
        if (len(liquidity)==1 and len(pump_calls)==1 and balances_complete
                and instructions_complete and not unexpected_programs):
            operation=liquidity[0]
            sign=1 if operation['kind']=='deposit' else -1
            states=[token_accounts.get(account) for account in operation['user_accounts']]
            valid=(all(states) and len(set(operation['mints']))==3
                   and all(state['owner']==wallet and state['mint']==mint
                           and state['delta_raw']==deltas.get(mint,0)
                           for state,mint in zip(states,operation['mints']))
                   and deltas.get(operation['mints'][0],0)*sign<0
                   and deltas.get(operation['mints'][1],0)*sign<0
                   and deltas.get(operation['mints'][2],0)*sign>0
                   and not any(raw and mint not in operation['mints'] for mint,raw in deltas.items()))
            if valid:
                for mint in sorted(operation['mints']):
                    event(mint,'liquidity',_quantity(abs(deltas[mint]),decimals[mint]))
                if adjusted_native:
                    event(SOL,'unknown',_quantity(abs(adjusted_native),9),multi=True,native=True)
                evidence['liquidity_operations']=[operation]
                evidence['instruction_coverage_complete']=not adjusted_native
                limitations.append('Liquidez identificada por IDL; custo das parcelas e taxas USD continuam desconhecidos.')
                return {'events':events,'evidence':evidence,'limitations':list(dict.fromkeys(limitations))}
        swaps, unsupported = _swap_instructions(instructions, wallet)
        relevant = [value for value in swaps if target_token is None or value['mint'] == target_token]
        selected = relevant[0] if len(relevant) == 1 and len(swaps) == 1 else None
        valid_swap = (selected is not None and balances_complete and instructions_complete
                      and not unexpected_programs and not unsupported and not _trade_interference(instructions, selected))
        if valid_swap:
            base = selected['mint']; quote = selected['quote_mint']
            state = token_accounts.get(selected['base_account'])
            raw = deltas.get(base, 0)
            expected = 1 if selected['kind'] == 'buy' else -1
            valid_swap = (state is not None and state['owner'] == wallet and state['mint'] == base
                          and state['delta_raw'] == raw and raw * expected > 0 and base != quote
                          and not any(v and token not in (base, quote) for token, v in deltas.items()))
            quote_raw = deltas.get(quote, 0)
            if quote == SOL:
                valid_swap = valid_swap and ((quote_raw * expected < 0)
                          or (adjusted_native is not None and adjusted_native * expected < 0))
            else:
                quote_state = token_accounts.get(selected['quote_account'])
                valid_swap = (valid_swap and quote_state is not None and quote_state['owner'] == wallet
                              and quote_state['mint'] == quote and quote_state['delta_raw'] == quote_raw
                              and quote_raw * expected < 0)
            if valid_swap:
                event(base, selected['kind'], _quantity(abs(raw), decimals[base]))
                if quote_raw:
                    event(quote, 'sell' if expected > 0 else 'buy', _quantity(abs(quote_raw), decimals[quote]))
                if adjusted_native:
                    # SOL desembolsado pode conter renda de contas ou fluxos
                    # adicionais. Não converter o total em custo exato do swap.
                    event(SOL, 'unknown', _quantity(abs(adjusted_native), 9), multi=True, native=True)
                    limitations.append('Fluxo SOL contém componentes não atribuídos; renda, taxas e custo USD não reconciliados.')
                evidence['recognized_swaps'] = [selected]
                evidence['recognized_buys'] = [base] if selected['kind'] == 'buy' else []
                evidence['instruction_coverage_complete'] = not adjusted_native
        if not valid_swap:
            active = {mint: raw for mint, raw in deltas.items() if raw}
            kinds = [_parsed(instruction)[0] for instruction in instructions if instruction['program'] in TOKEN_PROGRAMS]
            owned_wrap_actions = [i for i in instructions if i['program'] in TOKEN_PROGRAMS
                and _parsed(i)[0] in ('syncnative','closeaccount')
                and token_accounts.get(_parsed(i)[1].get('account'),{}).get('owner') == wallet
                and token_accounts.get(_parsed(i)[1].get('account'),{}).get('mint') == SOL]
            conflicting_wrap_transfers = any(_parsed(i)[0] in ('transfer','transferchecked')
                for i in instructions if i['program'] in TOKEN_PROGRAMS)
            wrap = (balances_complete and instructions_complete and bool(owned_wrap_actions)
                    and not conflicting_wrap_transfers and not unexpected_programs and not swaps and not unsupported
                    and set(active).issubset({SOL}) and bool(active)
                    and any(kind in ('syncnative', 'closeaccount') for kind in kinds)
                    and adjusted_native is not None and adjusted_native * active[SOL] < 0)
            mint_or_authority = any(kind in ('mintto', 'minttochecked', 'burn', 'burnchecked', 'setauthority') for kind in kinds)
            parsed_token_types = {'transfer','transferchecked','initializeaccount','initializeaccount2',
                                  'initializeaccount3','closeaccount','syncnative','approve','approvechecked','revoke'}
            token_instructions_known = all(_parsed(i)[0] in parsed_token_types
                                           for i in instructions if i['program'] in TOKEN_PROGRAMS)
            simple_transfer = (balances_complete and instructions_complete and token_instructions_known and not unexpected_programs
                               and not unsupported and not swaps and not mint_or_authority
                               and all(instruction['program'] in AUXILIARY_PROGRAMS for instruction in instructions))
            if wrap:
                event(SOL, 'wrap', _quantity(abs(active[SOL]), 9))
                evidence['instruction_coverage_complete'] = True
                limitations.append('Conversão SOL/wSOL identificada; renda e taxa USD continuam por apurar.')
            else:
                for mint, raw in sorted(active.items()):
                    kind = ('transfer_in' if raw > 0 else 'transfer_out') if simple_transfer else 'unknown'
                    event(mint, kind, _quantity(abs(raw), decimals[mint]), multi=not simple_transfer)
                if adjusted_native:
                    system_net = 0
                    for instruction in instructions:
                        kind, info = _parsed(instruction)
                        amount = _integer(info.get('lamports'))
                        if instruction['program'] == SYSTEM_PROGRAM and kind == 'transfer' and amount is not None:
                            if info.get('destination') == wallet: system_net += amount
                            if info.get('source') == wallet: system_net -= amount
                    if simple_transfer and system_net == adjusted_native:
                        event(SOL, 'transfer_in' if adjusted_native > 0 else 'transfer_out', _quantity(abs(adjusted_native), 9))
                    else:
                        event(SOL, 'unknown', _quantity(abs(adjusted_native), 9), multi=True, native=True)
                if not events:
                    event(target_token or SOL, 'unknown', multi=True)
                evidence['instruction_coverage_complete'] = simple_transfer and bool(active) and not adjusted_native
            if swaps or unsupported or unexpected_programs or not balances_complete:
                limitations.append('Instrução, rota, proprietário ou saldo insuficientes para classificar uma compra/venda.')
    if events and any(item.kind in ('buy', 'sell') for item in events):
        limitations.append('Preços históricos USD, custos totais e impacto não são deduzidos apenas dos saldos on-chain.')
    evidence['balance_coverage_complete'] = balances_complete
    return {'events': events, 'evidence': evidence, 'limitations': list(dict.fromkeys(limitations))}
