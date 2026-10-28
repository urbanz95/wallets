"""Contrato Helius Free exercitado só com transporte simulado e sem rede."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
import json
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from wallets.helius import (HeliusClient, HeliusError, AccessDenied,
                            BudgetExceeded, InvalidResponse, _NoRedirect, request_cost)

NOW = datetime(2026, 10, 28, tzinfo=timezone.utc)
KEY = 'mock-helius-key-never-real'
ADDRESS = 'So11111111111111111111111111111111111111112'


class FakeStore:
    def __init__(self):
        self.cache, self.costs = {}, []
    def cache_get(self, key, now):
        entry=self.cache.get(key)
        return entry[0] if entry and entry[1]>now else None
    def cache_put(self, key, value, expiry):self.cache[key]=(value,expiry)
    def reserve_budget(self,month,cost,ceiling,namespace='birdeye'):
        used=sum(item[1] for item in self.costs if item[0]==namespace)
        if used+cost>ceiling:return False
        self.costs.append((namespace,cost))
        return True


def envelope(data=None, **extra):
    return {'jsonrpc':'2.0','id':1,'result':{'data':[] if data is None else data,**extra}}


class HeliusClientTests(unittest.TestCase):
    def setUp(self):
        self.block_network=patch('socket.socket.connect',side_effect=AssertionError('Rede proibida'))
        self.block_network.start();self.addCleanup(self.block_network.stop)
    def client(self,transport=None,**overrides):
        self.store=FakeStore();self.sleeps=[]
        config=dict(max_requests_per_run=150,max_credits_per_run=5000,monthly_credit_ceiling=900000,
                    max_retries=2,requests_per_second=5,cache_ttl_seconds=900)
        config.update(overrides)
        self.transport=transport or Mock(return_value=envelope())
        return HeliusClient(KEY,self.store,config,transport=self.transport,clock=lambda:NOW,sleep=self.sleeps.append)
    def full(self,limit=1000):return [ADDRESS,{'transactionDetails':'full','limit':limit,'sortOrder':'asc'}]

    def test_fixed_host_jsonrpc_and_escaped_key(self):
        client=self.client();client.api_key=KEY+'&host=private'
        self.assertEqual(client.request('getTransactionsForAddress',self.full(1)),{'data':[]})
        request=self.transport.call_args.args[0]
        self.assertEqual(request.get_method(),'POST')
        parsed=urlsplit(request.full_url)
        self.assertEqual((parsed.scheme,parsed.hostname,parsed.path),('https','mainnet.helius-rpc.com','/'))
        self.assertEqual(parse_qs(parsed.query),{'api-key':[KEY+'&host=private']})
        payload=json.loads(request.data)
        self.assertEqual(payload,{'jsonrpc':'2.0','id':1,'method':'getTransactionsForAddress','params':self.full(1)})
        self.assertTrue(client.network_verified)

    def test_cache_provider_namespace_never_persists_key(self):
        client=self.client()
        client.request('getTransactionsForAddress',self.full(1));client.request('getTransactionsForAddress',self.full(1))
        self.transport.assert_called_once();self.assertEqual(client.credits,10)
        self.assertTrue(all(key.startswith('helius:') for key in self.store.cache))
        self.assertNotIn(KEY,repr(self.store.cache))
        self.assertEqual(self.store.costs,[('helius',10)])

    def test_worst_case_metering_and_no_refund_for_empty_results(self):
        for limit,expected in [(1,10),(100,10),(101,20),(250,30),(1000,100)]:
            self.assertEqual(request_cost('getTransactionsForAddress',self.full(limit)),expected)
        self.assertEqual(request_cost('getTransactionsForAddress',[ADDRESS]),10)
        client=self.client();client.request('getTransactionsForAddress',self.full(1000))
        self.assertEqual(client.credits,100)
        self.assertEqual(self.store.costs,[('helius',100)])

    def test_only_confirmed_read_method_allowed(self):
        client=self.client()
        for method in ('sendTransaction','sendRawTransaction','simulateBundle','getTransfersByAddress','https://other/rpc'):
            with self.assertRaises(AccessDenied):client.request(method,self.full())
        self.transport.assert_not_called()
        self.assertEqual(client.requests,0)

    def test_invalid_request_cannot_underreserve(self):
        client=self.client()
        invalids=[[],[ADDRESS,None],[ADDRESS,{'limit':True}],[ADDRESS,{'limit':0}],
                  [ADDRESS,{'limit':1001}],[ADDRESS,{'limit':'100'}],
                  [ADDRESS,{'transactionDetails':'everything'}],[ADDRESS,{'sortOrder':'invalid'}]]
        for params in invalids:
            with self.assertRaises(ValueError):client.request('getTransactionsForAddress',params)
        self.transport.assert_not_called()

    def test_missing_key_never_networks(self):
        client=self.client();client.api_key=''
        with self.assertRaises(AccessDenied):client.request('getTransactionsForAddress',self.full())
        self.assertEqual(client.requests,0);self.transport.assert_not_called()

    def test_run_requests_and_credits_limit_before_network(self):
        client=self.client(max_requests_per_run=1)
        client.request('getTransactionsForAddress',self.full(1),cache=False)
        with self.assertRaises(BudgetExceeded):client.request('getTransactionsForAddress',self.full(1),cache=False)
        self.assertEqual(client.requests,1)
        client=self.client(max_credits_per_run=99)
        with self.assertRaises(BudgetExceeded):client.request('getTransactionsForAddress',self.full(1000))
        self.transport.assert_not_called()

    def test_monthly_namespace_budget_is_separate(self):
        client=self.client(monthly_credit_ceiling=100,max_credits_per_run=100)
        self.store.costs.append(('birdeye',999999))
        client.request('getTransactionsForAddress',self.full(1000),cache=False)
        other=HeliusClient(KEY,self.store,dict(client.config,max_credits_per_run=100),transport=self.transport,clock=lambda:NOW,sleep=lambda _:None)
        with self.assertRaises(BudgetExceeded):other.request('getTransactionsForAddress',self.full(1),cache=False)
        self.assertEqual(other.requests,0)

    def test_http_authorization_errors_redacted_and_halt(self):
        for status in (400,401,402,403,404):
            def transport(request):raise HTTPError(request.full_url,status,KEY,{},BytesIO(KEY.encode()))
            client=self.client(transport)
            with self.assertRaises(AccessDenied) as error:client.request('getTransactionsForAddress',self.full())
            self.assertNotIn(KEY,str(error.exception));self.assertNotIn('api-key',str(error.exception))
            with self.assertRaises(AccessDenied):client.request('getTransactionsForAddress',self.full(1))
            self.assertEqual(client.requests,1)

    def test_jsonrpc_error_redacted_and_halts(self):
        transport=Mock(return_value={'jsonrpc':'2.0','id':1,'error':{'code':-32000,'message':KEY}})
        client=self.client(transport)
        with self.assertRaises(AccessDenied) as error:client.request('getTransactionsForAddress',self.full())
        self.assertNotIn(KEY,str(error.exception))
        with self.assertRaises(AccessDenied):client.request('getTransactionsForAddress',self.full())
        transport.assert_called_once();self.assertFalse(client.network_verified)

    def test_jsonrpc_envelope_result_and_records_checked(self):
        bad=[{}, {'jsonrpc':'1.0','id':1,'result':{'data':[]}},
             {'jsonrpc':'2.0','id':2,'result':{'data':[]}}, {'jsonrpc':'2.0','id':1,'result':None},
             envelope([None]),envelope([{},{}]),envelope(paginationToken=123)]
        for result in bad:
            client=self.client(Mock(return_value=result))
            with self.assertRaises(InvalidResponse):client.request('getTransactionsForAddress',self.full(1))
            self.assertFalse(client.network_verified)

    def test_successful_page_preserves_cursor_and_full_raw_data(self):
        raw={'slot':123,'transactionIndex':0,'blockTime':int(NOW.timestamp()),'meta':{'fee':5000}}
        client=self.client(Mock(return_value=envelope([raw],paginationToken='123:0')))
        result=client.request('getTransactionsForAddress',self.full(1))
        self.assertEqual(result,{'data':[raw],'paginationToken':'123:0'})

    def test_retry_reserves_each_attempt_and_bounds_delay(self):
        calls=[]
        def transport(request):
            calls.append(request)
            if len(calls)==1:raise HTTPError(request.full_url,429,KEY,{'Retry-After':'9999'},None)
            return envelope()
        client=self.client(transport)
        client.request('getTransactionsForAddress',self.full(1000))
        self.assertEqual(client.credits,200);self.assertEqual(client.requests,2)
        self.assertEqual(self.store.costs,[('helius',100),('helius',100)])
        self.assertIn(30,self.sleeps);self.assertTrue(all(0<=delay<=30 for delay in self.sleeps))

    def test_exhausted_429_stops_future_calls(self):
        def transport(request):raise HTTPError(request.full_url,429,KEY,{},None)
        client=self.client(transport,max_retries=1)
        with self.assertRaises(HeliusError):client.request('getTransactionsForAddress',self.full(1))
        with self.assertRaises(AccessDenied):client.request('getTransactionsForAddress',self.full(1))
        self.assertEqual(client.requests,2)

    def test_network_error_sanitized_and_retries_bounded(self):
        for failure in (URLError(KEY),TimeoutError(KEY),json.JSONDecodeError(KEY,KEY,0)):
            client=self.client(Mock(side_effect=failure),max_retries=1)
            with self.assertRaises(HeliusError) as error:client.request('getTransactionsForAddress',self.full(1))
            self.assertNotIn(KEY,str(error.exception));self.assertEqual(client.requests,2)

    def test_retry_cannot_exceed_pre_reserved_run_budget(self):
        def transport(request):raise HTTPError(request.full_url,503,KEY,{},None)
        client=self.client(transport,max_credits_per_run=100)
        with self.assertRaises(BudgetExceeded):client.request('getTransactionsForAddress',self.full(1000))
        self.assertEqual(client.requests,1);self.assertEqual(client.credits,100)

    def test_invalid_free_configuration_rejected_before_network(self):
        for options in ({'monthly_credit_ceiling':1000001},{'requests_per_second':10},
                        {'requests_per_second':float('nan')},{'max_retries':99},
                        {'max_requests_per_run':False},{'cache_ttl_seconds':-1}):
            with self.assertRaises(ValueError):self.client(**options)

    def test_redirect_blocked_before_forwarding_credentials(self):
        with self.assertRaises(AccessDenied) as error:
            _NoRedirect().redirect_request(None,None,302,KEY,{},'https://other.example')
        self.assertNotIn(KEY,str(error.exception))

    def test_http_parser_keeps_decimal_and_checks_size(self):
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.return_value=b'{"jsonrpc":"2.0","id":1,"result":{"data":[],"value":0.1234567890123456789}}'
        opener=Mock();opener.open.return_value=response
        with patch('wallets.helius.build_opener',return_value=opener):
            result=HeliusClient._http(Mock())
        self.assertEqual(result['result']['value'],Decimal('0.1234567890123456789'))
        with patch('wallets.helius.MAX_RESPONSE_BYTES',3),patch('wallets.helius.build_opener',return_value=opener):
            with self.assertRaises(InvalidResponse):HeliusClient._http(Mock())
