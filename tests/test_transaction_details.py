import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from wallets.birdeye import BirdeyeClient, BudgetExceeded
from wallets.models import Event
from wallets.transaction_details import ENDPOINT, enrich_details

NOW = datetime(2026, 10, 28, tzinfo=timezone.utc)


def event(signature='tx-one', **changes):
    base = Event('id-' + signature, 'A' * 44, 'B' * 44,
                 NOW - timedelta(seconds=10), 'buy', Decimal(1),
                 tx_hash=signature, source='birdeye')
    return replace(base, **changes)


def response(fee=5000, priority=1000, **changes):
    data = {'block_time': int((NOW - timedelta(seconds=10)).timestamp()),
            'fee': fee, 'priority_fee': priority, 'slot': 123}
    data.update(changes)
    return {'success': True, 'data': data}


class StubClient:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    def request(self, method, path, params):
        self.calls.append((method, path, params))
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return reply


class CacheStore:
    def __init__(self):
        self.cache, self.costs = {}, []

    def cache_get(self, key, now):
        entry = self.cache.get(key)
        return entry[0] if entry and entry[1] > now else None

    def cache_put(self, key, value, expiry):
        self.cache[key] = (value, expiry)

    def reserve_budget(self, month, cost, ceiling):
        if sum(self.costs) + cost > ceiling:
            return False
        self.costs.append(cost)
        return True


