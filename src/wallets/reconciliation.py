"""Uma transação económica por carteira; provas das fontes conservadas à parte."""
from collections import defaultdict
from dataclasses import replace
from decimal import Decimal


def reconcile(birdeye_events,helius_events):
    birdeye_events=list(birdeye_events);helius_events=list(helius_events)
    groups=defaultdict(list)
    for event in birdeye_events:
        groups[event.tx_hash].append(event)
    helius_hashes={e.tx_hash for e in helius_events if e.tx_hash}
    result=[e for e in birdeye_events if not e.tx_hash or e.tx_hash not in helius_hashes]
    matched=0;conflicts=[]
    for event in helius_events:
        siblings=groups.get(event.tx_hash,[]) if event.tx_hash else []
        candidates=[e for e in siblings if e.wallet==event.wallet and e.token==event.token and e.kind==event.kind]
        exact=[e for e in candidates if e.quantity==event.quantity and not e.multi_step]
        # USD só se existe uma correspondência inequívoca de unidades, sentido,
        # assinatura e instante histórico. Não estimar fees com SOL atual.
        if (event.kind in ('buy','sell') and not event.multi_step and len(exact)==1
                and len(candidates)==1 and exact[0].timestamp==event.timestamp
                and isinstance(exact[0].usd_value,Decimal) and exact[0].usd_value.is_finite()
                and exact[0].usd_value>=0 and exact[0].source=='birdeye'):
            event=replace(event,usd_value=exact[0].usd_value,source='birdeye+helius')
            matched+=1
        elif candidates and not exact:
            conflicts.append(event.tx_hash)
            event=replace(event,usd_value=None,multi_step=True)
        result.append(event)
    result.sort(key=lambda e:(e.timestamp,e.id))
    return result,{'birdeye_events':len(birdeye_events),'helius_events':len(helius_events),
                   'overlapping_transactions':len(helius_hashes & set(groups)),
                   'historical_values_matched':matched,'conflicting_transactions':sorted(set(conflicts)),
                   'policy':'Uma representação Helius por assinatura observada; Birdeye só acrescenta USD histórico quando unidades e sentido coincidem exatamente.'}
