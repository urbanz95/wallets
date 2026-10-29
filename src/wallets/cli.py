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
from .helius import HeliusClient, HeliusError
from .helius_collection import HeliusHistory
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
    helius=config.get('helius',{})
    if not isinstance(helius,dict) or not isinstance(helius.get('enabled',False),bool):raise ValueError('Ativação Helius inválida')
    if helius.get('enabled',False):
        from .helius import validate_config
        validate_config(helius)
        for name in ('max_pages','page_size','max_discovery_tokens','max_early_buyers','max_launch_addresses'):
            value=helius.get(name,1)
            if isinstance(value,bool) or not isinstance(value,int) or value<=0:raise ValueError('Limite Helius inválido')
        if helius.get('page_size',100)>1000:raise ValueError('Página Helius excede contrato')
        launch=helius.get('launch_addresses',{})
        if not isinstance(launch,dict):raise ValueError('Lista de endereços Helius inválida')
        for token,addresses in launch.items():
            if not valid_address(token) or not isinstance(addresses,list) or any(not valid_address(address) for address in addresses):raise ValueError('Endereço público de lançamento inválido')
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
    reader=store.get_analysis_events if saved.source=='birdeye+helius' else store.get_events
    events=reader(saved.wallet,saved.coverage.start,saved.coverage.end)
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
        store.prune_cache(now)
        key=os.environ.get('BIRDEYE_API_KEY')
        notes=[];discovered=[];collector=None;helius_history=None;helius_client=None
        settings=config.get('helius',{})
        helius_key=os.environ.get('HELIUS_API_KEY') if settings.get('enabled',False) else None
        if helius_key:
            helius_client=HeliusClient(helius_key,store,settings)
            if args.checkpoint_command and not store.prepay_run(now.strftime('%Y-%m'),settings['max_credits_per_run'],settings['monthly_credit_ceiling'],checkpoint,namespace='helius'):
                notes.append('Limite mensal Helius atingido: recolha adiada.');helius_key=None
            if helius_key:helius_history=HeliusHistory(helius_client,store,config)
        elif settings.get('enabled',False):notes.append('HELIUS_API_KEY não configurada: histórico adicional e compras iniciais observadas indisponíveis.')
        if key:
            client=BirdeyeClient(key,store,config['api']);collector=Collector(client,store,config)
            if args.checkpoint_command:
                if not store.prepay_run(now.strftime('%Y-%m'),config['api']['max_credits_per_run'],
                                        config['api']['monthly_credit_ceiling'],checkpoint):
                    notes.append('Limite mensal local atingido: consultas adiadas, sem novas aprovações.')
                    key=None
            if key:
                try:discovered=collector.discover(now)
                except (BirdeyeError,HeliusError) as exc:notes.append(str(exc))
                notes.extend(collector.limitations)
        if not key:notes.append('Sem consultas nesta execução: chave ausente ou orçamento indisponível. Verificações ficam indeterminadas.')
        early=[]
        if helius_history:
            selected=list(getattr(collector,'selected_tokens',[])) if collector else []
            tokens=list(dict.fromkeys(getattr(args,'token',[])+selected))[:settings.get('max_discovery_tokens',3)]
            for token in tokens:
                addresses=settings.get('launch_addresses',{}).get(token,[])
                try:
                    found=helius_history.discover_first_observed(token,now,addresses=addresses)
                    early.extend(found.get('wallets',[]))
                    notes.append(f'Helius: {len(found.get("wallets",[]))} carteiras com compras iniciais observadas para {token}; cobertura global não demonstrada.')
                    if found.get('failures'):notes.append('Descoberta Helius parcial: consultar evidências guardadas.')
                except (HeliusError,ValueError,TypeError):notes.append('Descoberta Helius indisponível ou incompleta nesta execução.')
        # Primeiro pedidos manuais; restantes candidatos rodam por avaliação mais antiga.

        history={e.wallet:e for e in store.latest_evaluations()}
        automatic=list(dict.fromkeys(early+discovered+store.wallets()))
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
            if helius_history:
                from .pipeline import augment_with_helius
                events,coverage,provider=augment_with_helius(wallet,start,now,events,coverage,provider,helius_history,store)
            elif settings.get('enabled',False):
                coverage.failures.append('Histórico Helius não atualizado; não assumir ausência de atividade adicional.')
            try:results=account(events,coverage,current_marks(provider,now))
            except (ValueError,ArithmeticError):
                coverage.complete=False;coverage.failures.append('Contabilidade incoerente; requer dados verificáveis.')
                results=[]
            evaluation=evaluate(wallet,events,coverage,results,store.discovery_tokens(wallet),config,utcnow(),provider)
            store.save_evaluation(evaluation)
        notes.append('Limites gratuitos ou falta de cobertura nunca autorizam aprovação por omissão.')
        if collector:notes.append(f'Pedidos Birdeye nesta execução: {collector.client.requests}; CU reservados por pedido: {collector.client.credits}.')
        if helius_client:notes.append(f'Pedidos Helius nesta execução: {helius_client.requests}; créditos máximos reservados: {helius_client.credits}.')
        path=write_reports(store.latest_evaluations(),args.output,notes=notes,discoveries=store.discoveries())
        print(f'Relatório local: {path}. Carteiras avaliadas nesta execução: {len(candidates)}.',flush=True)
        for note in notes:print(note,flush=True)
        checkpoint()
        print('Estado confirmado no armazenamento configurado.',flush=True)
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


