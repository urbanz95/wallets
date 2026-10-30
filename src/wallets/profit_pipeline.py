"""Funil por lucro fornecido -> WAC barato -> FIFO próprio em SOL -> ranking.

As identidades são persistidas apenas no estado privado e no Excel. Nenhuma
função deste módulo disponibiliza notificações ou operações de trading.
"""
from __future__ import annotations
from collections import Counter
from datetime import timedelta
from decimal import Decimal
import os
from pathlib import Path

from .birdeye import BirdeyeClient, BirdeyeError
from .collection import normalize_trade
from .helius import HeliusClient, HeliusError
from .helius_collection import HeliusHistory
from .models import parse_time, utcnow
from .profit_discovery import discover
from .profit_prefilter import prefilter_wallet
from .profit_ranking import rank_candidates
from .profit_reporting import write_profit_reports
from .profit_persistence import ensure_schema, load_receipts, save_receipts_and_prune, save_run
from .sol_accounting import analyze_sol

ZERO = Decimal(0)


def _number(value):
    try:
        result=Decimal(str(value))
        return result if result.is_finite() else None
    except (ArithmeticError,ValueError,TypeError):return None


def _provider_order(candidate):
    pref=candidate.get('prefilter7',{})
    amount=_number(pref.get('pnl_usd'))
    pf=_number(pref.get('profit_factor'))
    top3=_number(pref.get('top3_share'))
    tokens=_number(pref.get('tokens_count'))
    # Qualidade mensurável antes do montante: não premiar só um grande vencedor.
    quality=(int(pf is not None and pf >= Decimal('1.3'))
             +int(top3 is not None and top3 <= Decimal('.65')))
    return (not pref.get('passed',False),-len(set(candidate.get('origins',[]))),
            -quality,-min(tokens or ZERO,Decimal(40)),-(amount or ZERO))


def _codes(values):
    """Motivos públicos são códigos, nunca texto da rede ou identidades."""
    result=[]
    allowed={'activity_over_daily_limit','discovery_activity_absent','discovery_metrics_unknown',
        'excluded_cohort_tag','provider_pnl_not_positive','provider_pnl_unknown',
        'provider_activity_unknown','provider_activity_absent','prefilter_sample_insufficient',
        'deep_history_partial','deep_unavailable','deep_normalization_unknown',
        'provider_30d_not_positive','pnl_schema_unknown','pnl_token_count_unknown',
        'provider_7d','provider_30d','deep_coverage','recent_activity','sol_sample',
        'known_cost_scope','sol_worst_case','profit_factor','winner_removal','concentration',
        'execution_intensity','holding_horizon','identity','cohort_exclusion','sol_costs',
        'pnl_unavailable','excluded_tag','discovery_activity_outside_window',
        'discovery_time_unknown','unsupported_network','pnl_pagination_changed',
        'pnl_token_schema_unknown','pnl_duplicate_token','pnl_pagination_incoherent','pnl_wallet_mismatch'}
    for value in values:
        code=value.split(':',1)[0] if isinstance(value,str) else ''
        result.append(code if code in allowed else 'other_data_limitation')
    return result


def _birdeye_trade_evidence(client,wallet,start,end,pages):
    events=[];failures=[];completed=False
    for offset in range(max(1,int(pages))):
        try:
            response=client.request('GET','/trader/txs/seek_by_time',{
                'address':wallet,'after_time':int(start.timestamp())-1,'tx_type':'all',
                'ui_amount_mode':'raw','offset':offset*100,'limit':100})
            data=response.get('data',{});rows=data.get('items')
            if not isinstance(rows,list):failures.append('birdeye_trade_schema_unknown');break
            for row in rows:
                try:
                    parsed=normalize_trade(row,wallet)
                    events.extend(e for e in parsed if start<=e.timestamp<end)
                except (ValueError,TypeError,KeyError):failures.append('birdeye_trade_uninterpreted')
            more=data.get('hasNext',data.get('has_next',len(rows)>=100))
            if more is False:completed=True;break
            if not rows:break
        except BirdeyeError:
            failures.append('birdeye_history_unavailable');break
    # Offset num feed móvel não prova por si só uma fotografia estável.
    return events,{'pages_complete':completed and offset==0,'reasons':sorted(set(failures)),
                   'scope':'USD informativo e corroboração de swaps; não converte o FIFO SOL.'}


