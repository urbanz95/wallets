"""Interface simples; modo demo nunca partilha o estado nem envia para Telegram."""
from __future__ import annotations
import argparse
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import os
from pathlib import Path
import shlex
import sqlite3
import subprocess
import sys
import tomllib
from .accounting import account
from .birdeye import BirdeyeClient, BirdeyeError
from .checks import evaluate
from .collection import Collector, valid_address, decimal
from .models import Coverage, Event, TokenResult, dumps, parse_time, utcnow
from .reporting import write_reports
from .storage import Store, process_lock
from .telegram import Notifier, eligible_for_delivery


def load_config(path):
    with open(path,'rb') as handle:config=tomllib.load(handle)
    if not config.get('criteria_version'):raise ValueError('Falta versão dos critérios')
    for key in ('min_tokens','min_activity_days','min_weeks','lookback_days','max_data_age_hours',
                'max_evaluation_age_hours','max_notifications_per_run'):
        if isinstance(config.get(key),bool) or config.get(key,0)<=0:raise ValueError('Limite de configuração inválido')
    if not 0<config['api']['monthly_credit_ceiling']<=30000:raise ValueError('Teto mensal deve respeitar Standard gratuito')
    if not 0<config['api']['max_credits_per_run']<=config['api']['monthly_credit_ceiling']:raise ValueError('Orçamento por execução inválido')
    if not 0<config['api']['max_requests_per_run']:raise ValueError('Limite de pedidos inválido')
    for key in ('max_top1_share','max_top3_share','max_unknown_cost_fraction','max_open_loss_fraction',
                'max_rapid_close_fraction','min_win_rate','min_profitable_week_fraction'):
        if not 0<=config[key]<=1:raise ValueError('Fração de configuração fora de 0..1')
    return config


def checkpoint_for(args):
    if args.checkpoint_command:
        command=shlex.split(args.checkpoint_command)
        if not command:raise ValueError('Comando de checkpoint vazio')
        def checkpoint():
            subprocess.run(command,check=True)
        return checkpoint
    if os.environ.get('GITHUB_ACTIONS')=='true':
        raise ValueError('GitHub Actions exige checkpoint durável externo')
    return lambda:None  # SQLite é durável no disco local; runners usam sempre cofre remoto


def current_marks(provider, end):
    """Marcas indicativas separadas do custo; timestamp snapshot não prova liquidez."""
    stamp=provider.get('holdings_time')
    if stamp is None:return {}
    try:stamp=parse_time(stamp)
    except (TypeError,ValueError,OverflowError):return {}
    if stamp>end:return {}  # nunca usar observação posterior ao cutoff de seleção
    result={}
    holdings=provider.get('holdings',[])
    if not isinstance(holdings,list):return {}
    for row in holdings:
        if not isinstance(row,dict):continue
        token=row.get('address',row.get('token_address'))
        price=decimal(row.get('price') if row.get('price') is not None else row.get('priceUsd'))
        if valid_address(token) and price is not None and price>=0:
            result[token]={'price_usd':price,'timestamp':stamp}
    return result


def reevaluate(saved,store,config,now=None):
    now=now or utcnow()
    events=store.get_events(saved.wallet,saved.coverage.start,saved.coverage.end)
    provider=saved.metrics.get('provider_metrics',{})
    coverage=replace(saved.coverage,failures=list(saved.coverage.failures))
    # A atualização do relógio de avaliação não torna os dados mais recentes.
    results=account(events,coverage,current_marks(provider,coverage.end))
    return evaluate(saved.wallet,events,coverage,results,saved.discovery_tokens,config,now,provider)


def notify_from_store(store,config,checkpoint):
    notifier=Notifier(store,config,os.environ.get('TELEGRAM_BOT_TOKEN'),os.environ.get('TELEGRAM_CHAT_ID'),checkpoint=checkpoint)
    sent=0
    for previous in store.latest_evaluations():
        if not eligible_for_delivery(previous,config):continue
        fresh=reevaluate(previous,store,config)
        store.save_evaluation(fresh)
        if notifier.send(fresh):sent+=1
    print(f'Envios elegíveis concluídos: {sent}. Nenhum estado técnico é enviado ao Telegram.')
    return sent


