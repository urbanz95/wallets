"""Telegram só após gate central. Uma tentativa por reserva; falhas ambíguas não repetem."""
from __future__ import annotations
from datetime import timedelta
from dataclasses import asdict
from hashlib import sha256
from decimal import Decimal
from html import escape
import json
import urllib.request
from zoneinfo import ZoneInfo
from .models import REQUIRED_CHECKS, Status, utcnow, dumps

LISBON=ZoneInfo('Europe/Lisbon')

def local_time(value):
    return value.astimezone(LISBON).strftime('%d/%m/%Y %H:%M %Z')

def eligible_for_delivery(evaluation, config, now=None):
    now=now or utcnow()
    if not evaluation or not evaluation.eligible:return False
    from .collection import valid_address
    from .models import dumps
    from hashlib import sha256
    if not valid_address(evaluation.wallet):return False
    if evaluation.metrics.get('_criteria_hash')!=sha256(dumps(config).encode()).hexdigest():return False
    if dumps(evaluation.metrics.get('_criteria_config'))!=dumps(config):return False
    if evaluation.source!='birdeye' or evaluation.coverage.source!='birdeye':return False
    if evaluation.criteria_version!=config['criteria_version']:return False
    coverage=evaluation.coverage
    if not (coverage.start<coverage.end<=coverage.fetched_at<=evaluation.evaluated_at<=now):return False
    if now-coverage.end>timedelta(hours=config['max_data_age_hours']):return False
    if now-coverage.fetched_at>timedelta(hours=config['max_data_age_hours']):return False
    if now-evaluation.evaluated_at>timedelta(hours=config['max_evaluation_age_hours']):return False
    if not (coverage.complete and coverage.all_tokens and coverage.opening_inventory_known
            and coverage.fees_complete and coverage.marks_complete and coverage.liquidity_complete
            and coverage.execution_complete and coverage.coordination_complete and coverage.labels_complete):return False
    if coverage.truncated or coverage.failures:return False
    if len(evaluation.checks)!=len(REQUIRED_CHECKS):return False
    for check in evaluation.checks:
        if (check.status!=Status.PASS or check.criteria_version!=evaluation.criteria_version
                or check.evaluated_at!=evaluation.evaluated_at or check.period_start!=coverage.start
                or check.period_end!=coverage.end):return False
    return True

def _amount(value):
    return 'desconhecido' if value is None else f'{Decimal(str(value)):,.2f} USD'

def _percent(value):
    return 'desconhecida' if value is None else f'{Decimal(str(value))*100:.1f}%'

def format_message(evaluation):
    m=evaluation.metrics;c=evaluation.coverage
    address=escape(evaluation.wallet,quote=True)
    checks='; '.join(f'{escape(c.name)}: {c.status.value}' for c in evaluation.checks)
    return '\n'.join([
        '<b>Carteira aprovada nos critérios desta avaliação</b>',
        f'<code>{address}</code>',
        'Rede: Solana',
        f'Período: {local_time(c.start)} a {local_time(c.end)}',
        f'Tokens distintos avaliados: {m.get("tokens_count",len(evaluation.tokens))}',
        f'Realizado recalculado: {_amount(m.get("realized_total"))}',
        'Custos: FIFO; custos históricos e taxas verificados, sem converter o passado ao preço atual de SOL.',
        f'Sem melhor token (realizado + aberto marcado): {_amount(m.get("without_best"))}',
        f'Concentração nos ganhos positivos: melhor {_percent(m.get("top1_share"))}; três melhores {_percent(m.get("top3_share"))}',
        f'Resultado aberto por marcas: {_amount(m.get("unrealized_total"))}; perdas abertas: {_amount(m.get("open_losses"))}',
        'As marcas de preço não garantem a venda integral da posição.',
        f'Verificações: {checks}',
        'Automatização e possibilidade de acompanhar: passaram os limites documentados com cobertura suficiente; não confirma identidade humana.',
        f'Avaliação: {local_time(evaluation.evaluated_at)}; critérios {escape(evaluation.criteria_version)}',
        f'<a href="https://solscan.io/account/{address}">Solscan</a> | <a href="https://birdeye.so/profile/{address}?chain=solana">Birdeye</a>',
    ])


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise RuntimeError('Redirecionamento Telegram recusado')

def telegram_transport(token,chat_id,message):
    payload=json.dumps({'chat_id':chat_id,'text':message,'parse_mode':'HTML',
                        'link_preview_options':{'is_disabled':True}}).encode()
    req=urllib.request.Request(f'https://api.telegram.org/bot{token}/sendMessage',data=payload,
                               headers={'Content-Type':'application/json'},method='POST')
    # Não repetir POST: timeout pode significar entrega aceite sem resposta recebida.
    with urllib.request.build_opener(_NoRedirect()).open(req,timeout=20) as response:
        result=json.loads(response.read(65536))
    if not result.get('ok'):raise RuntimeError('Telegram recusou o envio')
    return str(result['result']['message_id'])

class Notifier:
    def __init__(self,store,config,token,chat_id,*,checkpoint,transport=telegram_transport,clock=utcnow):
        self.store=store;self.config=config;self.token=token;self.chat_id=chat_id
        self.checkpoint=checkpoint;self.transport=transport;self.clock=clock;self.attempts=0

    def _events_current(self,evaluation):
        events=self.store.get_events(evaluation.wallet,evaluation.coverage.start,evaluation.coverage.end)
        evidence=dumps([asdict(e) for e in sorted(events,key=lambda e:(e.timestamp,e.id))])
        return evaluation.metrics.get('_events_hash')==sha256(evidence.encode()).hexdigest()

    def send(self,evaluation):
        now=self.clock()
        if not self.token or not self.chat_id:return False
        if self.attempts>=self.config['max_notifications_per_run']:return False
        if not eligible_for_delivery(evaluation,self.config,now):return False
        if not evaluation.id or self.store.latest_id(evaluation.wallet)!=evaluation.id:return False
        if not self._events_current(evaluation):return False
        saved=self.store.get_evaluation(evaluation.id)
        if not saved or saved.to_dict()!=evaluation.to_dict():
            # Decimal nas métricas serializadas pode ter representação str; compara JSON canónico.
            from .models import dumps
            if not saved or dumps(saved.to_dict())!=dumps(evaluation.to_dict()):return False
        last=self.store.last_notification(evaluation.wallet)
        if last:
            if last['state']!='sent':return False  # entrega incerta exige revisão local
            if now-last['at']<timedelta(days=self.config['notification_cooldown_days']):return False
            previous=self.store.get_evaluation(last['evaluation_id'])
            if not previous or evaluation.coverage.start<previous.coverage.end:return False
        if not self.store.reserve_notification(evaluation,now):return False
        self.attempts+=1
        self.checkpoint()  # se falhar, NÃO executar HTTP
        # Gate repetido imediatamente antes de enviar, depois do checkpoint remoto.
        if (self.store.latest_id(evaluation.wallet)!=evaluation.id or not self._events_current(evaluation)
                or not eligible_for_delivery(evaluation,self.config,self.clock())):
            self.store.finish_notification(evaluation.id,'cancelled')
            self.checkpoint()
            return False
        try:
            message_id=self.transport(self.token,self.chat_id,format_message(evaluation))
        except Exception:
            self.store.finish_notification(evaluation.id,'uncertain')
            self.checkpoint()
            return False
        self.store.finish_notification(evaluation.id,'sent',message_id)
        self.checkpoint()
        return True
