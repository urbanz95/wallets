"""Histórico Helius resumível e descoberta de compradores observados.

``pages_complete`` refere-se apenas ao endereço/intervalo pedido. Consultar um
mint não prova cobertura de todos os mercados ou migrações de um token.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import re
from .collection import valid_address
from .models import UTC, Event, dumps, iso, parse_time, utcnow

CURSOR = re.compile(r'^\d+:\d+$')
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class HistoryError(ValueError):
    """Dados incompatíveis com a paginação; sem detalhes de autenticação."""


def transaction_identity(row):
    transaction = row.get('transaction') if isinstance(row, dict) else None
    signatures = transaction.get('signatures') if isinstance(transaction, dict) else None
    if (not isinstance(signatures, list) or not signatures
            or not all(isinstance(value, str) and value.strip() for value in signatures)):
        raise HistoryError('Transação sem assinatura verificável.')
    return signatures[0]


def transaction_position(row):
    slot, index = row.get('slot'), row.get('transactionIndex')
    if not isinstance(slot, int) or isinstance(slot, bool) or slot < 0:
        raise HistoryError('Transação sem slot válido.')
    if index is not None and (not isinstance(index, int) or isinstance(index, bool) or index < 0):
        raise HistoryError('Transação com posição inválida no bloco.')
    return slot, index


def _ordered(rows, ascending):
    # Ordenação de apresentação é determinística; índice ausente continua
    # explicitamente desconhecido e não se transforma em evidência cronológica.
    return sorted(rows, key=lambda row: (row['slot'], row.get('transactionIndex')
                  if row.get('transactionIndex') is not None else 2**63,
                  transaction_identity(row)), reverse=not ascending)


class HeliusHistory:
    def __init__(self, client, store, config, *, clock=utcnow, normalizer=None):
        self.client, self.store, self.config = client, store, config
        self.clock = clock
        self.normalizer = normalizer

    @property
    def settings(self):
        return self.config.get('helius', self.config)

    @property
    def max_pages(self):
        return max(1, min(100, int(self.settings.get('max_pages', 5))))

    @property
    def page_size(self):
        return max(1, min(1000, int(self.settings.get('page_size', 100))))

    @staticmethod
    def cursor_key(address, ascending=False, token_accounts=True):
        return f'helius:history:v1:{address}:{"ata" if token_accounts else "direct"}:{"asc" if ascending else "desc"}'

    def _state(self, key):
        raw = self.store.get_cursor(key)
        if not raw:
            return {'schema': 1, 'covered_start': None, 'covered_end': None, 'pending': None}
        try:
            state = json.loads(raw)
            if not isinstance(state, dict) or state.get('schema') != 1:
                raise ValueError
            a, b = state.get('covered_start'), state.get('covered_end')
            if (a is None) != (b is None) or (a is not None and parse_time(a) > parse_time(b)):
                raise ValueError
            pending = state.get('pending')
            if pending is not None:
                if not isinstance(pending, dict) or parse_time(pending['start']) >= parse_time(pending['end']):
                    raise ValueError
                token = pending.get('next')
                if token is not None and (not isinstance(token, str) or not CURSOR.fullmatch(token)):
                    raise ValueError
                seen = pending.get('seen', [])
                if not isinstance(seen, list) or len(seen) > 100000 or any(
                        not isinstance(t, str) or not CURSOR.fullmatch(t) for t in seen):
                    raise ValueError
            return state
        except (ValueError, TypeError, KeyError, OverflowError):
            raise HistoryError('Cursor Helius inválido; não assumir histórico completo.') from None

    def _save_state(self, key, state):
        self.store.set_cursor(key, dumps(state))

    @staticmethod
    def _covered(state, start, end):
        return (state.get('covered_start') is not None
                and parse_time(state['covered_start']) <= start
                and parse_time(state['covered_end']) >= end)

    @staticmethod
    def _finish_window(state, start, end):
        a, b = state.get('covered_start'), state.get('covered_end')
        if a is not None and start <= parse_time(b) and end >= parse_time(a):
            start, end = min(start, parse_time(a)), max(end, parse_time(b))
        state['covered_start'], state['covered_end'] = iso(start), iso(end)
        state['pending'] = None

    def history(self, address, start, end, *, ascending=False, token_accounts=True):
        from .helius import HeliusError
        start, end = parse_time(start), parse_time(end)
        if not valid_address(address):
            raise ValueError('Endereço público Solana inválido.')
        if start >= end or end > self.clock():
            raise ValueError('Intervalo Helius inválido ou posterior ao instante atual.')
        key = self.cursor_key(address, ascending, token_accounts)
        failures, queried_windows, pages = [], [], 0
        try:
            state = self._state(key)
        except HistoryError as exc:
            # Cursor corrompido não é apagado: exige revisão, evita saltar páginas.
            state = None
            failures.append(str(exc))
        while state is not None and pages < self.max_pages and not self._covered(state, start, end):
            pending = state.get('pending')
            if pending is None:
                query_start = start
                if state.get('covered_start') is not None and start >= parse_time(state['covered_start']):
                    query_start = max(start, parse_time(state['covered_end']) - timedelta(seconds=1))
                pending = {'start': iso(query_start), 'end': iso(end), 'next': None, 'seen': []}
                state['pending'] = pending
                # A janela fica ancorada antes do primeiro pedido. Falhas de rede
                # ou crashes retomam exatamente estes filtros e não um fim móvel.
                self._save_state(key, state)
            query_start, query_end = parse_time(pending['start']), parse_time(pending['end'])
            if query_end > end:
                failures.append('Há uma janela pendente posterior ao corte solicitado; consulta futura recusada.')
                break
            query = {
                'transactionDetails': 'full', 'encoding': 'jsonParsed',
                'maxSupportedTransactionVersion': 1, 'sortOrder': 'asc' if ascending else 'desc',
                'limit': self.page_size, 'commitment': 'finalized',
                'filters': {'blockTime': {'gte': int(query_start.timestamp()), 'lte': int(query_end.timestamp())},
                            'status': 'any', 'tokenAccounts': 'balanceChanged' if token_accounts else 'none'},
            }
            if pending.get('next'):
                query['paginationToken'] = pending['next']
            queried_windows.append({'start': query_start, 'end': query_end, 'pagination_token': pending.get('next')})
            try:
                result = self.client.request('getTransactionsForAddress', [address, query])
                pages += 1
                if not isinstance(result, dict) or not isinstance(result.get('data'), list):
                    raise HistoryError('Histórico Helius sem array de transações válido.')
                rows = result['data']
                if len(rows) > self.page_size or any(not isinstance(row, dict) for row in rows):
                    raise HistoryError('Página Helius malformada ou acima do limite pedido.')
                continuation = result.get('paginationToken')
                if continuation is not None and (not isinstance(continuation, str) or not CURSOR.fullmatch(continuation)):
                    raise HistoryError('Continuação Helius inválida.')
                if continuation and (not rows or continuation in pending['seen']):
                    raise HistoryError('Paginação Helius repetida ou vazia com continuação.')
                if 'paginationToken' not in result and len(rows) >= self.page_size:
                    raise HistoryError('Página cheia sem indicação de fim; histórico não comprovado.')
                identified = {}
                for row in rows:
                    tx_id = transaction_identity(row)
                    transaction_position(row)
                    timestamp = parse_time(row.get('blockTime'))
                    if not query_start <= timestamp <= query_end or timestamp > end:
                        raise HistoryError('Transação Helius fora do intervalo ou com informação futura.')
                    if tx_id in identified and dumps(identified[tx_id]) != dumps(row):
                        raise HistoryError('Assinatura duplicada com conteúdo divergente.')
                    identified[tx_id] = row
                # Persistência precede sempre o avanço do token de paginação.
                self.store.put_source_transactions('helius', address, list(identified.values()))
                if continuation:
                    pending['next'] = continuation
                    pending['seen'].append(continuation)
                    self._save_state(key, state)
                else:
                    self._finish_window(state, query_start, query_end)
                    self._save_state(key, state)
            except HeliusError as exc:
                failures.append(f'Histórico Helius interrompido ({type(exc).__name__}); cobertura insuficiente.')
                break
            except (HistoryError, ValueError, TypeError, KeyError, OverflowError) as exc:
                failures.append(str(exc) if isinstance(exc, HistoryError) else 'Persistência ou formato do histórico Helius incoerente.')
                break
        complete = state is not None and self._covered(state, start, end) and not failures
        if not complete and state is not None and pages >= self.max_pages:
            failures.append('Limite de páginas Helius atingido; janela e continuação guardadas para retomar.')
        rows = self.store.get_source_transactions('helius', address, start, end)
        # Também verificar os registos recuperados: uma base inconsistente não
        # deve transformar um erro numa lista aparentemente válida de transações.
        valid = []
        for row in rows:
            try:
                transaction_identity(row); transaction_position(row)
                if not start <= parse_time(row.get('blockTime')) <= end:
                    raise HistoryError('Transação persistida fora do período.')
                valid.append(row)
            except (HistoryError, ValueError, TypeError, KeyError, OverflowError):
                failures.append('Transação persistida inválida; cobertura não confirmada.')
                complete = False
        ordering_complete = all(row.get('transactionIndex') is not None for row in valid)
        return {
            'source': 'helius', 'address': address, 'token_accounts': bool(token_accounts),
            'start': start, 'end': end, 'transactions': _ordered(valid, ascending),
            'pages_complete': complete, 'truncated': not complete,
            'ordering_complete': ordering_complete, 'failures': list(dict.fromkeys(failures)),
            'pages_requested': pages, 'queried_windows': queried_windows,
            'covered_start': parse_time(state['covered_start']) if state and state.get('covered_start') else None,
            'covered_end': parse_time(state['covered_end']) if state and state.get('covered_end') else None,
        }

    def discover_first_observed(self, token, now, *, addresses=()):
        now = parse_time(now)
        if not valid_address(token) or now > self.clock():
            raise ValueError('Token ou data de descoberta inválidos.')
        if isinstance(addresses, str):
            raise ValueError('Os endereços de lançamento devem ser uma lista.')
        if any(not valid_address(address) for address in addresses):
            raise ValueError('Endereço de lançamento inválido.')
        max_addresses = max(1, int(self.settings.get('max_launch_addresses', 3)))
        all_addresses = list(dict.fromkeys([token, *addresses]))
        scoped_addresses = all_addresses[:max_addresses]
        limit = max(1, int(self.settings.get('max_early_buyers', 20)))
        scope_complete, ordering_complete, failures, rows_by_signature = True, True, [], {}
        conflicted_signatures=set()
        for address in scoped_addresses:
            history = self.history(address, EPOCH, now, ascending=True, token_accounts=False)
            scope_complete = scope_complete and history['pages_complete']
            ordering_complete = ordering_complete and history['ordering_complete']
            failures.extend(history['failures'])
            for row in history['transactions']:
                identity = transaction_identity(row)
                if identity in conflicted_signatures:continue
                previous = rows_by_signature.get(identity)
                if previous is not None and dumps(previous) != dumps(row):
                    scope_complete = False
                    failures.append('A mesma assinatura difere entre endereços consultados; descoberta não comprovada.')
                    conflicted_signatures.add(identity)
                    rows_by_signature.pop(identity,None)
                    continue
                rows_by_signature[identity] = row
        if self.normalizer is None:
            from .helius_normalization import normalize_transaction
            normalizer = normalize_transaction
        else:
            normalizer = self.normalizer
        buyers = {}
        for row in _ordered(list(rows_by_signature.values()), True):
            meta = row.get('meta')
            if not isinstance(meta, dict) or 'err' not in meta or meta['err'] is not None:
                continue
            pre,post=meta.get('preTokenBalances'),meta.get('postTokenBalances')
            if not isinstance(pre,list) or not isinstance(post,list):
                failures.append('Saldos de token indisponíveis; beneficiário da compra não comprovado.')
                continue
            balances = [*pre,*post]
            owners = {balance.get('owner') for balance in balances if isinstance(balance, dict)
                      and balance.get('mint') == token and valid_address(balance.get('owner'))}
            for owner in sorted(owners):
                try:
                    normalized = normalizer(row, owner, target_token=token)
                    if not isinstance(normalized,dict) or not isinstance(normalized.get('events'),list):
                        raise ValueError('Normalização indisponível')
                    events = normalized['events']
                except (ValueError, TypeError, KeyError, OverflowError):
                    failures.append('Operação inicial não interpretável; não classificada como compra.')
                    continue
                for event in events:
                    if (isinstance(event,Event) and event.wallet == owner and event.token == token and event.kind == 'buy'
                            and event.source == 'helius' and not event.multi_step
                            and event.quantity > 0 and event.timestamp <= now):
                        buyers.setdefault(owner, {
                            'wallet': owner, 'tx_hash': event.tx_hash, 'traded_at': event.timestamp,
                            'slot': row['slot'], 'transaction_index': row.get('transactionIndex'),
                        })
        selected = list(buyers.values())[:limit]
        for buyer in selected:
            self.store.record_discovery(buyer['wallet'], token, now, evidence={
                'origin': 'helius_first_observed', 'source': 'helius',
                'scope_addresses': scoped_addresses, 'tx_hash': buyer['tx_hash'],
                'traded_at': iso(buyer['traded_at']), 'slot': buyer['slot'],
                'transaction_index': buyer['transaction_index'], 'scope_pages_complete': scope_complete,
                'global_first_buyers_verified': False, 'ordering_complete': ordering_complete,
            })
        return {
            'source': 'helius', 'token': token, 'observed_at': now,
            'wallets': [buyer['wallet'] for buyer in selected], 'buyers': selected,
            'scope_addresses': scoped_addresses, 'scope_pages_complete': scope_complete,
            'ordering_complete': ordering_complete,
            'global_first_buyers_verified': False,
            'discovery_scope': 'primeiros_compradores_observados_nos_enderecos_consultados',
            'limit_reached': len(buyers) > limit or len(all_addresses) > max_addresses,
            'failures': list(dict.fromkeys(failures)),
            'limitations': [
                'Mint e endereços conhecidos não demonstram cobertura de todas as curvas, pools ou migrações.',
                'Compradores observados servem apenas como candidatas; não passam verificações por terem comprado cedo.',
                'Ordens no mesmo bloco exigem transactionIndex; posição ausente permanece desconhecida.',
            ],
        }
