"""Descoberta e normalização conservadora. Métricas Birdeye ficam separadas do FIFO."""
from __future__ import annotations
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import re
from .models import Coverage, Event, dumps, iso, parse_time, utcnow
from .birdeye import BirdeyeError, InvalidResponse

ADDRESS = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')

def valid_address(address):
    return isinstance(address, str) and bool(ADDRESS.fullmatch(address))

def decimal(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None

def _quantity(leg):
    """ui_amount_mode=raw; não adivinhar casas decimais ou multiplicadores."""
    amount = decimal(leg.get('amount', leg.get('change_amount')))
    decimals = leg.get('decimals')
    if amount is None or not isinstance(decimals, int) or not 0 <= decimals <= 30:
        return None
    return abs(amount) / Decimal(10) ** decimals

def _timestamp(row):
    return parse_time(row.get('block_unix_time', row.get('unix_time')))

def normalize_trade(row, wallet):
    stamp = _timestamp(row)
    tx = str(row.get('tx_hash', ''))
    index = f"{row.get('ins_index', '?')}:{row.get('inner_ins_index', '?')}"
    indices_known = all(isinstance(row.get(field), int) and not isinstance(row.get(field), bool)
                        for field in ('ins_index', 'inner_ins_index'))
    if not indices_known or not tx:
        index += ':' + hashlib.sha256(dumps(row).encode()).hexdigest()[:24]
    base, quote = row.get('base') or row.get('from'), row.get('quote') or row.get('to')
    legs = [leg for leg in (base, quote) if isinstance(leg, dict)]
    raw_kind = str(row.get('tx_type', row.get('type', ''))).lower()
    failed = row.get('success') is False or str(row.get('status', '')).lower() in ('failed', 'failure', 'error')
    if failed or raw_kind in ('add', 'remove', 'add_liquidity', 'remove_liquidity', 'liquidity'):
        kind = 'failed' if failed else 'liquidity'
        token = next((x.get('address') for x in legs if valid_address(x.get('address'))), wallet)
        return [Event(f'{tx}:{index}:{kind}', wallet, token, stamp, kind, Decimal(0),
                      tx_hash=tx, block=row.get('block_number'), source='birdeye')]
    if raw_kind and raw_kind not in ('swap', 'buy', 'sell'):
        token = next((x.get('address') for x in legs if valid_address(x.get('address'))), wallet)
        return [Event(f'{tx}:{index}:unknown', wallet, token, stamp, 'unknown', Decimal(0),
                      tx_hash=tx, block=row.get('block_number'), source='birdeye')]
    if not indices_known or not tx:
        token = next((x.get('address') for x in legs if valid_address(x.get('address'))), wallet)
        return [Event(f'{tx}:{index}:unknown', wallet, token, stamp, 'unknown', Decimal(0),
                      tx_hash=tx, block=row.get('block_number'), source='birdeye', multi_step=True)]
    parsed = []
    for leg in legs:
        token = leg.get('address', '')
        quantity = _quantity(leg)
        kind = {'from': 'sell', 'to': 'buy'}.get(leg.get('type_swap'))
        # from/to shape may carry direction in object key, but the docs do not
        # guarantee that name alone expresses owner cashflow. Fail closed.
        if not valid_address(token) or quantity is None or kind is None:
            continue
        price = decimal(leg.get('price'))
        usd = price * quantity if price is not None and price >= 0 else None
        if usd is None and len(legs) == 2:
            usd = decimal(row.get('volume_usd'))
        if usd is not None and usd < 0:
            usd = None
        eid = f'{tx}:{index}:{token}:{kind}'
        parsed.append(Event(eid, wallet, token, stamp, kind, quantity, usd,
                            None, tx, row.get('block_number'), 'birdeye'))
    if len(parsed) == 2 and parsed[0].kind != parsed[1].kind and tx:
        # Uma única consideração USD para o swap inteiro. O preço indicativo
        # do ativo recebido não é, por si só, o custo pago pela carteira.
        consideration = decimal(row.get('volume_usd'))
        if consideration is None or consideration < 0:
            consideration = next(event.usd_value for event in parsed if event.kind == 'sell')
        return [replace(event, usd_value=consideration) for event in parsed]
    token = next((x.get('address') for x in legs if valid_address(x.get('address'))), wallet)
    return [Event(f'{tx}:{index}:unknown', wallet, token, stamp, 'unknown', Decimal(0),
                  tx_hash=tx, block=row.get('block_number'), source='birdeye', multi_step=True)]

def normalize_transfer(row, wallet):
    token = row.get('token_address', '')
    stamp = _timestamp(row)
    amount = decimal(row.get('ui_amount'))
    kind = {'in': 'transfer_in', 'out': 'transfer_out'}.get(row.get('flow'), 'unknown')
    action = str(row.get('action', '')).lower()
    if action in ('airdrop', 'distribution') and row.get('flow') == 'in':
        kind = 'airdrop'
    elif action and action not in ('transfer', 'transfer_checked', 'spl_transfer', 'sol_transfer'):
        # Mint/burn/authority não são compras nem transferências ordinárias.
        kind = 'unknown'
    if amount is None or not valid_address(token):
        token, amount, kind = token if valid_address(token) else wallet, Decimal(0), 'unknown'
    tx = str(row.get('tx_hash', ''))
    # Mesmo hash pode ter várias transferências. A identificação inclui contas,
    # quantidade e fluxo, mas linhas perfeitamente idênticas colapsam conservadoramente.
    digest = hashlib.sha256(dumps(row).encode()).hexdigest()[:24]
    return Event(f'{tx}:transfer:{digest}', wallet, token, stamp, kind, abs(amount),
                 None, None, tx, row.get('block_number'), 'birdeye')

class Collector:
    def __init__(self, client, store, config, *, clock=utcnow):
        self.client, self.store, self.config, self.clock = client, store, config, clock
        self.limitations = []
        self.selected_tokens = []

    @property
    def max_pages(self):
        return max(1, min(100, self.config.get('api', {}).get('max_pages', 5)))

    @staticmethod
    def _items(result, key='items'):
        data = result.get('data')
        if not isinstance(data, dict) or not isinstance(data.get(key), list):
            raise InvalidResponse('Formato Birdeye inesperado; não assumir lista vazia.')
        if any(not isinstance(row,dict) for row in data[key]):
            raise InvalidResponse('Registo Birdeye malformado; cobertura não confirmada.')
        return data[key], data

    def discover(self, now):
        settings = self.config.get('discovery', {})
        wallets = []
        seen_tokens = set()
        max_tokens = settings.get('max_tokens', 5)
        max_wallets = settings.get('max_wallets', 10)
        for page in range(self.max_pages):
            params = {
                'sort_by': 'volume_24h_usd', 'sort_type': 'desc', 'offset': page * 100,
                'limit': 100,
                'platform_name': settings.get('platform_name', 'pump.fun'),
                'min_liquidity': settings.get('min_liquidity', 100000),
                'min_volume_24h_usd': settings.get('min_volume_24h_usd', 100000),
                'min_price_change_24h_percent': settings.get('min_price_change_24h_percent', 10),
                'min_recent_listing_time': int((now - timedelta(days=settings.get('max_age_days', 90))).timestamp()),
                'max_recent_listing_time': int((now - timedelta(days=settings.get('min_age_days', 1))).timestamp()),
            }
            items, meta = self._items(self.client.request('GET', '/defi/v3/token/list', params))
            for token in items:
                address = token.get('address')
                if not valid_address(address) or address in seen_tokens:
                    continue
                # Critérios são reaplicados localmente. Listing age != idade de mint.
                try:
                    listed = parse_time(token['recent_listing_time'])
                    age = (now - listed).total_seconds() / 86400
                    meets = (settings.get('min_age_days', 1) <= age <= settings.get('max_age_days', 90)
                             and decimal(token['liquidity']) >= Decimal(str(params['min_liquidity']))
                             and decimal(token['volume_24h_usd']) >= Decimal(str(params['min_volume_24h_usd']))
                             and decimal(token['price_change_24h_percent']) >= Decimal(str(params['min_price_change_24h_percent'])))
                except (ValueError, TypeError, KeyError):
                    meets = False
                if not meets:
                    continue
                seen_tokens.add(address)
                self.selected_tokens.append(address)
                self.store.record_token_selection(address,now,token)
                for trader_page in range(self.max_pages):
                    rows, trader_meta = self._items(self.client.request(
                        'GET', '/defi/v2/tokens/top_traders', {
                            'address': address, 'time_frame': '30d', 'sort_by': 'volume',
                            'sort_type': 'desc', 'offset': trader_page * 10, 'limit': 10}))
                    for row in rows:
                        wallet = row.get('owner')
                        if valid_address(wallet):
                            self.store.record_discovery(wallet, address, now,{'tags':row.get('tags'), 'endpoint':'/defi/v2/tokens/top_traders'})
                            if wallet not in wallets and len(wallets) < max_wallets:
                                wallets.append(wallet)
                    if len(rows) < 10 or len(wallets) >= max_wallets:
                        break
                if len(seen_tokens) >= max_tokens:
                    return wallets
            if not meta.get('hasNext', meta.get('has_next', False)):
                return wallets
            if not items:
                self.limitations.append('Descoberta: página vazia com continuação; universo incompleto.')
                return wallets
        self.limitations.append('Descoberta limitada pelo máximo configurado de páginas.')
        return wallets

    def _paged(self, path, method, params, field, page_size=100):
        rows = []
        holdings_total = None
        for page in range(self.max_pages):
            query = dict(params, offset=page * page_size, limit=page_size)
            result = self.client.request(method, path, query if method == 'GET' else None,
                                         query if method == 'POST' else None)
            items, meta = self._items(result, field)
            rows.extend(items)
            if path == '/wallet/v2/current-net-worth':
                pagination = result.get('pagination')
                total = pagination.get('total') if isinstance(pagination, dict) else None
                if (isinstance(total, bool) or not isinstance(total, int) or total < 0
                        or total < len(rows) or (holdings_total is not None and total != holdings_total)):
                    return rows, False, meta
                holdings_total = total
                if len(rows) == total:
                    return rows, True, meta
                more = True
            else:
                flag = meta.get('hasNext', meta.get('has_next'))
                more = flag if isinstance(flag, bool) else len(items) >= page_size
                if path == '/wallet/v2/pnl/details':
                    total = (meta.get('summary') or {}).get('unique_tokens')
                    if isinstance(total, int) and total > len(rows):
                        more = True
            if not more:
                return rows, True, meta
            if not items:
                return rows, False, meta
        return rows, False, meta

    def _transfers(self, wallet, start, end):
        rows, cursor, seen = [], None, set()
        for _ in range(self.max_pages):
            body = {'wallet': wallet, 'cursor': cursor} if cursor else {
                'wallet': wallet, 'time_from': int(start.timestamp()),
                'time_to': int(end.timestamp()), 'limit': 100}
            result = self.client.request('POST', '/wallet/v2/transfer', body=body)
            items = result.get('data')
            if not isinstance(items, list):
                raise InvalidResponse('Transferências Birdeye sem array data válido.')
            if any(not isinstance(row,dict) for row in items):
                raise InvalidResponse('Registo de transferência Birdeye malformado.')
            rows.extend(items)
            cursor = result.get('next_cursor')
            if cursor is None or cursor == '':
                # Documentação textual refere cursor, schema não garante campo.
                # Uma página cheia sem cursor não prova fim do histórico.
                return rows, len(items) < 100
            if not isinstance(cursor,str) or len(cursor)>65536:
                raise InvalidResponse('Cursor de transferências Birdeye inválido; cobertura não confirmada.')
            if cursor in seen or not items:
                return rows, False
            seen.add(cursor)
        return rows, False

    def collect(self, wallet, start, end):
        if not valid_address(wallet):
            raise ValueError('Endereço Solana inválido.')
        if start >= end or end > self.clock():
            raise ValueError('Período inválido ou com informação futura.')
        coverage = Coverage(start, end, self.clock(), source='birdeye')
        provider = {'source': 'birdeye', 'pnl_method': 'wac', 'pnl_position_scope': 'duration_only', 'pnl_duration': 'all', 'pnl': [], 'holdings': [],
                    'limitations': ['Idade do token na descoberta refere listagem Birdeye, não criação do mint.',
                                     'Plataforma pump.fun é uma aproximação de launchpad; não confirma categoria memecoin.']}
        events = []
        complete = {}
        cursor_key = 'trader:' + wallet
        start_key='trader-start:'+wallet
        covered_start=self.store.get_cursor(start_key)
        advance_cursor = False
        update_covered_start = True
        if covered_start:
            try:
                update_covered_start = start < parse_time(covered_start)
            except (ValueError, TypeError):
                coverage.failures.append('Cursor histórico inválido; recolha integral repetida.')
        try:
            rows, done, _ = self._paged('/trader/txs/seek_by_time', 'GET', {
                'address': wallet, 'before_time': int(end.timestamp()), 'tx_type': 'all', 'ui_amount_mode': 'raw'}, 'items')
            complete['trades'] = done
            tx_instructions = {}
            trader_errors = False
            for row in rows:
                try:
                    if row.get('owner') and row['owner'] != wallet:
                        trader_errors = True
                        coverage.failures.append('Transação de outra carteira; excluída.')
                        continue
                    stamp = _timestamp(row)
                    # A API real aceita apenas um limite temporal. Pedir o
                    # limite superior impede dados futuros; o inferior é local.
                    # Sem ordem documentada, linhas antigas não terminam páginas.
                    if stamp < start:
                        continue
                    if stamp > end:
                        trader_errors = True
                        coverage.failures.append('Transação fora do período; excluída da análise.')
                        continue
                    tx = str(row.get('tx_hash', ''))
                    tx_instructions.setdefault(tx, set()).add((row.get('ins_index'), row.get('inner_ins_index')))
                    events.extend(normalize_trade(row, wallet))
                except (ValueError, TypeError, OverflowError):
                    trader_errors = True
                    coverage.failures.append('Transação não interpretável; cobertura insuficiente.')
            events = [replace(event, multi_step=True) if len(tx_instructions.get(event.tx_hash, ())) > 1 else event
                      for event in events]
            advance_cursor = done and not trader_errors
        except BirdeyeError as exc:
            coverage.failures.append(str(exc))
            complete['trades'] = False
        try:
            rows, done = self._transfers(wallet, start, end)
            complete['transfers'] = done
            trade_hashes = {event.tx_hash for event in events if event.tx_hash}
            for row in rows:
                try:
                    event = normalize_transfer(row, wallet)
                    if not start <= event.timestamp <= end:
                        coverage.failures.append('Transferência fora do período; excluída.')
                        continue
                    # Sem mapeamento de instruções seguro, não contar as mesmas pernas
                    # como transferência E compra. Bloquear contabilidade desta tx.
                    if event.tx_hash in trade_hashes:
                        provider['limitations'].append('Transferência e swap sobrepostos; instruções não reconciliadas.')
                        events = [replace(x, multi_step=True) if x.tx_hash == event.tx_hash else x for x in events]
                    else:
                        events.append(event)
                except (ValueError, TypeError, OverflowError):
                    coverage.failures.append('Transferência não interpretável.')
        except BirdeyeError as exc:
            coverage.failures.append(str(exc))
            complete['transfers'] = False
        try:
            pnl, done, meta = self._paged('/wallet/v2/pnl/details', 'POST', {
                'wallet': wallet, 'duration': 'all', 'pnl_method': 'wac',
                'position_scope': 'duration_only', 'sort_by': 'last_trade', 'sort_type': 'desc'}, 'tokens')
            provider['pnl'], provider['pnl_meta'] = pnl, {k: v for k, v in meta.items() if k != 'tokens'}
            complete['pnl'] = done
        except BirdeyeError as exc:
            coverage.failures.append(str(exc))
            complete['pnl'] = False
        try:
            holdings, done, meta = self._paged('/wallet/v2/current-net-worth', 'GET', {
                'wallet': wallet, 'flags': 'include_low_liquidity', 'sort_type': 'desc',
                'sort_by': 'value'}, 'items')
            provider['holdings'], provider['holdings_time'] = holdings, meta.get('current_timestamp')
            complete['holdings'] = done
        except BirdeyeError as exc:
            coverage.failures.append(str(exc))
            complete['holdings'] = False
        # Paginação esgotada != garantia de retenção nem de inventário inicial.
        coverage.truncated = not all(complete.values())
        coverage.complete = False
        coverage.all_tokens = False
        coverage.failures.append('Retenção integral e universo de tokens não garantidos pelos contratos Standard; cobertura indeterminada.')
        provider['pages_complete'] = complete
        provider['limitations'].extend([
            'PnL Birdeye usa WAC e duration=all: informativo; não substituir FIFO nem o período de seleção.',
            'Taxas históricas em USD e inventário inicial não comprovados.',
            'Preços indicativos de holdings não demonstram liquidez de saída, slippage ou execução reproduzível.',
            'Etiquetas, coordenação, MEV e cobertura de transações falhadas não comprovados.',
        ])
        old_events = {event.id: event for event in self.store.get_events(wallet, start, end)}
        events = [replace(event, multi_step=True) if event.id in old_events and old_events[event.id].multi_step else event
                  for event in events]
        self.store.put_events(events)
        persisted = self.store.get_events(wallet, start, end)
        # Cursor só avança DEPOIS dos eventos estarem gravados. Crash antes disto
        # apenas repete consultas deduplicáveis, nunca salta eventos por gravar.
        if advance_cursor:
            self.store.set_cursor(cursor_key, iso(end))
            if update_covered_start:
                self.store.set_cursor(start_key,iso(start))
        provider['discovery_labels']=self.store.discovery_evidence(wallet,end)
        # Enriquecimento limitado: taxas observadas em lamports, separado do FIFO.
        detail_limit=self.config.get('api',{}).get('max_transaction_details_per_wallet',0)
        if detail_limit:
            from .transaction_details import enrich_details
            provider['transaction_details']=enrich_details(self.client,persisted,end,detail_limit)
        # Reconciliar multistep flags também em duplicados atualizados pelo Store.
        return persisted, coverage, provider
