"""Exercita o envio SÓ com mocks; rede bloqueada mesmo perante uma regressão.

Os resultados sintéticos aprovados são rotulados ``birdeye`` exclusivamente neste
módulo para testar o gate. Não são dados reais, nem podem sair deste teste.
"""
from contextlib import redirect_stdout
from copy import deepcopy
from dataclasses import replace, asdict
from datetime import timedelta
from decimal import Decimal
from io import StringIO
import json
from hashlib import sha256
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_checks import CONFIG, NOW, full_case
from wallets.checks import evaluate
from wallets.models import SOL, Status, dumps
from wallets.storage import Store
from wallets.telegram import (Notifier, eligible_for_delivery, format_message,
                              telegram_transport)

ADDRESS = SOL
OTHER_ADDRESS = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
TOKEN = 'mock-token-never-a-real-credential'
CHAT = 'mock-private-chat'


def mock_approved(*, at=NOW, config=None, wallet=ADDRESS, start=None, source='birdeye'):
    """Dados fictícios; nunca chamar um transporte real com este resultado."""
    config = deepcopy(config or CONFIG)
    events, coverage, results = full_case()
    delta = at-NOW
    events = [replace(event, id=event.id+'@'+at.isoformat(), tx_hash=event.tx_hash+'@'+at.isoformat(), wallet=wallet, timestamp=event.timestamp+delta, source=source)
              for event in events]
    coverage.start = start or coverage.start+delta
    coverage.end += delta
    coverage.fetched_at += delta
    coverage.source = source
    for token in results:
        token.weekly = {
            ((NOW.fromisoformat(week)+delta-timedelta(days=(NOW.fromisoformat(week)+delta).weekday())).date().isoformat()): value
            for week, value in token.weekly.items()
        }
    result = evaluate(wallet, events, coverage, results, [], config, now=at)
    if not result.eligible:
        raise AssertionError([(check.name, check.status.value, check.reason) for check in result.checks])
    result._test_events = events
    return result


