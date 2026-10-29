import unittest
import json
from datetime import datetime, timezone
from io import BytesIO
from urllib.error import HTTPError, URLError
from wallets.birdeye import BirdeyeClient, AccessDenied, BudgetExceeded, BirdeyeError, InvalidResponse

NOW = datetime(2026, 10, 28, tzinfo=timezone.utc)

class Store:
    def __init__(self):
        self.cache, self.costs = {}, []
    def cache_get(self, key, now):
        entry = self.cache.get(key)
        return entry[0] if entry and entry[1] > now else None
    def cache_put(self, key, value, expiry): self.cache[key] = (value, expiry)
    def reserve_budget(self, month, cost, ceiling):
        if sum(self.costs) + cost > ceiling: return False
        self.costs.append(cost)
        return True

class ClientTests(unittest.TestCase):
    def client(self, transport, **overrides):
        self.store = Store()
        self.sleeps = []
        config = {'max_requests_per_run': 40, 'max_credits_per_run': 2000,
                  'monthly_credit_ceiling': 28000, 'max_retries': 2,
                  'requests_per_second': 1, 'cache_ttl_seconds': 900}
        config.update(overrides)
        return BirdeyeClient('secret-never-log', self.store, config, transport=transport,
                             clock=lambda: NOW, sleep=self.sleeps.append)

    def test_cache_and_allowlist(self):
        requests = []
        client = self.client(lambda request: requests.append(request) or {'success': True, 'data': {}})
        for _ in range(2): client.request('GET', '/defi/v3/token/list', {'limit': 1})
        self.assertEqual(len(requests), 1)
        self.assertEqual(client.credits, 50)
        self.assertEqual(requests[0].host, 'public-api.birdeye.so')
        self.assertEqual(requests[0].get_header('X-chain'), 'solana')
        with self.assertRaises(AccessDenied): client.request('GET', 'https://private.example/data')

    def test_401_stops_all_endpoints_and_redacts(self):
        def transport(request):
            raise HTTPError(request.full_url, 401, 'secret-never-log', {}, BytesIO(b'secret-never-log'))
        client = self.client(transport)
        with self.assertRaises(AccessDenied) as result:
            client.request('GET', '/defi/v3/token/list')
        self.assertNotIn('secret-never-log', str(result.exception))
        with self.assertRaises(AccessDenied): client.request('POST', '/wallet/v2/pnl/details')
        self.assertEqual(client.requests, 1)

    def test_retry_consumes_budget_and_rate_limited(self):
        calls = []
        def transport(request):
            calls.append(request)
            if len(calls) == 1:
                raise HTTPError(request.full_url, 429, 'quota', {'Retry-After': '999'}, None)
            return {'success': True, 'data': {}}
        client = self.client(transport)
        client.request('GET', '/defi/v3/token/list')
        self.assertEqual(client.requests, 2)
        self.assertEqual(sum(self.store.costs), 100)
        self.assertIn(30, self.sleeps)
        self.assertTrue(all(0 <= x <= 30 for x in self.sleeps))

    def test_run_and_monthly_quota(self):
        client = self.client(lambda request: {'success': True, 'data': {}}, max_credits_per_run=49)
        with self.assertRaises(BudgetExceeded): client.request('GET', '/defi/v3/token/list')
        self.assertEqual(client.requests, 0)
        client = self.client(lambda request: {'success': True, 'data': {}}, monthly_credit_ceiling=49)
        with self.assertRaises(BudgetExceeded): client.request('GET', '/defi/v3/token/list')

    def test_failure_is_not_empty_success(self):
        client = self.client(lambda request: {'success': False, 'message': 'secret-never-log'})
        with self.assertRaises(InvalidResponse) as result: client.request('GET', '/defi/v3/token/list')
        self.assertNotIn('secret-never-log', str(result.exception))

    def test_network_failure_retries_bounded(self):
        def transport(request): raise URLError('secret-never-log')
        client = self.client(transport)
        with self.assertRaises(BirdeyeError) as result: client.request('GET', '/defi/v3/token/list')
        self.assertEqual(client.requests, 3)
        self.assertNotIn('secret-never-log', str(result.exception))

    def test_missing_key_never_queries(self):
        client = self.client(lambda request: self.fail('No network expected'))
        client.api_key = ''
        with self.assertRaises(AccessDenied): client.request('GET', '/defi/v3/token/list')
        self.assertEqual(client.requests, 0)

    def test_pnl_documented_optional_success(self):
        client = self.client(lambda request: {'data': {'tokens': []}})
        result = client.request('POST', '/wallet/v2/pnl/details', body={'wallet': 'A'*44})
        self.assertEqual(result['data']['tokens'], [])
        with self.assertRaises(InvalidResponse): client.request('GET', '/defi/v3/token/list')

    def test_validation_diagnostic_preserves_only_contract_names_codes_and_numeric_bounds(self):
        error_body={'detail':[
            {'loc':['query','limit'],'type':'less_than_equal','msg':'PRIVATE-MESSAGE',
             'input':'PRIVATE-INPUT','ctx':{'le':50,'private':'PRIVATE-CTX','max_length':False}},
            {'loc':['body','wallet'],'type':'string_type','input':'PRIVATE-WALLET'},
            {'loc':['query','PRIVATE-LOCATION'],'type':'bad-code/PRIVATE-TYPE'},
        ],'message':'PRIVATE-FREE-MESSAGE'}
        def transport(request):
            raise HTTPError(request.full_url,422,'PRIVATE-REASON',{},BytesIO(json.dumps(error_body).encode()))
        client=self.client(transport)
        with self.assertRaises(BirdeyeError) as failure:
            client.request('POST','/wallet/v2/pnl/details',{'limit':100},body={'wallet':'PRIVATE-WALLET'})
        text=str(failure.exception)
        self.assertIn('POST /wallet/v2/pnl/details',text)
        self.assertIn('less_than_equal',text)
        self.assertIn('"le": 50',text)
        self.assertIn('"wallet"',text)
        self.assertNotIn('PRIVATE-',text)
        self.assertNotIn('max_length',text)
        self.assertNotIn('?',text)
        self.assertEqual(client.requests,1)

    def test_validation_diagnostic_ignores_free_text_oversized_or_malformed_bodies(self):
        for body in (b'{"detail":"PRIVATE-TEXT"}',b'PRIVATE-NOT-JSON',b'x'*20000):
            with self.subTest(size=len(body)):
                def transport(request):
                    raise HTTPError(request.full_url,400,'PRIVATE-REASON',{},BytesIO(body))
                client=self.client(transport)
                with self.assertRaises(BirdeyeError) as failure:
                    client.request('GET','/trader/txs/seek_by_time',{'address':'PRIVATE-WALLET'})
                self.assertIn('GET /trader/txs/seek_by_time',str(failure.exception))
                self.assertNotIn('PRIVATE-',str(failure.exception))
                self.assertNotIn('Validação:',str(failure.exception))

    def test_validation_diagnostic_cannot_echo_api_key_in_type_or_unbounded_context(self):
        body={'detail':[{'loc':['query','limit'],'type':'secret',
                         'ctx':{'le':float('inf'),'ge':True,'maximum':10**50,'lt':3}}]}
        def transport(request):
            raise HTTPError(request.full_url,422,'PRIVATE',{},BytesIO(json.dumps(body).encode()))
        client=self.client(transport);client.api_key='secret'
        with self.assertRaises(BirdeyeError) as failure:
            client.request('GET','/wallet/v2/current-net-worth',{'limit':100})
        text=str(failure.exception)
        self.assertNotIn('secret',text)
        self.assertIn('"lt": 3',text)
        for field in ('"le"','"ge"','"maximum"'):self.assertNotIn(field,text)