def _probe_structure(result):
    """Só tipos, chaves estáticas e contagens; nunca valores remotos de identidade."""
    def kind(value):
        if value is None:return 'null'
        if isinstance(value,bool):return 'boolean'
        if isinstance(value,(int,float,Decimal)):return 'number'
        if isinstance(value,str):return 'string'
        if isinstance(value,list):return 'array'
        if isinstance(value,dict):return 'object'
        return 'invalid'
    known_root=('success','data','pagination','next_cursor','detail')
    known_data=('items','tokens','summary','meta','total','current_timestamp',
                'wallet','wallet_address','currency','total_value','hasNext','has_next')
    known_item=('address','wallet','owner','tx_hash','block_unix_time','unix_time',
                'base','quote','from','to','token_address','amount','balance','price',
                'value','decimals','counts','quantity','cashflow_usd','pnl','pricing')
    shape={'root_type':kind(result)}
    if not isinstance(result,dict):return shape
    shape['root_fields']={name:kind(result[name]) for name in known_root if name in result}
    shape['unknown_root_fields']=len(set(result)-set(known_root))
    data=result.get('data')
    if isinstance(data,dict):
        shape['data_fields']={name:kind(data[name]) for name in known_data if name in data}
        shape['unknown_data_fields']=len(set(data)-set(known_data))
        for name in ('items','tokens'):
            rows=data.get(name)
            if isinstance(rows,list):
                shape[name+'_count']=len(rows)
                shape[name+'_row_types']=sorted(set(kind(row) for row in rows))
                if rows and isinstance(rows[0],dict):
                    shape[name+'_first_row_fields']={field:kind(rows[0][field]) for field in known_item if field in rows[0]}
                    shape[name+'_first_row_unknown_fields']=len(set(rows[0])-set(known_item))
    elif isinstance(data,list):
        shape['data_count']=len(data)
        shape['data_row_types']=sorted(set(kind(row) for row in data))
    pagination=result.get('pagination')
    if isinstance(pagination,dict):
        shape['pagination_fields']={name:kind(pagination[name]) for name in ('limit','offset','total') if name in pagination}
    return shape