class TelegramTests(unittest.TestCase):
    def setUp(self):
        # Nenhuma falha de mock pode contactar Telegram (nem qualquer outro host).
        self.no_http = patch('urllib.request.urlopen', side_effect=AssertionError('Rede proibida nos testes'))
        self.no_socket = patch('socket.socket.connect', side_effect=AssertionError('Sockets proibidos nos testes'))
        self.no_opener = patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('HTTP proibido nos testes'))
        self.no_opener.start(); self.addCleanup(self.no_opener.stop)
        self.no_http.start(); self.no_socket.start()
        self.addCleanup(self.no_http.stop); self.addCleanup(self.no_socket.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name)/'state.sqlite'
        self.store = Store(self.path)
        self.addCleanup(lambda: self.store.close() if self.store else None)
        self.transport = Mock(return_value='mock-message-id')
        self.checkpoint = Mock()
        self.config = deepcopy(CONFIG)

    def persist(self, result):
        if hasattr(result, '_test_events'):
            self.store.put_events(result._test_events)
            # Fotografias financeiras sintéticas: o gate deve estar ancorado em
            # todos os eventos persistidos, inclusive os da janela sobreposta.
            observed=self.store.get_events(result.wallet,result.coverage.start,result.coverage.end)
            result.metrics['_events_hash']=sha256(dumps([asdict(e) for e in sorted(observed,key=lambda e:(e.timestamp,e.id))]).encode()).hexdigest()
        self.store.save_evaluation(result)
        return result

    def notifier(self, **kwargs):
        options = dict(checkpoint=self.checkpoint, transport=self.transport, clock=lambda: NOW)
        options.update(kwargs)
        return Notifier(self.store, self.config, TOKEN, CHAT, **options)

    def reopen(self):
        self.store.close(); self.store = Store(self.path)

    def test_all_mandatory_checks_pass_can_send_to_configured_chat_with_mock(self):
        result = self.persist(mock_approved())
        self.assertTrue(eligible_for_delivery(result, self.config, NOW))
        notifier = self.notifier()
        self.assertTrue(notifier.send(result))
        self.transport.assert_called_once_with(TOKEN, CHAT, format_message(result))
        self.assertEqual(self.store.last_notification(ADDRESS)['state'], 'sent')
        self.assertEqual(notifier.attempts, 1)
        self.assertEqual(self.checkpoint.call_count, 2)

    def test_one_failed_mandatory_check_never_sends(self):
        result = mock_approved(); result.checks[3].status = Status.FAIL
        self.persist(result)
        self.assertFalse(self.notifier().send(result))
        self.transport.assert_not_called()
        self.assertIsNone(self.store.last_notification(ADDRESS))

    def test_one_indeterminate_mandatory_check_never_sends(self):
        result = mock_approved(); result.checks[5].status = Status.UNKNOWN
        self.persist(result)
        self.assertFalse(self.notifier().send(result))
        self.transport.assert_not_called()

    def test_fixture_source_always_blocks_even_all_checks_pass(self):
        result = self.persist(mock_approved(source='fixture'))
        self.assertTrue(result.eligible)
        self.assertFalse(eligible_for_delivery(result, self.config, NOW))
        self.assertFalse(self.notifier().send(result))
        self.transport.assert_not_called()

    def test_mixed_fixture_coverage_and_real_label_always_blocks(self):
        result = mock_approved(); result.coverage.source = 'fixture'
        self.persist(result)
        self.assertFalse(self.notifier().send(result))
        self.transport.assert_not_called()

    def test_old_approval_and_old_data_do_not_authorize_new_message(self):
        result = self.persist(mock_approved())
        later = NOW+timedelta(hours=25)
        self.assertFalse(self.notifier(clock=lambda: later).send(result))
        self.transport.assert_not_called()

    def test_expired_evaluation_independently_blocks(self):
        result = self.persist(mock_approved())
        self.config['max_data_age_hours'] = 72
        self.config['max_evaluation_age_hours'] = 1
        result = self.persist(mock_approved(config=self.config))
        self.assertFalse(self.notifier(clock=lambda: NOW+timedelta(hours=2)).send(result))
        self.transport.assert_not_called()

    def test_future_evaluation_blocks(self):
        result = self.persist(mock_approved(at=NOW+timedelta(seconds=1)))
        self.assertFalse(self.notifier().send(result))
        self.transport.assert_not_called()

    def test_empty_store_sends_nothing_through_cli(self):
        from wallets.cli import notify_from_store
        notifier = self.notifier()
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': TOKEN, 'TELEGRAM_CHAT_ID': CHAT}), \
             patch('wallets.cli.Notifier', return_value=notifier), redirect_stdout(StringIO()):
            self.assertEqual(notify_from_store(self.store, self.config, self.checkpoint), 0)
        self.transport.assert_not_called()
        self.checkpoint.assert_not_called()

    def test_duplicate_message_blocked_across_database_reopen(self):
        result = self.persist(mock_approved())
        self.assertTrue(self.notifier().send(result))
        self.reopen()
        restored = self.store.get_evaluation(result.id)
        self.assertFalse(self.notifier().send(restored))
        self.transport.assert_called_once()

    def test_new_snapshot_within_cooldown_does_not_repeat(self):
        first = self.persist(mock_approved())
        self.assertTrue(self.notifier().send(first))
        next_time = NOW+timedelta(days=1)
        second = self.persist(mock_approved(at=next_time))
        self.assertFalse(self.notifier(clock=lambda: next_time).send(second))
        self.transport.assert_called_once()

    def test_cooldown_alone_is_insufficient_if_analysis_period_overlaps(self):
        first = self.persist(mock_approved())
        self.assertTrue(self.notifier().send(first))
        next_time = NOW+timedelta(days=31)
        second = self.persist(mock_approved(at=next_time, start=NOW-timedelta(days=1)))
        self.assertFalse(self.notifier(clock=lambda: next_time).send(second))
        self.transport.assert_called_once()

    def test_nonoverlapping_period_after_cooldown_can_notify_with_mock(self):
        first = self.persist(mock_approved())
        self.assertTrue(self.notifier().send(first))
        next_time = NOW+timedelta(days=31)
        second = self.persist(mock_approved(at=next_time))
        self.assertGreaterEqual(second.coverage.start, first.coverage.end)
        self.assertTrue(self.notifier(clock=lambda: next_time).send(second))
        self.assertEqual(self.transport.call_count, 2)

    def test_same_version_changed_effective_criteria_blocks(self):
        result = self.persist(mock_approved())
        self.config['min_tokens'] += 1
        self.assertFalse(self.notifier().send(result))
        self.transport.assert_not_called()

    def test_tampered_criteria_snapshot_blocks_even_hash_unchanged(self):
        result = mock_approved(); result.metrics['_criteria_config']['min_tokens'] = 1
        self.persist(result)
        self.assertFalse(self.notifier().send(result))
        self.transport.assert_not_called()

    def test_revoked_latest_state_prevents_old_approval_sending(self):
        result = self.persist(mock_approved())
        revoked = deepcopy(result); revoked.evaluated_at += timedelta(seconds=1)
        revoked.checks[0].status = Status.FAIL
        self.persist(revoked)
        self.assertNotEqual(self.store.latest_id(ADDRESS), result.id)
        self.assertFalse(self.notifier(clock=lambda: NOW+timedelta(seconds=1)).send(result))
        self.transport.assert_not_called()

    def test_same_timestamp_revocation_dominates_prior_approval(self):
        result = self.persist(mock_approved())
        revoked = deepcopy(result); revoked.checks[0].status = Status.FAIL
        self.persist(revoked)
        self.assertEqual(self.store.latest_id(ADDRESS), revoked.id)
        self.assertFalse(self.notifier().send(result))
        self.transport.assert_not_called()

    def test_checkpoint_failure_stops_before_http_and_survives_restart(self):
        result = self.persist(mock_approved())
        self.checkpoint.side_effect = RuntimeError('falha simulada de persistência')
        with self.assertRaises(RuntimeError):
            self.notifier().send(result)
        self.transport.assert_not_called()
        self.assertEqual(self.store.last_notification(ADDRESS)['state'], 'pending')
        self.reopen(); self.checkpoint.side_effect = None
        self.assertFalse(self.notifier().send(self.store.get_evaluation(result.id)))
        self.transport.assert_not_called()

    def test_checkpoint_delay_rechecks_freshness_before_http(self):
        result = self.persist(mock_approved())
        clock = Mock(side_effect=[NOW, NOW+timedelta(hours=25)])
        self.assertFalse(self.notifier(clock=clock).send(result))
        self.transport.assert_not_called()
        state = self.store.db.execute('SELECT state FROM notifications').fetchone()[0]
        self.assertEqual(state, 'cancelled')
        self.assertEqual(self.checkpoint.call_count, 2)

    def test_checkpoint_revocation_rechecks_latest_before_http(self):
        result = self.persist(mock_approved())
        revoked = deepcopy(result); revoked.checks[0].status = Status.UNKNOWN
        called = []
        def checkpoint():
            if not called:
                called.append(True); self.persist(revoked)
        self.assertFalse(self.notifier(checkpoint=checkpoint).send(result))
        self.transport.assert_not_called()
        self.assertEqual(self.store.latest_id(ADDRESS), revoked.id)

    def test_uncertain_network_delivery_is_never_retried_indefinitely(self):
        result = self.persist(mock_approved())
        self.transport.side_effect = TimeoutError('resposta perdida após possível entrega')
        self.assertFalse(self.notifier().send(result))
        self.assertEqual(self.store.last_notification(ADDRESS)['state'], 'uncertain')
        self.reopen()
        self.assertFalse(self.notifier().send(self.store.get_evaluation(result.id)))
        next_time = NOW+timedelta(days=31)
        second = self.persist(mock_approved(at=next_time))
        self.assertFalse(self.notifier(clock=lambda: next_time).send(second))
        self.transport.assert_called_once()

    def test_daily_attempt_limit_applies_to_different_wallets(self):
        self.config['max_notifications_per_run'] = 1
        first = self.persist(mock_approved(config=self.config))
        second = self.persist(mock_approved(config=self.config, wallet=OTHER_ADDRESS))
        notifier = self.notifier()
        self.assertTrue(notifier.send(first))
        self.assertFalse(notifier.send(second))
        self.assertEqual(notifier.attempts, 1)
        self.transport.assert_called_once()

    def test_missing_telegram_credentials_no_messages_or_outbox_reservation(self):
        result = self.persist(mock_approved())
        for token, chat in [('', CHAT), (TOKEN, ''), (None, None)]:
            notifier = Notifier(self.store, self.config, token, chat, checkpoint=self.checkpoint,
                                transport=self.transport, clock=lambda: NOW)
            self.assertFalse(notifier.send(result))
        self.transport.assert_not_called()
        self.assertIsNone(self.store.last_notification(ADDRESS))

    def test_unsaved_or_modified_evaluation_cannot_send(self):
        result = mock_approved()
        self.assertFalse(self.notifier().send(result))
        self.persist(result); result.metrics['realized_total'] = Decimal('999999999')
        self.assertFalse(self.notifier().send(result))
        self.transport.assert_not_called()

    def test_pending_checkpoint_precedes_http_and_sent_checkpoint_follows(self):
        result = self.persist(mock_approved())
        order = []
        def checkpoint():
            order.append('checkpoint:'+self.store.last_notification(ADDRESS)['state'])
        def transport(*args):
            order.append('http'); return 'fake-id'
        self.assertTrue(self.notifier(checkpoint=checkpoint, transport=transport).send(result))
        self.assertEqual(order, ['checkpoint:pending', 'http', 'checkpoint:sent'])

    def test_message_contains_full_address_costs_concentration_and_lisbon_dates(self):
        result = mock_approved()
        message = format_message(result)
        self.assertIn('<code>'+ADDRESS+'</code>', message)
        self.assertIn('Rede: Solana', message)
        self.assertIn('Tokens distintos avaliados: 20', message)
        self.assertIn('Realizado recalculado: 200.00 USD', message)
        self.assertIn('Custos: FIFO', message)
        self.assertIn('taxas verificados', message)
        self.assertIn('Sem melhor token (realizado + aberto marcado): 190.00 USD', message)
        self.assertIn('melhor 5.0%; três melhores 15.0%', message)
        self.assertIn('perdas abertas: 0.00 USD', message)
        self.assertIn('não confirma identidade humana', message)
        self.assertIn('30/09/2026 13:00 WEST', message)
        self.assertIn('28/10/2026 12:00 WET', message)
        self.assertIn('https://solscan.io/account/'+ADDRESS, message)
        self.assertIn('https://birdeye.so/profile/'+ADDRESS+'?chain=solana', message)
        self.assertLessEqual(len(message), 4096)

    def test_message_escapes_html(self):
        result = mock_approved(); result.wallet = '<wallet&"bad>'
        result.criteria_version = 'v<&'
        message = format_message(result)
        self.assertIn('&lt;wallet&amp;&quot;bad&gt;', message)
        self.assertIn('v&lt;&amp;', message)
        self.assertNotIn('<wallet', message)

    def test_transport_payload_uses_only_configured_chat_and_does_not_retry(self):
        response = Mock()
        response.read.return_value = b'{"ok":true,"result":{"message_id":123}}'
        response.__enter__ = Mock(return_value=response); response.__exit__ = Mock(return_value=False)
        with patch('wallets.telegram.urllib.request.OpenerDirector.open', return_value=response) as http:
            self.assertEqual(telegram_transport(TOKEN, CHAT, 'mensagem fictícia só no mock'), '123')
        http.assert_called_once()
        request = http.call_args.args[0]
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.full_url, 'https://api.telegram.org/bot'+TOKEN+'/sendMessage')
        payload = json.loads(request.data)
        self.assertEqual(payload['chat_id'], CHAT)
        self.assertEqual(payload['parse_mode'], 'HTML')
        self.assertEqual(payload['link_preview_options'], {'is_disabled':True})

    def test_transport_rejection_or_timeout_no_retry(self):
        response = Mock()
        response.read.return_value = b'{"ok":false,"description":"mock rejection"}'
        response.__enter__ = Mock(return_value=response); response.__exit__ = Mock(return_value=False)
        with patch('wallets.telegram.urllib.request.OpenerDirector.open', return_value=response) as http:
            with self.assertRaises(RuntimeError):telegram_transport(TOKEN, CHAT, 'mock')
            http.assert_called_once()
        with patch('wallets.telegram.urllib.request.OpenerDirector.open', side_effect=TimeoutError) as http:
            with self.assertRaises(TimeoutError):telegram_transport(TOKEN, CHAT, 'mock')
            http.assert_called_once()

    def test_changed_events_before_reservation_block_delivery(self):
        result=self.persist(mock_approved())
        event=result._test_events[0]
        self.store.put_events([replace(event,multi_step=True)])
        self.assertFalse(self.notifier().send(result))
        self.transport.assert_not_called()
        self.assertIsNone(self.store.last_notification(ADDRESS))

    def test_changed_events_during_checkpoint_block_http(self):
        result=self.persist(mock_approved())
        def checkpoint():
            self.store.put_events([replace(result._test_events[0],multi_step=True)])
        self.assertFalse(self.notifier(checkpoint=checkpoint).send(result))
        self.transport.assert_not_called()