def run(args, config):
    checkpoint=checkpoint_for(args)
    with process_lock(args.db), Store(args.db) as store:
        now=utcnow();start=now-timedelta(days=config['lookback_days'])
        key=os.environ.get('BIRDEYE_API_KEY')
        notes=[];discovered=[];collector=None
        if key:
            client=BirdeyeClient(key,store,config['api']);collector=Collector(client,store,config)
            if args.checkpoint_command:
                if not store.prepay_run(now.strftime('%Y-%m'),config['api']['max_credits_per_run'],
                                        config['api']['monthly_credit_ceiling'],checkpoint):
                    notes.append('Limite mensal local atingido: consultas adiadas, sem novas aprovações.')
                    key=None
            if key:
                try:discovered=collector.discover(now)
                except BirdeyeError as exc:notes.append(str(exc))
                notes.extend(collector.limitations)
        if not key:notes.append('Sem consultas nesta execução: chave ausente ou orçamento indisponível. Verificações ficam indeterminadas.')
        # Primeiro pedidos manuais; restantes candidatos rodam por avaliação mais antiga.
        history={e.wallet:e for e in store.latest_evaluations()}
        automatic=list(dict.fromkeys(discovered+store.wallets()))
        automatic.sort(key=lambda address:history[address].evaluated_at.isoformat() if address in history else '')
        candidates=list(dict.fromkeys(args.wallet+automatic))[:config['discovery']['max_wallets']]
        for wallet in candidates:
            provider={};coverage=Coverage(start,now,utcnow(),source='birdeye')
            events=store.get_events(wallet,start,now)
            if key and collector:
                try:events,coverage,provider=collector.collect(wallet,start,now)
                except (BirdeyeError,ValueError,TypeError):
                    coverage.failures.append('Recolha/validação incompleta; não reutilizar aprovação anterior.')
            else:coverage.failures.append('Não foi possível atualizar os dados Birdeye.')
            try:results=account(events,coverage,current_marks(provider,now))
            except (ValueError,ArithmeticError):
                coverage.complete=False;coverage.failures.append('Contabilidade incoerente; requer dados verificáveis.')
                results=[]
            evaluation=evaluate(wallet,events,coverage,results,store.discovery_tokens(wallet),config,utcnow(),provider)
            store.save_evaluation(evaluation)
        notes.append('Limites gratuitos ou falta de cobertura nunca autorizam aprovação por omissão.')
        if collector:notes.append(f'Pedidos nesta execução: {collector.client.requests}; CU reservados por pedido: {collector.client.credits}.')
        path=write_reports(store.latest_evaluations(),args.output,notes=notes,discoveries=store.discoveries())
        checkpoint()
        print(f'Relatório local: {path}. Carteiras avaliadas nesta execução: {len(candidates)}.')
        for note in notes:print(note)
        if args.notify:notify_from_store(store,config,checkpoint)
    return 0


def demo(args,config):
    now=utcnow();coverage=Coverage(now-timedelta(days=28),now,now,source='fixture',complete=True,all_tokens=True,
        opening_inventory_known=True,fees_complete=True,marks_complete=True,liquidity_complete=True,
        execution_complete=True,coordination_complete=True,labels_complete=True)
    results=[]
    for wallet,values in [('Carteira A fictícia',[12000,-500,-500,-500,-500]),('Carteira B fictícia',[800,800,800,800,800])]:
        tokens=[];events=[]
        for i,value in enumerate(values):
            token=f'token_{i}';stamp=now-timedelta(days=26-i*4)
            week=(stamp-timedelta(days=stamp.weekday())).date().isoformat()
            tokens.append(TokenResult(token,realized=Decimal(value),identified_realized=Decimal(value),
                costs=Decimal(1000),realized_cost=Decimal(1000),open_cost=Decimal(0),unrealized=Decimal(0),
                weekly={week:Decimal(value)},entries=[Decimal(1000)],holding_seconds=[3600],reliable=True))
            events.append(Event(f'{wallet}_{i}',wallet,token,stamp,'sell',Decimal(1),source='fixture'))
        results.append(evaluate(wallet,events,coverage,tokens,[],config,now))
    path=write_reports(results,args.output,notes=['DEMONSTRAÇÃO COM DADOS FICTÍCIOS. Não consulta APIs, não guarda no estado operacional e nunca envia para Telegram.'])
    print(f'Demonstração local: {path}')
    for e in results:print(f"{e.wallet}: total={e.metrics['total_result']}; sem melhor={e.metrics['without_best']}; concentração={e.metrics['top1_share']}")
    return 0


