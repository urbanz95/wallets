"""Auxiliares de configuração sem credenciais reais nem rede."""
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
from scripts import generate_state_key as key_helper
from scripts import telegram_chat_id as chat_helper


class SetupHelperTests(unittest.TestCase):
    def setUp(self):
        self.no_network=patch('socket.socket.connect',side_effect=AssertionError('Rede proibida nos testes'))
        self.no_network.start();self.addCleanup(self.no_network.stop)

    def local_main(self, module, args=None):
        out,err=StringIO(),StringIO()
        out.isatty=lambda:True
        with patch.dict(os.environ,{},clear=True),patch('sys.stdin.isatty',return_value=True),redirect_stdout(out),redirect_stderr(err):
            result=module.main(args)
        return result,out.getvalue(),err.getvalue()

    def test_getupdates_is_read_only_and_token_not_returned(self):
        response=Mock()
        response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.return_value=json.dumps({'ok':True,'result':[]}).encode()
        opener=Mock();opener.open.return_value=response
        self.assertEqual(chat_helper.fetch_updates('mock-bot-token',opener=opener),[])
        request=opener.open.call_args.args[0]
        self.assertEqual(request.get_method(),'GET')
        self.assertTrue(request.full_url.endswith('/getUpdates'))
        self.assertNotIn('sendMessage',request.full_url)
        opener.open.assert_called_once()

    def test_network_failure_never_leaks_token_url(self):
        opener=Mock();opener.open.side_effect=OSError('https://api.telegram.org/botmock-secret/getUpdates')
        with self.assertRaises(chat_helper.SetupError) as caught:
            chat_helper.fetch_updates('mock-secret',opener=opener)
        self.assertNotIn('mock-secret',str(caught.exception))
        self.assertNotIn('https://',str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)

    def test_telegram_redirects_rejected(self):
        with self.assertRaises(chat_helper.SetupError):chat_helper.NoRedirect().redirect_request(None)

    def test_private_chats_deduplicate_and_exclude_groups(self):
        updates=[None,{}, {'message':None},
                 {'message':{'chat':{'type':'group','id':-123}}},
                 {'message':{'chat':{'type':'private','id':123}}},
                 {'message':{'chat':{'type':'private','id':123}}},
                 {'message':{'chat':{'type':'private','id':True}}}]
        self.assertEqual(chat_helper.private_chats(updates),[123])

    def test_one_private_chat_shown_without_token(self):
        with patch.object(chat_helper.getpass,'getpass',return_value='mock-secret'), \
             patch.object(chat_helper,'fetch_updates',return_value=[{'message':{'chat':{'type':'private','id':123}}}]):
            code,out,err=self.local_main(chat_helper)
        self.assertEqual((code,err),(0,''))
        self.assertIn('123',out)
        self.assertNotIn('mock-secret',out)

    def test_multiple_chats_never_selected_automatically(self):
        updates=[{'message':{'chat':{'type':'private','id':123}}},{'message':{'chat':{'type':'private','id':456}}}]
        with patch.object(chat_helper.getpass,'getpass',return_value='mock-secret'), \
             patch.object(chat_helper,'fetch_updates',return_value=updates):
            code,out,err=self.local_main(chat_helper)
        self.assertEqual(code,2)
        self.assertNotIn('123',out+err);self.assertNotIn('456',out+err)

    def test_empty_updates_explains_manual_message(self):
        with patch.object(chat_helper.getpass,'getpass',return_value='mock-secret'), \
             patch.object(chat_helper,'fetch_updates',return_value=[]):
            code,out,err=self.local_main(chat_helper)
        self.assertEqual(code,1);self.assertIn('/start',out)

    def test_chat_helper_refuses_actions_before_reading_secret(self):
        with patch.dict(os.environ,{'GITHUB_ACTIONS':'true'}), \
             patch.object(chat_helper.getpass,'getpass') as read,redirect_stderr(StringIO()):
            self.assertEqual(chat_helper.main(),2)
        read.assert_not_called()

    def test_malformed_telegram_response_is_not_empty_success(self):
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.return_value=b'{"ok":false,"result":[]}'
        opener=Mock();opener.open.return_value=response
        with self.assertRaises(chat_helper.SetupError):chat_helper.fetch_updates('mock-secret',opener=opener)

    def test_generated_key_private_and_usable(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'secret.key'
            key=key_helper.generate_key_file(path)
            self.assertEqual(path.read_text().strip(),key)
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            cipher=Fernet(key.encode())
            self.assertEqual(cipher.decrypt(cipher.encrypt(b'state')),b'state')

    def test_existing_key_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'secret.key';path.write_bytes(b'keep-original')
            with self.assertRaises(FileExistsError):key_helper.generate_key_file(path)
            self.assertEqual(path.read_bytes(),b'keep-original')

    def test_key_shown_only_with_explicit_local_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'hidden.key'
            code,out,err=self.local_main(key_helper,['--output',str(path)])
            self.assertEqual((code,err),(0,''));self.assertNotIn(path.read_text().strip(),out)
            other=Path(directory)/'shown.key'
            code,out,err=self.local_main(key_helper,['--output',str(other),'--show-key'])
            self.assertEqual((code,err),(0,''));self.assertIn(other.read_text().strip(),out)

    def test_key_helper_refuses_actions_and_creates_no_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'secret.key'
            with patch.dict(os.environ,{'GITHUB_ACTIONS':'true'}),redirect_stderr(StringIO()):
                self.assertEqual(key_helper.main(['--output',str(path),'--show-key']),2)
            self.assertFalse(path.exists())

    def test_chat_helper_refuses_noninteractive_input_before_token(self):
        with patch.dict(os.environ,{},clear=True),patch('sys.stdin.isatty',return_value=False), \
             patch.object(chat_helper.getpass,'getpass') as read,redirect_stderr(StringIO()):
            self.assertEqual(chat_helper.main(),2)
        read.assert_not_called()

    def test_show_key_refuses_redirected_output_before_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'secret.key'
            with patch.dict(os.environ,{},clear=True),redirect_stdout(StringIO()),redirect_stderr(StringIO()):
                self.assertEqual(key_helper.main(['--output',str(path),'--show-key']),2)
            self.assertFalse(path.exists())