class TransactionDetailTests(unittest.TestCase):
    def test_unique_signatures_priority_is_subset_not_extra_cost(self):
        client = StubClient([response(), response(fee=8000, priority=2000)])
        result = enrich_details(client, [event(), event(id='other'), event('tx-two')], NOW)
        self.assertEqual(len(client.calls), 2)
        self.assertTrue(all(method == 'GET' and path == ENDPOINT
                            for method, path, _ in client.calls))
        self.assertEqual(client.calls[0][2], {'signature': 'tx-one'})
        fees = result['identified_network_fees']
        self.assertEqual(fees['total_lamports'], Decimal(13000))
        self.assertEqual(fees['priority_subset_lamports'], Decimal(3000))
        self.assertIsInstance(fees['total_lamports'], Decimal)
        self.assertFalse(fees['complete'])
        self.assertFalse(fees['allocated_to_accounting'])
        self.assertIsNone(fees['usd_value'])
        self.assertFalse(result['coverage_complete'])

    def test_missing_optional_status_does_not_imply_success(self):
        result = enrich_details(StubClient([response()]), [event()], NOW)
        self.assertEqual(result['details'][0]['execution_status'], 'unknown')
        self.assertIsNone(result['details'][0]['status_evidence'])
        result = enrich_details(StubClient([response(status={'err': 'custom'})]), [event()], NOW)
        self.assertEqual(result['details'][0]['execution_status'], 'unknown')
        self.assertEqual(result['details'][0]['status_evidence'], {'err': 'custom'})

    def test_future_events_and_empty_signature_never_query(self):
        client = StubClient([])
        result = enrich_details(client, [event(''), event(timestamp=NOW + timedelta(seconds=1))], NOW)
        self.assertEqual(client.calls, [])
        self.assertEqual(result['rejected_future_events'], 1)
        self.assertIsNone(result['identified_network_fees']['total_lamports'])

    def test_future_details_do_not_supply_fees_or_status(self):
        client = StubClient([response(block_time=int((NOW + timedelta(seconds=1)).timestamp()),
                                      status={'Ok': None})])
        result = enrich_details(client, [event()], NOW)
        self.assertEqual(result['details'], [])
        self.assertEqual(result['rejected_details'], 1)
        self.assertIsNone(result['identified_network_fees']['total_lamports'])

    def test_old_or_divergent_detail_time_cannot_reprice_known_signature(self):
        for timestamp in (NOW - timedelta(days=1), NOW - timedelta(seconds=1)):
            with self.subTest(timestamp=timestamp):
                result = enrich_details(StubClient([response(block_time=int(timestamp.timestamp()))]),
                                        [event()], NOW)
                self.assertEqual(result['details'], [])
                self.assertEqual(result['rejected_details'], 1)
                self.assertIsNone(result['identified_network_fees']['total_lamports'])

    def test_missing_time_cannot_establish_evidence_period(self):
        result = enrich_details(StubClient([response(block_time=None)]), [event()], NOW)
        self.assertEqual(result['details'], [])
        self.assertEqual(result['rejected_details'], 1)

    def test_capacity_and_disabled_limit_are_explicit(self):
        client = StubClient([response()])
        result = enrich_details(client, [event(), event('tx-two')], NOW, max_signatures=1)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(result['known_signatures'], 2)
        self.assertTrue(result['capacity_limited'])
        client = StubClient([])
        result = enrich_details(client, [event()], NOW, max_signatures=0)
        self.assertEqual(client.calls, [])
        self.assertTrue(result['capacity_limited'])

    def test_quota_stops_enrichment_without_approving_or_leaking_error_body(self):
        client = StubClient([response(), BudgetExceeded('sensitive-api-key-body')])
        result = enrich_details(client, [event(), event('tx-two'), event('tx-three')], NOW,
                                max_signatures=3)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(len(result['details']), 1)
        self.assertEqual(result['identified_network_fees']['total_lamports'], Decimal(5000))
        self.assertFalse(result['coverage_complete'])
        self.assertNotIn('sensitive-api-key-body', str(result))
        self.assertIn('BudgetExceeded', str(result['limitations']))

    def test_negative_fractional_or_missing_fee_stays_unknown(self):
        for fee in (None, -1, Decimal('2.5'), True, 'NaN'):
            with self.subTest(fee=fee):
                result = enrich_details(StubClient([response(fee=fee)]), [event()], NOW)
                self.assertIsNone(result['identified_network_fees']['total_lamports'])
                self.assertEqual(result['identified_network_fees']['transactions_with_known_fee'], 0)
        result = enrich_details(StubClient([response(fee=0, priority=0)]), [event()], NOW)
        self.assertEqual(result['identified_network_fees']['total_lamports'], Decimal(0))
        self.assertEqual(result['identified_network_fees']['transactions_with_known_fee'], 1)

    def test_invalid_priority_never_raises_total_fee(self):
        result = enrich_details(StubClient([response(fee=5000, priority=6000)]), [event()], NOW)
        fees = result['identified_network_fees']
        self.assertEqual(fees['total_lamports'], Decimal(5000))
        self.assertIsNone(fees['priority_subset_lamports'])

    def test_wrong_signature_and_invalid_response_are_rejected(self):
        for reply in (response(signatures=['different']), {'success': False, 'data': {}},
                      {'success': True, 'data': []}):
            with self.subTest(reply=reply):
                result = enrich_details(StubClient([reply]), [event()], NOW)
                self.assertEqual(result['details'], [])
                self.assertEqual(result['rejected_details'], 1)

    def test_standard_allowlist_cost_and_cache_reused_between_enrichments(self):
        store = CacheStore()
        network_calls = []
        config = {'max_requests_per_run': 2, 'max_credits_per_run': 20,
                  'monthly_credit_ceiling': 20, 'cache_ttl_seconds': 900}
        client = BirdeyeClient('never-print', store, config,
                               transport=lambda req: network_calls.append(req) or response(),
                               clock=lambda: NOW, sleep=lambda seconds: None)
        for _ in range(2):
            result = enrich_details(client, [event()], NOW)
            self.assertEqual(result['identified_network_fees']['total_lamports'], Decimal(5000))
        self.assertEqual(len(network_calls), 1)
        self.assertEqual(store.costs, [10])
        self.assertEqual(client.credits, 10)

    def test_real_client_budget_prevents_network(self):
        client = BirdeyeClient('never-print', CacheStore(), {'max_credits_per_run': 9},
                               transport=lambda req: self.fail('Budget must prevent network'),
                               clock=lambda: NOW)
        result = enrich_details(client, [event()], NOW)
        self.assertEqual(result['details'], [])
        self.assertIn('BudgetExceeded', str(result['limitations']))
        self.assertFalse(result['coverage_complete'])
