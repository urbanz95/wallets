#!/usr/bin/env python3
"""Lê apenas getUpdates do bot; nunca envia uma mensagem e nunca imprime o token."""
from __future__ import annotations

import getpass
import json
import os
import sys
from urllib.request import HTTPRedirectHandler, Request, build_opener


class SetupError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise SetupError('Redirecionamento Telegram recusado.')


def fetch_updates(token, *, opener=None):
    if not isinstance(token, str) or not token.strip() or any(c.isspace() for c in token):
        raise SetupError('Token inválido.')
    request = Request('https://api.telegram.org/bot' + token + '/getUpdates', method='GET')
    try:
        with (opener or build_opener(NoRedirect())).open(request, timeout=15) as response:
            result = json.loads(response.read(1024 * 1024))
    except Exception:
        raise SetupError('Não foi possível ler as mensagens. Confirma o token e a rede.') from None
    if not isinstance(result, dict) or result.get('ok') is not True or not isinstance(result.get('result'), list):
        raise SetupError('Resposta Telegram inválida; não é possível obter o identificador.')
    return result['result']


def private_chats(updates):
    chats = set()
    for update in updates:
        if not isinstance(update, dict):
            continue
        message = update.get('message')
        if not isinstance(message, dict):
            continue
        chat = message.get('chat')
        if isinstance(chat, dict) and chat.get('type') == 'private':
            chat_id = chat.get('id')
            if isinstance(chat_id, int) and not isinstance(chat_id, bool) and chat_id > 0:
                chats.add(chat_id)
    return sorted(chats)


def main(argv=None):
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        print('Executa este auxiliar apenas no teu computador; não deve publicar identificadores nos logs do GitHub.', file=sys.stderr)
        return 2
    if not sys.stdin.isatty():
        print('Usa um terminal interativo no teu computador para introduzir o token de forma oculta.', file=sys.stderr)
        return 2
    try:
        token = getpass.getpass('Token do BotFather (fica oculto): ')
        chats = private_chats(fetch_updates(token))
    except (SetupError, EOFError, KeyboardInterrupt):
        print('Não foi possível obter o identificador. Confirma o token, a rede e a mensagem manual ao teu bot.', file=sys.stderr)
        return 2
    if not chats:
        print('Não há mensagens privadas disponíveis. Abre o teu bot, envia /start manualmente e volta a executar.')
        return 1
    if len(chats) != 1:
        print('Existe mais de uma conversa privada. Não vou escolher uma por ti. Confirma localmente qual é a tua antes de configurar TELEGRAM_CHAT_ID.', file=sys.stderr)
        return 2
    print('Identificador da única conversa privada observada:')
    print(chats[0])
    print('Confirma que esta conversa é tua e guarda o número em TELEGRAM_CHAT_ID. Não o publiques.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
