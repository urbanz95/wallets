"""Recolha profunda limitada, reprocessável e com inventário anterior separado."""
from datetime import timedelta
from hashlib import sha256
from .models import dumps, parse_time, utcnow
from .helius import HeliusError
from .helius_collection import HeliusHistory,transaction_identity
from .helius_parsed import parse_transactions
from .profit_evidence import replay,persist,expire
from .sol_accounting import analyze_sol,normalize_sol_receipt


def evaluate(store,client,config,wallet,start,end,evidence,origins,checkpoint,history_factory=HeliusHistory):
    settings=config['profit']; hconfig=dict(config,helius=dict(config['helius']))
    hconfig['helius']['history_namespace']='profit-replay-v2'
    captured=history_factory(client,store,hconfig).history(wallet,start,end,token_accounts=True)
    current={r['signature'] if r.get('schema') else transaction_identity(r):r for r in replay(store,wallet,start,end)}
    for raw in captured['transactions']:
        if start<=parse_time(raw['blockTime'])<end:current[transaction_identity(raw)]=raw
    proofs={}; failures=list(captured.get('failures',[])); parsed_requests=0
    signatures=[]
    for sig,raw in current.items():
        key='parsed-proof:'+sha256((wallet+':'+sig).encode()).hexdigest()
        old=store.cache_get(key,utcnow())
        if old:proofs[sig]=old;continue
        try:known=normalize_sol_receipt(raw,wallet,birdeye_events=evidence).get('classification_complete') is True
        except (ValueError,TypeError,KeyError,ArithmeticError):known=False
        if not known:signatures.append(sig)
    maximum=max(0,int(settings.get('parsed_max_batches',0)))
    for offset in range(0,min(len(signatures),maximum*100),100):
        try:
            response=parse_transactions(client,signatures[offset:offset+100]);parsed_requests+=1
            for item in response:
                sig=item['signature'];proofs[sig]=item
                store.cache_put('parsed-proof:'+sha256((wallet+':'+sig).encode()).hexdigest(),item,utcnow()+timedelta(days=35))
                raw=item.get('rawTransaction')
                # Recolher novamente provas legadas que foram podadas; nunca trocar origem já presente.
                if current[sig].get('schema') and isinstance(raw,dict) and transaction_identity(raw)==sig and start<=parse_time(raw['blockTime'])<end:
                    current[sig]=raw
        except (HeliusError,ValueError,TypeError,KeyError):
            failures.append('Parsed Events incompletos; interpretação nativa conservada.');break
    values=list(current.values())
    result=analyze_sol(values,wallet,start,end,origins,birdeye_events=evidence,parsed_events=proofs)
    raw=[r for r in values if not r.get('schema')]
    stats=persist(store,wallet,result['receipts'],raw,start,end)
    checkpoint()  # Evidência recente durável antes do backfill opcional.
    opening_complete=False;opening_count=0
    pages=int(settings.get('opening_max_pages',0))
    need_opening=any('Inventário inicial' in t['unknown_reason'] or 'aquisição anterior' in t['unknown_reason'] for t in result['tokens'])
    if pages and need_opening and captured.get('pages_complete'):
        opening_start=start-timedelta(days=int(settings.get('opening_lookback_days',21)))
        oldconfig=dict(hconfig,helius=dict(hconfig['helius'],max_pages=pages,history_namespace='profit-opening-v2'))
        old=history_factory(client,store,oldconfig).history(wallet,opening_start,start,token_accounts=True)
        previous={r['signature'] if r.get('schema') else transaction_identity(r):r for r in replay(store,wallet,opening_start,start)}
        for row in old['transactions']:
            if opening_start<=parse_time(row['blockTime'])<start:previous[transaction_identity(row)]=row
        prior=list(previous.values());opening_count=len(prior)
        opening_complete=bool(old.get('pages_complete') and not old.get('truncated') and not old.get('failures'))
        previous_result=analyze_sol(prior,wallet,opening_start,start)
        persist(store,wallet,previous_result['receipts'],[r for r in prior if not r.get('schema')],opening_start,start)
        if opening_complete:
            result=analyze_sol(values,wallet,start,end,origins,birdeye_events=evidence,parsed_events=proofs,
                opening_transactions=prior,opening_start=opening_start)
        else:failures.append('Inventário anterior incompleto; custos iniciais permanecem desconhecidos.')
    expire(store,end-timedelta(days=35))
    return result,captured,dict(stats,parsed_requests=parsed_requests,opening_transactions=opening_count,
                                 opening_complete=opening_complete),failures
