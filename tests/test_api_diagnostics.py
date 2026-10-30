"""Mensagens do fornecedor continuam privadas mesmo depois de redigidas."""
import base64
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
import unittest
from urllib.parse import quote, quote_plus

from wallets.api_diagnostics import extract_private_diagnostic, MAX_BODY_BYTES, MAX_MESSAGE_CHARS

KEY = 'private-Key+value/ABC=2026'


def body(message, **fields):
    return json.dumps(dict(message=message, **fields)).encode()


class PrivateApiDiagnosticTests(unittest.TestCase):
    def test_keeps_official_message_and_safe_code_without_any_other_response_fields(self):
        raw = body('Invalid value for before_time', success=False,
                   error={'code': 'INVALID_PARAMETER', 'message': 'ignore', 'input': 'PRIVATE'},
                   headers={'X-API-KEY': KEY}, request={'key': KEY}, input=KEY,
                   data={'wallet': 'PRIVATE-WALLET'})
        result = extract_private_diagnostic(raw, KEY)
        self.assertEqual(result, {'message': 'Invalid value for before_time',
                                  'error': {'code': 'INVALID_PARAMETER'}})
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_redacts_raw_urlencoded_base64_and_urlsafe_key_variants(self):
        key = 'private-Key+value/ABC=2026? é'
        variants = {key, quote(key, safe=''), quote_plus(key, safe=''),
                    base64.b64encode(key.encode()).decode(),
                    base64.urlsafe_b64encode(key.encode()).decode()}
        variants.update(value.rstrip('=') for value in tuple(variants))
        for value in variants:
            with self.subTest(variant=value):
                message = 'Invalid before_time; returned token: ' + value
                result = extract_private_diagnostic(body(message), key)
                self.assertNotIn(value, result['message'])
                self.assertIn('REDIGIDO', result['message'])
                self.assertIn('before_time', result['message'])

    def test_redacts_full_urls_auth_headers_cookies_and_bearer_credentials(self):
        messages = [
            'Request https://public-api.birdeye.so/path?api_key=another-secret failed',
            'Request https:\\/\\/private.invalid/token failed',
            'Authorization: Bearer OTHER-SECRET',
            '"X-API-KEY": "OTHER-SECRET"',
            'cookie=session=OTHER-SECRET; expires=forever',
            'set-cookie: session=OTHER-SECRET',
            'Upstream returned Bearer OTHER-SECRET',
            'Upstream returned Basic T1RIRVItU0VDUkVU',
        ]
        for message in messages:
            with self.subTest(message=message):
                result = extract_private_diagnostic(body(message), KEY)
                self.assertNotIn('OTHER-SECRET', result['message'])
                self.assertNotIn('T1RIRVItU0VDUkVU', result['message'])
                self.assertNotIn('private.invalid', result['message'])
                self.assertNotIn('public-api.birdeye.so', result['message'])
                self.assertIn('REDIGIDO', result['message'])

    def test_controls_cannot_hide_key_or_remain_in_result(self):
        message = 'Invalid:\x00' + KEY[:8] + '\u202e' + KEY[8:] + '\n\t\x7f'
        result = extract_private_diagnostic(body(message), KEY)
        self.assertNotIn(KEY, result['message'])
        self.assertIn('REDIGIDO', result['message'])
        self.assertTrue(all(ord(ch) >= 32 and ord(ch) != 127 for ch in result['message']))

    def test_limits_after_redaction_so_truncation_cannot_expose_key_prefix(self):
        prefix = 'x' * (MAX_MESSAGE_CHARS - 3)
        result = extract_private_diagnostic(body(prefix + KEY + ' extra'), KEY)
        self.assertEqual(len(result['message']), MAX_MESSAGE_CHARS)
        self.assertNotIn(KEY[:3], result['message'])
        self.assertNotIn(KEY, result['message'])

    def test_rejects_malformed_missing_message_wrong_types_and_oversized_body(self):
        cases = [b'not-json', b'{', b'[]', b'null', b'{"message":null}',
                 b'{"message":42}', b'{"error":{"code":400}}', b'\xff',
                 b' ' * (MAX_BODY_BYTES + 1), '{"message":"wrong raw type"}',
                 body(''), body('\x00\u202e')]
        for raw in cases:
            with self.subTest(raw_type=type(raw).__name__, size=len(raw)):
                self.assertEqual(extract_private_diagnostic(raw, KEY), {})
        for key in ('', None, 1):
            self.assertEqual(extract_private_diagnostic(body('test'), key), {})

    def test_code_cannot_carry_secret_urls_free_text_or_unbounded_numeric_values(self):
        for code in (KEY, quote(KEY, safe=''), 'https://private.invalid',
                     'arbitrary private words', 'x' * 65, True, None, {},
                     10 ** 13, float('nan'), float('inf')):
            with self.subTest(code_type=type(code).__name__):
                result = extract_private_diagnostic(body('Bad request', error={'code': code}), KEY)
                self.assertEqual(result, {'message': 'Bad request'})
        for code in ('E422', 'validation.invalid_parameter', 422, 12.5):
            self.assertEqual(extract_private_diagnostic(body('Bad request', error={'code': code}), KEY),
                             {'message': 'Bad request', 'error': {'code': code}})

    def test_function_does_not_print_diagnostic_or_credential(self):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            result = extract_private_diagnostic(body('Server echoed ' + KEY), KEY)
        self.assertIn('REDIGIDO', result['message'])
        self.assertEqual((out.getvalue(), err.getvalue()), ('', ''))


if __name__ == '__main__':
    unittest.main()


class RestrictionDiagnosticTests(unittest.TestCase):
    def test_text_and_error_string_restrictions_redact_authentication(self):
        from wallets.api_diagnostics import extract_restriction_diagnostic
        for raw in (b'Too many requests',b'{"error":"Too many requests"}',b'{"detail":"Too many requests"}'):
            self.assertEqual(extract_restriction_diagnostic(raw,'fixture-secret')['message'],'Too many requests')
        value=extract_restriction_diagnostic(b'Quota fixture-secret https://secret.example/forbidden','fixture-secret')
        self.assertNotIn('fixture-secret',str(value));self.assertNotIn('secret.example',str(value))