def run_pipeline(store,config,budget,checkpoint,output,now=None):
    now=now or utcnow();start=now-timedelta(days=7);settings=config['profit']
    ensure_schema(store)
    bird=BirdeyeClient(os.environ.get('BIRDEYE_API_KEY',''),store,config['api'])
    helius=HeliusClient(os.environ.get('HELIUS_API_KEY',''),store,config['helius'])
    clients={'birdeye':bird,'helius':helius}
    histogram=Counter();counts={'candidates':0,'pre_filter':0,'pnl_details':0,'persistence':0,'deep':0,'top3':0}
    with budget.stage('discovery',birdeye=int(settings.get('discovery_budget',230)),store=store,clients=clients):
        found=discover(bird,store,config,now)
    candidates=found['candidates']
    counts['candidates']=len(candidates)+len(found.get('rejections',[]))
    counts['pre_filter']=len(candidates)
    for row in found.get('rejections',[]):histogram.update(_codes(row.get('reasons',[])))
    for row in found.get('errors',[]):histogram.update(['discovery_source_unavailable'])
    assessed=[]
    pre_candidates=candidates[:int(settings.get('max_prefilters',7))]
    pnl_page_cost=30*max(1,int(settings.get('prefilter_max_pages',1)))
    with budget.stage('prefilter',birdeye=min(int(settings.get('prefilter_budget',210)),len(pre_candidates)*pnl_page_cost),store=store,clients=clients):
        for candidate in pre_candidates:
            candidate['prefilter7']=prefilter_wallet(bird,candidate['wallet'],7,config,now)
            counts['pnl_details']+=1;assessed.append(candidate)
            histogram.update(_codes(candidate['prefilter7'].get('reasons',[])))
    provisional=sorted((c for c in assessed if c['prefilter7'].get('passed')),key=_provider_order)
    persistence_candidates=provisional[:int(settings.get('max_persistence',3))]
    with budget.stage('persistence',birdeye=min(int(settings.get('persistence_budget',90)),len(persistence_candidates)*pnl_page_cost),store=store,clients=clients):
        for candidate in persistence_candidates:
            candidate['prefilter30']=prefilter_wallet(bird,candidate['wallet'],30,config,now)
            counts['persistence']+=1
            histogram.update(_codes(candidate['prefilter30'].get('reasons',[])))
    deep_candidates=[c for c in provisional if c.get('prefilter30',{}).get('passed')][:int(settings.get('max_deep',2))]
    with budget.stage('deep',birdeye=min(int(settings.get('deep_budget',270)),len(deep_candidates)*12*int(settings.get('deep_birdeye_pages',3))),
                      helius=min(int(settings.get('helius_deep_budget',200)),len(deep_candidates)*100),store=store,clients=clients):
        history=HeliusHistory(helius,store,config)
        for candidate in deep_candidates:
            wallet=candidate['wallet'];counts['deep']+=1
            deep={'complete':False,'period_start':start,'period_end':now,'fetched_at':utcnow(),
                  'metrics':{},'reasons':[],'currency':'SOL'}
            try:
                evidence,bird_scope=_birdeye_trade_evidence(bird,wallet,start,now,settings.get('deep_birdeye_pages',3))
                captured=history.history(wallet,start,now,token_accounts=True)
                raw=[row for row in captured['transactions'] if start<=parse_time(row['blockTime'])<now]
                previous=load_receipts(store,wallet,start,now)
                # Só contabilizar cada assinatura uma vez; dados completos novos
                # substituem a representação normalizada da mesma transação.
                fresh_ids={(row.get('transaction') or {}).get('signatures',[None])[0] for row in raw}
                previous=[row for row in previous if row.get('signature') not in fresh_ids]
                result=analyze_sol(previous+raw,wallet,start,now,candidate.get('origins',[]),birdeye_events=evidence)
                stats=save_receipts_and_prune(store,wallet,result['receipts'],raw,start,now,
                    metadata={'pages_complete':captured.get('pages_complete'), 'truncated':captured.get('truncated')})
                deep.update(metrics=result['metrics'],tokens=result['tokens'],receipt_counts=stats,
                    complete=bool(captured.get('pages_complete') and not captured.get('truncated') and not captured.get('failures')),
                    coverage=dict(result['coverage'],start=start,end=now,source='helius',pages_complete=captured.get('pages_complete')),
                    birdeye_scope=bird_scope,fetched_at=utcnow())
                if not deep['complete']:deep['reasons'].append('deep_history_partial')
                if result.get('limitations'):deep['limitations']=result['limitations']
            except (HeliusError,BirdeyeError,ValueError,TypeError,KeyError,ArithmeticError):
                deep['reasons'].append('deep_normalization_unknown')
            candidate['deep']=deep
            histogram.update(_codes(deep['reasons']))
            checkpoint()
    evaluated_at=utcnow()
    ranked=rank_candidates(assessed,config,evaluated_at);counts['top3']=len(ranked)
    # Histograma de toda a amostra consultada, não apenas dos três primeiros.
    for candidate in assessed:
        for row in rank_candidates([candidate],config,evaluated_at):
            histogram.update(_codes(row.get('reason_codes',[])))
    levels=Counter(row.get('evidence_level','C') for row in ranked)
    telemetry={'started_at':now,'stages':counts,'budget':budget.stats,
               'rejections':dict(histogram),'ranking_levels':dict(levels)}
    save_run(store,{'profile':settings.get('version',config['criteria_version']),'criteria':config,
                    'started_at':now,'finished_at':utcnow(),'candidates':assessed,
                    'ranking':ranked,'telemetry':telemetry})
    checkpoint()
    report=write_profit_reports(ranked,telemetry,output)
    # O resultado público é construído pelo relator com allowlist; nunca é o ranking.
    return report['public_summary']
