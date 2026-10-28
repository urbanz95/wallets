"""Observações posteriores ancoradas numa fotografia; sem simulação de execução."""
from __future__ import annotations
from dataclasses import asdict
from hashlib import sha256
from .models import dumps, parse_time


def followup(selection, subsequent_events, as_of):
    """O histórico de seleção nunca conta como observação posterior.

    Conta apenas atividade efetivamente guardada. Sem inventário no instante de
    seleção e custos futuros reconciliados não atribui PnL nem preços a seguidor.
    """
    as_of=parse_time(as_of)
    if not selection.id:raise ValueError('É necessária uma fotografia identificada')
    if as_of<=selection.evaluated_at:raise ValueError('O período posterior tem de começar após a seleção')
    observed={}
    for event in subsequent_events:
        if event.wallet!=selection.wallet:raise ValueError('Carteira diferente da seleção')
        if not selection.evaluated_at<event.timestamp<=as_of:
            raise ValueError('Evento fora do período posterior; informação futura ou histórico de seleção')
        previous=observed.get(event.id)
        if previous is not None and previous!=event:raise ValueError('Evento posterior divergente')
        observed[event.id]=event
    events=sorted(observed.values(),key=lambda e:(e.timestamp,e.id))
    live=selection.source=='birdeye' and all(e.source=='birdeye' for e in events)
    return {
        'selection_id':selection.id,'selection_wallet':selection.wallet,
        'criteria_version':selection.criteria_version,'criteria_hash':selection.metrics.get('_criteria_hash'),
        'selection_time':selection.evaluated_at,'selection_was_eligible':selection.eligible,
        'period_start_exclusive':selection.evaluated_at,'as_of':as_of,
        'source':'birdeye' if live else 'fixture',
        'event_count':len(events),'distinct_tokens':len({e.token for e in events}),
        'transaction_count':len({e.tx_hash or e.id for e in events}),
        'events_hash':sha256(dumps([asdict(e) for e in events]).encode()).hexdigest(),
        'coverage_complete':False,'status':'INDETERMINADA',
        'observed_wallet_pnl':None,'follower_pnl':None,'simulation':False,
        'real_performance_validated':False,
        'reason':'Apenas atividade posterior observada. Sem inventário inicial, taxas e cobertura reconciliados não é possível validar desempenho posterior nem atribuir preços ao seguidor.',
    }