def probe(args,config):
    if not os.environ.get('BIRDEYE_API_KEY'):
        print('BIRDEYE_API_KEY não configurada. Nenhum pedido efetuado.');return 0
    if not args.wallet:raise ValueError('probe exige --wallet com endereço público a consultar')
    checkpoint=checkpoint_for(args)
    with process_lock(args.db),Store(args.db) as store:
        api=dict(config['api'],max_requests_per_run=1,max_retries=0,max_credits_per_run=30)
        if args.checkpoint_command and not store.prepay_run(utcnow().strftime('%Y-%m'),30,api['monthly_credit_ceiling'],checkpoint):
            raise ValueError('Quota mensal local insuficiente')
        client=BirdeyeClient(os.environ['BIRDEYE_API_KEY'],store,api)
        try:
            client.request('GET','/wallet/v2/current-net-worth',{'wallet':args.wallet,'flags':'include_low_liquidity','limit':1,'offset':0},cache=False)
            print('Acesso confirmado ao endpoint gratuito de posições: 1 pedido, 30 CU. Isto não comprova os restantes endpoints nem aprova a carteira.')
        finally:checkpoint()
    return 0


def main(argv=None):
    parser=argparse.ArgumentParser(description='Carteiras Solana: Birdeye Standard, verificações obrigatórias e Telegram condicionado.')
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('run','notify','demo','probe','report','followup'):
        p=sub.add_parser(name)
        p.add_argument('--config',default='config.toml');p.add_argument('--db',default='state/wallets.sqlite')
        p.add_argument('--output',default='reports');p.add_argument('--checkpoint-command')
        if name=='run':
            p.add_argument('--wallet',action='append',default=[]);p.add_argument('--notify',action='store_true')
        if name=='probe':p.add_argument('--wallet')
        if name=='followup':
            p.add_argument('--selection',required=True);p.add_argument('--as-of')
    args=parser.parse_args(argv)
    try:
        config=load_config(args.config)
        addresses=args.wallet if hasattr(args,'wallet') and isinstance(args.wallet,list) else ([args.wallet] if hasattr(args,'wallet') and args.wallet else [])
        if any(not valid_address(address) for address in addresses):raise ValueError('Endereço público Solana inválido')
        if args.command=='run':return run(args,config)
        if args.command=='demo':return demo(args,config)
        if args.command=='probe':return probe(args,config)
        with process_lock(args.db),Store(args.db) as store:
            if args.command=='followup':
                from .validation import followup
                selection=store.get_evaluation(args.selection)
                if not selection:raise ValueError('Fotografia desconhecida')
                as_of=parse_time(args.as_of) if args.as_of else utcnow()
                if as_of>utcnow():raise ValueError('Observação futura recusada')
                events=[e for e in store.get_events(selection.wallet,selection.evaluated_at,as_of) if e.timestamp>selection.evaluated_at]
                observation=followup(selection,events,as_of)
                observation_id=store.save_followup(observation)
                output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
                (output/'followup.json').write_text(dumps(dict(observation,id=observation_id)),encoding='utf-8')
                checkpoint_for(args)()
                print('Observação posterior guardada; desempenho permanece indeterminado sem contabilidade reconciliada.')
            elif args.command=='notify':notify_from_store(store,config,checkpoint_for(args))
            else:print(write_reports(store.latest_evaluations(),args.output,discoveries=store.discoveries()))
        return 0
    except BirdeyeError as exc:
        print(str(exc),file=sys.stderr);return 2
    except (ValueError,TypeError,OSError,ArithmeticError,subprocess.SubprocessError,KeyError,sqlite3.DatabaseError):
        print('Operação interrompida por configuração, integridade ou persistência inválida. Nenhuma aprovação foi concedida por omissão.',file=sys.stderr)
        return 2

if __name__=='__main__':raise SystemExit(main())