def _probe_contract_shape(path,result):
    data=result.get('data') if isinstance(result,dict) else None
    if path=='/wallet/v2/transfer':return isinstance(data,list) and all(isinstance(row,dict) for row in data)
    field='tokens' if path=='/wallet/v2/pnl/details' else 'items'
    if not isinstance(data,dict) or not isinstance(data.get(field),list):return False
    if any(not isinstance(row,dict) for row in data[field]):return False
    if path=='/wallet/v2/current-net-worth':
        pagination=result.get('pagination')
        return isinstance(pagination,dict) and type(pagination.get('total')) is int and pagination['total']>=0
    return True


def probe_contracts(args,config):
    """Até quatro leituras gratuitas, sem retentativas, avaliações ou notificações."""
    if args.provider!='birdeye':raise ValueError('--contracts só suporta --provider birdeye')
    key=os.environ.get('BIRDEYE_API_KEY')
    if not key:
        print('BIRDEYE_API_KEY não configurada; contratos não verificados. Nenhum pedido efetuado.')
        return 2
    if not args.wallet:raise ValueError('probe --contracts exige --wallet')
    checkpoint=checkpoint_for(args)
    end=utcnow();start=end-timedelta(days=config['lookback_days'])
    requests=(
        ('GET','/trader/txs/seek_by_time',{'address':args.wallet,'before_time':int(end.timestamp()),'tx_type':'all','ui_amount_mode':'raw','offset':0,'limit':1}),
        ('POST','/wallet/v2/transfer',{'wallet':args.wallet,'time_from':int(start.timestamp()),'time_to':int(end.timestamp()),'limit':1}),
        ('POST','/wallet/v2/pnl/details',{'wallet':args.wallet,'duration':'all','pnl_method':'wac',
            'position_scope':'duration_only','sort_by':'last_trade','sort_type':'desc','offset':0,'limit':1}),
        ('GET','/wallet/v2/current-net-worth',{'wallet':args.wallet,'flags':'include_low_liquidity',
            'sort_by':'value','sort_type':'desc','offset':0,'limit':1}),
    )
    api=dict(config['api'],max_requests_per_run=4,max_retries=0,max_credits_per_run=82)
    failed=False
    with process_lock(args.db),Store(args.db) as store:
        if args.checkpoint_command and not store.prepay_run(end.strftime('%Y-%m'),82,api['monthly_credit_ceiling'],checkpoint):
            raise ValueError('Quota mensal local insuficiente para sondagem de contratos')
        client=BirdeyeClient(key,store,api)
        for method,path,query in requests:
            try:
                result=client.request(method,path,query if method=='GET' else None,query if method=='POST' else None,cache=False)
                print(f'Contrato {method} {path}: '+dumps(_probe_structure(result)),flush=True)
                if not _probe_contract_shape(path,result):
                    failed=True
                    print('Estrutura obrigatória do contrato indisponível; cobertura não confirmada.',flush=True)
            except BirdeyeError as exc:
                failed=True
                print(str(exc),file=sys.stderr,flush=True)
            finally:checkpoint()
    print('Sondagem de contratos concluída: no máximo 4 pedidos e 82 CU; sem avaliação de carteiras nem Telegram.',flush=True)
    return 2 if failed else 0


