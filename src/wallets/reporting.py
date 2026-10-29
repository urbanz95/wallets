"""Relatórios locais: inclui candidatas/inconclusivas; nunca enviado ao Telegram."""
from csv import DictWriter
from html import escape
import json
from pathlib import Path
from .models import dumps, EVALUATION_SOURCES
from .telegram import local_time, format_message, format_observed_preview

def write_reports(evaluations, output, *, notes=(), discoveries=()):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    evaluations=list(evaluations)
    report={'evaluations':[e.to_dict() for e in evaluations], 'notes':list(notes),
            'discoveries':list(discoveries),'timezone_display':'Europe/Lisbon','timezone_storage':'UTC'}
    (output/'report.json').write_text(dumps(report),encoding='utf-8')
    fields=['wallet','evaluated_at_lisbon','eligible_checks','source','tokens','realized','without_best','top1_share','top3_share',
            'selection_policy','observed_selected','strict_eligible','observed_gross_result_before_fees',
            'observed_closed_tokens','observed_total_tokens','observed_without_best','observed_without_top3',
            'observed_top1_share','observed_top3_share','observed_excluded_tokens','observed_block_reasons',
            'strict_block_reasons','unverified']
    with (output/'report.csv').open('w',encoding='utf-8',newline='') as handle:
        writer=DictWriter(handle,fieldnames=fields);writer.writeheader()
        for e in evaluations:
            m=e.metrics
            selection=m.get('selection') if isinstance(m.get('selection'),dict) else {}
            observed=selection.get('metrics') if isinstance(selection.get('metrics'),dict) else {}
            writer.writerow(dict(wallet=e.wallet,evaluated_at_lisbon=local_time(e.evaluated_at),
                eligible_checks=e.eligible,source=e.source,tokens=m.get('tokens_count'),realized=m.get('realized_total'),
                without_best=m.get('without_best'),top1_share=m.get('top1_share'),top3_share=m.get('top3_share'),
                selection_policy=e.selection_policy,observed_selected=e.observed_eligible,strict_eligible=e.strict_eligible,
                observed_gross_result_before_fees=observed.get('gross_result_before_fees'),
                observed_closed_tokens=observed.get('closed_tokens_count'),observed_total_tokens=observed.get('observed_tokens_count'),
                observed_without_best=observed.get('without_best'),observed_without_top3=observed.get('without_top3'),
                observed_top1_share=observed.get('top1_share'),observed_top3_share=observed.get('top3_share'),
                observed_excluded_tokens=dumps(observed.get('excluded_tokens',[])),
                observed_block_reasons=' | '.join(c.name+': '+c.status.value+' — '+c.reason for c in e.decision_checks if c.status.value!='APROVADA'),
                strict_block_reasons=' | '.join(c.name+': '+c.status.value+' — '+c.reason for c in e.checks if c.status.value!='APROVADA'),
                unverified=' | '.join(item['name']+': '+item['reason'] for item in e.unverified_checks)))
    parts=['<!doctype html><html lang="pt-PT"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
        '<title>Análise de carteiras Solana</title><style>body{font:16px system-ui;margin:2rem auto;padding:0 1rem;max-width:1100px;color:#172537;background:#f7f9fc}article{padding:1.3rem;background:white;margin:1.5rem 0;border:1px solid #cdd5df;border-radius:8px}td,th{text-align:left;padding:.6rem;border-bottom:1px solid #ddd;vertical-align:top}table{width:100%;border-collapse:collapse}code,pre{overflow-wrap:anywhere;white-space:pre-wrap}summary{cursor:pointer}h1{font-size:1.8rem}li{margin:.5rem 0}</style>',
        '<h1>Análise de carteiras Solana</h1><p>Fontes autorizadas: Birdeye Standard (mercado e dados on-chain) e Helius Free (histórico on-chain). Datas apresentadas em Europe/Lisbon; dados guardados em UTC.</p>',
        '<p>Uma aprovação só significa que os critérios documentados passaram naquela avaliação. Não garante rentabilidade, ausência de fraude ou identidade humana.</p><ul>']
    parts += ['<li>'+escape(str(note))+'</li>' for note in notes]
    parts+=['</ul>']
    if not evaluations:parts+=['<p>Nenhuma avaliação disponível. Não há qualquer mensagem a enviar ao Telegram.</p>']
    for e in evaluations:
        state=('APROVADA NAS VERIFICAÇÕES INTEGRAIS' if e.eligible else
               'SELECIONADA NOS CRITÉRIOS OBSERVADOS — APENAS RELATÓRIO' if e.observed_eligible else 'BLOQUEADA PARA TELEGRAM')
        if e.source not in EVALUATION_SOURCES:state='DADOS FICTÍCIOS — NUNCA ENVIÁVEIS'
        parts += [f'<article><h2><code>{escape(e.wallet)}</code></h2><p><b>{state}</b></p>',
            f'<p>Avaliação: {escape(local_time(e.evaluated_at))} | critérios {escape(e.criteria_version)} | fonte {escape(e.source)}</p>',
            '<p>Telegram exige as oito verificações integrais; seleção observada é avaliação local.</p>']
        if e.selection_policy=='observed-v1':
            parts+=['<h3>Critérios observados — resultado bruto, sem aprovação integral</h3><table><tr><th>Verificação</th><th>Estado</th><th>Motivo</th></tr>']
            for c in e.decision_checks:parts+=[f'<tr><td>{escape(c.name)}</td><td>{c.status.value}</td><td>{escape(c.reason)}</td></tr>']
            parts+=['</table><p><b>NÃO VERIFICADO</b></p><ul>']
            parts+=['<li>'+escape(item['reason'])+'</li>' for item in e.unverified_checks]
            parts+=['</ul>']
        parts+=['<h3>Verificações integrais para Telegram</h3><table><tr><th>Verificação obrigatória</th><th>Estado</th><th>Motivo</th></tr>']
        for c in e.checks:parts+=[f'<tr><td>{escape(c.name)}</td><td>{c.status.value}</td><td>{escape(c.reason)}</td></tr>']
        parts+=['</table><details><summary>Métricas, critérios, cobertura e evidências</summary><pre>'+escape(json.dumps(json.loads(dumps(e.to_dict())),indent=2,ensure_ascii=False))+'</pre></details>',
            '<details><summary>Mensagem integral (sem envio)</summary>'+(format_message(e) if e.eligible else '<p>Sem mensagem: a carteira não passou todas as verificações integrais.</p>')+'</details>']
        if e.observed_eligible:
            parts+=['<details><summary>Pré-visualização observada LOCAL — envio não ativado</summary>'+format_observed_preview(e)+'</details>']
        parts+=['</article>']
    parts+=['</html>']
    (output/'report.html').write_text('\n'.join(parts),encoding='utf-8')
    return output/'report.html'
