"""Integra Helius como complemento explícito, com provas e custos desconhecidos."""
from dataclasses import replace
from .helius import HeliusError
from .models import utcnow
from .reconciliation import reconcile


def augment_with_helius(wallet,start,end,base_events,coverage,provider,history,store):
    """Mantém dados brutos por fonte e evita duplicação da mesma assinatura."""
    from .helius_normalization import normalize_transaction
    coverage=replace(coverage,source='birdeye+helius',failures=list(coverage.failures))
    details={'source':'helius','pages_complete':False,'truncated':True,'failures':[],
             'transaction_count':0,'normalization':[]}
    try:
        result=history.history(wallet,start,end,token_accounts=True)
        details.update({key:value for key,value in result.items() if key!='transactions'})
        raw=result['transactions'];details['transaction_count']=len(raw)
        events=[]
        for row in raw:
            parsed=normalize_transaction(row,wallet)
            if not isinstance(parsed,dict) or not isinstance(parsed.get('events'),list):
                raise ValueError('Formato de normalização inválido')
            if any(e.wallet!=wallet or not start<=e.timestamp<=end or e.source!='helius' for e in parsed['events']):
                raise ValueError('Evento normalizado fora do âmbito')
            events.extend(parsed['events'])
            details['normalization'].append({'evidence':parsed.get('evidence',{}),'limitations':parsed.get('limitations',[])})
        store.put_source_events('helius',events)
        if result.get('truncated') or not result.get('pages_complete'):
            coverage.failures.append('Histórico Helius incompleto no período ou âmbito solicitado.')
        if result.get('failures'):
            coverage.failures.append('Falhas na recolha Helius; cobertura não confirmada.')
    except (HeliusError,ValueError,TypeError,KeyError):
        details['failures'].append('Histórico ou normalização Helius não verificável; sem aprovação por omissão.')
        coverage.failures.append('Não foi possível concluir a recolha Helius.')
    persisted=store.get_source_events('helius',wallet,start,end)
    events,proof=reconcile(base_events,persisted)
    if proof['conflicting_transactions']:
        coverage.failures.append('Fontes discordam sobre quantidades de transações; contabilidade indeterminada.')
    details['reconciliation']=proof
    provider=dict(provider,helius=details)
    coverage.fetched_at=utcnow()
    # Concluir páginas de um endereço não prova inventário, taxas USD, todos os
    # protocolos ou liquidez executável. Nenhum requisito é promovido por flag.
    provider['sources']=['birdeye','helius']
    return events,coverage,provider