def probe(args,config):
    if getattr(args,'contracts',False):return probe_contracts(args,config)
    providers=('birdeye','helius') if args.provider=='both' else (args.provider,)
    available={name:os.environ.get('BIRDEYE_API_KEY' if name=='birdeye' else 'HELIUS_API_KEY') for name in providers}
    if not any(available.values()):
        print('Chaves solicitadas não configuradas. Nenhum pedido efetuado.');return 0
    if not args.wallet:raise ValueError('probe exige --wallet com endereço público a consultar')
    checkpoint=checkpoint_for(args)
    with process_lock(args.db),Store(args.db) as store:
        for name in providers:
            if not available[name]:
                print(f'Chave {name} não configurada; acesso não verificado.');continue
            if name=='birdeye':
                api=dict(config['api'],max_requests_per_run=1,max_retries=0,max_credits_per_run=30)
                cost=30
            else:
                api=dict(config.get('helius',{}),max_requests_per_run=1,max_retries=0,max_credits_per_run=10)
                cost=10
            if args.checkpoint_command and not store.prepay_run(utcnow().strftime('%Y-%m'),cost,api['monthly_credit_ceiling'],checkpoint,namespace=name):
                raise ValueError('Quota mensal local insuficiente')
            try:
                if name=='birdeye':
                    client=BirdeyeClient(available[name],store,api)
                    client.request('GET','/wallet/v2/current-net-worth',{'wallet':args.wallet,'flags':'include_low_liquidity','sort_type':'desc','limit':1,'offset':0},cache=False)
                    print('Acesso confirmado ao endpoint gratuito Birdeye de posições: 1 pedido, 30 CU. Não comprova os restantes endpoints nem aprova a carteira.')
                else:
                    client=HeliusClient(available[name],store,api)
                    client.request('getTransactionsForAddress',[args.wallet,{'transactionDetails':'signatures','limit':1,'sortOrder':'asc','commitment':'finalized'}],cache=False)
                    print('Acesso confirmado ao histórico Helius: 1 pedido, máximo 10 créditos. Não confirma cobertura integral nem aprova a carteira.')
            finally:checkpoint()
    return 0


def main(argv=None):
    parser=argparse.ArgumentParser(description='Carteiras Solana: Birdeye Standard e Helius Free, verificações obrigatórias e Telegram condicionado.')
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('run','notify','demo','probe','report','followup'):
        p=sub.add_parser(name)
        p.add_argument('--config',default='config.toml');p.add_argument('--db',default='state/wallets.sqlite')
        p.add_argument('--output',default='reports');p.add_argument('--checkpoint-command')
        if name=='run':
            p.add_argument('--wallet',action='append',default=[]);p.add_argument('--token',action='append',default=[]);p.add_argument('--notify',action='store_true')
        if name=='probe':
            p.add_argument('--wallet');p.add_argument('--provider',choices=('birdeye','helius','both'),default='birdeye')
            p.add_argument('--contracts',action='store_true',help='Até quatro consultas Birdeye sem retentativas, só diagnóstico estrutural e sem Telegram')
        if name=='followup':
            p.add_argument('--selection',required=True);p.add_argument('--as-of')
    args=parser.parse_args(argv)
    try:
        config=load_config(args.config)
        addresses=list(args.wallet) if hasattr(args,'wallet') and isinstance(args.wallet,list) else ([args.wallet] if hasattr(args,'wallet') and args.wallet else [])
        addresses+=getattr(args,'token',[])
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
                events=[e for e in (store.get_analysis_events if selection.source=='birdeye+helius' else store.get_events)(selection.wallet,selection.evaluated_at,as_of) if e.timestamp>selection.evaluated_at]
                observation=followup(selection,events,as_of)
                observation_id=store.save_followup(observation)
                output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
                (output/'followup.json').write_text(dumps(dict(observation,id=observation_id)),encoding='utf-8')
                checkpoint_for(args)()
                print('Observação posterior guardada; desempenho permanece indeterminado sem contabilidade reconciliada.')
            elif args.command=='notify':notify_from_store(store,config,checkpoint_for(args))
            else:print(write_reports(store.latest_evaluations(),args.output,discoveries=store.discoveries()))
        return 0
    except (BirdeyeError,HeliusError) as exc:
        print(str(exc),file=sys.stderr);return 2
    except (ValueError,TypeError,OSError,ArithmeticError,subprocess.SubprocessError,KeyError,sqlite3.DatabaseError):
        print('Operação interrompida por configuração, integridade ou persistência inválida. Nenhuma aprovação foi concedida por omissão.',file=sys.stderr)
        return 2

if __name__=='__main__':raise SystemExit(main())
