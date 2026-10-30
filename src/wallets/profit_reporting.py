"""Relatórios do ranking: identidades só no Excel privado e no estado cifrado.

CSV, HTML e JSON usam apenas posições anónimas. O resumo público é construído
por allowlist de contadores; não copia motivos livres, carteiras ou tokens.
O XLSX usa apenas a biblioteca padrão e todas as células são texto, sem fórmulas.
"""
from __future__ import annotations

import csv
from decimal import Decimal
from html import escape
import math
import os
from pathlib import Path
import re
from zipfile import ZIP_DEFLATED, ZipFile
from zoneinfo import ZoneInfo

from .models import dumps, parse_time

LISBON = ZoneInfo('Europe/Lisbon')
STAGES = frozenset(('candidates', 'pre_filter', 'prefilter', 'pnl_details', 'persistence',
                    'pnl_responses', 'prefilter_passed', 'deep', 'top3', 'discovered', 'prefiltered', 'pnl_7d', 'pnl_30d'))
BUDGET_KEYS = frozenset(('discovery', 'prefilter', 'persistence', 'deep', 'total', 'totals',
                         'stages', 'by_stage', 'birdeye', 'helius', 'reserved', 'attempted',
                         'refunded', 'requests', 'used', 'consumed', 'credits', 'cu',
                         'reserved_credits', 'attempted_credits', 'refunded_credits'))
PUBLIC_REASON = re.compile(r'^[a-z]{1,24}(?:_[a-z0-9]{1,24})*$')
ADDRESS = re.compile(r'[1-9A-HJ-NP-Za-km-z]{32,44}')
XML_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')
UNKNOWN = 'Por apurar'

METRICS = (
    ('realized_sol', 'Resultado realizado identificado (SOL)'),
    ('worst_case_sol', 'Resultado conservador, incluindo abertos (SOL)'),
    ('open_cost_sol', 'Custo restante das posições abertas (SOL)'),
    ('known_fees_sol', 'Taxas identificadas (SOL)'),
    ('profit_factor', 'Profit factor — cenário conservador'),
    ('win_rate', 'Taxa de sucesso por token'),
    ('median_return', 'Retorno mediano por token'),
    ('top1_share', 'Concentração do melhor na soma dos ganhos'),
    ('top3_share', 'Concentração dos três melhores na soma dos ganhos'),
    ('without_best_sol', 'Resultado sem o melhor token (SOL)'),
    ('without_top3_sol', 'Resultado sem os três melhores (SOL)'),
    ('unknown_cost_fraction', 'Fração de tokens com custo desconhecido'),
    ('tokens_count', 'Tokens distintos observados'),
    ('known_tokens_count', 'Tokens com contabilidade conhecida'),
)
HEADERS = ('Posição', 'Pontuação', 'Nível de evidência', 'Carteira', 'Resultado da avaliação',
           'PnL fornecedor 7d (USD)', 'PnL fornecedor 30d (USD)') + tuple(label for _, label in METRICS) + (
           'Início (Lisboa)', 'Fim (Lisboa)', 'Recolha (Lisboa)', 'Histórico profundo completo',
           'Motivos e limitações', 'NÃO VERIFICADO')


def _text(value):
    if value is None:
        return UNKNOWN
    if isinstance(value, bool):
        return 'Sim' if value else 'Não'
    if isinstance(value, (list, tuple)):
        return ' | '.join(_text(item) for item in value) or 'Nenhum motivo adicional'
    if isinstance(value, dict):
        return dumps(value)
    return str(value)


def _local(value):
    try:
        return parse_time(value).astimezone(LISBON).strftime('%d/%m/%Y %H:%M:%S %Z')
    except (ValueError, TypeError, OverflowError):
        return UNKNOWN


def _map(value):
    return value if isinstance(value, dict) else {}


def _anonymous(text, identities):
    text = _text(text)
    for identity in sorted(identities, key=len, reverse=True):
        if identity:
            text = text.replace(identity, '[endereço omitido]')
    return ADDRESS.sub('[endereço omitido]', text)


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    try:
        return value if value >= 0 and math.isfinite(value) else None
    except (ValueError, OverflowError, TypeError):
        return None


def _budget_counts(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key not in BUDGET_KEYS:
                continue
            clean = _budget_counts(item)
            if clean is not None:
                result[key] = clean
        return result
    number = _number(value)
    if number is None:
        return None
    return int(number) if number == int(number) else float(number)


def public_summary(telemetry, ranking=()):
    """Resumo publicável, sem copiar campos livres ou identidades de entrada."""
    telemetry = _map(telemetry)
    stages = {name: value for name, value in _map(telemetry.get('stages')).items()
              if name in STAGES and type(value) is int and value >= 0}
    stages.setdefault('top3', min(3, len(ranking)))
    histogram = {}
    for reason, count in _map(telemetry.get('rejections')).items():
        if type(count) is not int or count < 0:
            continue
        key = reason if isinstance(reason, str) and PUBLIC_REASON.fullmatch(reason) else 'other'
        histogram[key] = histogram.get(key, 0) + count
    levels = {level: 0 for level in ('A', 'B', 'C')}
    for row in ranking[:3]:
        level = row.get('evidence_level') if isinstance(row, dict) else None
        if level in levels:
            levels[level] += 1
    result = {'profile': 'profit-evidence-v1', 'stages': stages,
              'budget': _budget_counts(_map(telemetry.get('budget'))),
              'rejections': dict(sorted(histogram.items())), 'ranking_levels': levels,
              'telegram_sent': 0}
    try:
        result['started_at'] = parse_time(telemetry['started_at']).isoformat()
    except (KeyError, ValueError, TypeError, OverflowError):
        pass
    return result


def _column(index):
    result = ''
    while index:
        index, rest = divmod(index-1, 26)
        result = chr(65+rest)+result
    return result


def _xml_text(value):
    text = XML_CONTROL.sub('', _text(value))
    if len(text) > 32767:
        text = text[:32690]+' [texto truncado; consultar estado cifrado]'
    return escape(text, quote=True)


def _sheet(rows, widths):
    end = _column(max(len(row) for row in rows)) + str(len(rows))
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
             '<dimension ref="A1:'+end+'"/><sheetViews><sheetView workbookViewId="0">',
             '<pane xSplit="1" ySplit="1" topLeftCell="B2" activePane="bottomRight" state="frozen"/>',
             '</sheetView></sheetViews><sheetFormatPr defaultRowHeight="24"/><cols>']
    for number, width in enumerate(widths, 1):
        parts.append(f'<col min="{number}" max="{number}" width="{width}" customWidth="1"/>')
    parts.append('</cols><sheetData>')
    for index, row in enumerate(rows, 1):
        height = '54' if index == 1 else '96'
        parts.append(f'<row r="{index}" ht="{height}" customHeight="1">')
        for number, value in enumerate(row, 1):
            style = '1' if index == 1 else '0'
            parts.append(f'<c r="{_column(number)}{index}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{_xml_text(value)}</t></is></c>')
        parts.append('</row>')
    parts += ['</sheetData><autoFilter ref="A1:'+end+'"/></worksheet>']
    return ''.join(parts)


def _xlsx(path, sheets):
    ns = 'http://schemas.openxmlformats.org'
    content = ['<?xml version="1.0" encoding="UTF-8"?>',
               f'<Types xmlns="{ns}/package/2006/content-types">',
               '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
               '<Default Extension="xml" ContentType="application/xml"/>',
               '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
               '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>']
    for index in range(1, len(sheets)+1):
        content.append(f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
    content.append('</Types>')
    workbook = [f'<workbook xmlns="{ns}/spreadsheetml/2006/main" xmlns:r="{ns}/officeDocument/2006/relationships"><sheets>']
    relationships = [f'<Relationships xmlns="{ns}/package/2006/relationships">']
    for index, (name, rows, widths) in enumerate(sheets, 1):
        workbook.append(f'<sheet name="{escape(name, quote=True)}" sheetId="{index}" r:id="rId{index}"/>')
        relationships.append(f'<Relationship Id="rId{index}" Type="{ns}/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>')
    workbook.append('</sheets></workbook>')
    relationships.append(f'<Relationship Id="rId{len(sheets)+1}" Type="{ns}/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
    styles = f'''<styleSheet xmlns="{ns}/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF17365D"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'''
    with ZipFile(path, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', ''.join(content))
        archive.writestr('_rels/.rels', f'<Relationships xmlns="{ns}/package/2006/relationships"><Relationship Id="rId1" Type="{ns}/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr('xl/workbook.xml', ''.join(workbook))
        archive.writestr('xl/_rels/workbook.xml.rels', ''.join(relationships))
        archive.writestr('xl/styles.xml', styles)
        for index, (_, rows, widths) in enumerate(sheets, 1):
            archive.writestr(f'xl/worksheets/sheet{index}.xml', _sheet(rows, widths))


def write_profit_reports(ranking, telemetry, output):
    """Escrever até três posições, mesmo sem nível A; não inventar entradas."""
    ranking = [row for row in ranking if isinstance(row, dict)][:3]
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    os.chmod(output, 0o700)
    identities = {str(row.get('wallet', '')) for row in ranking}
    for row in ranking:
        identities.update(value for value in row.get('origins', []) if isinstance(value, str))
    excel_rows = [HEADERS]
    anonymous_rows = []
    metric_rows = [('Posição', 'Âmbito', 'Métrica', 'Valor')]
    for index, candidate in enumerate(ranking, 1):
        wallet = _text(candidate.get('wallet'))
        if len(wallet) > 32767:
            raise ValueError('Endereço excede o limite da célula Excel; identidade não truncada')
        deep = _map(candidate.get('deep'))
        metrics = _map(deep.get('metrics'))
        pre7, pre30 = _map(candidate.get('prefilter7')), _map(candidate.get('prefilter30'))
        state = 'Cumpre critérios observáveis' if candidate.get('assessment_passed') is True else 'Não concluída ou reprovada'
        row = [_text(candidate.get('rank', index)), _text(candidate.get('score')),
               _text(candidate.get('evidence_level', 'C')), wallet, state,
               _text(pre7.get('pnl_usd')), _text(pre30.get('pnl_usd'))]
        row += [_text(metrics.get(key)) for key, _ in METRICS]
        row += [_local(deep.get('period_start')), _local(deep.get('period_end')),
                _local(deep.get('fetched_at')), _text(deep.get('complete')),
                _text(candidate.get('reasons', [])), _text(candidate.get('unverified', []))]
        excel_rows.append(row)
        anonymous = {header: _anonymous(value, identities) for header, value in zip(HEADERS, row) if header != 'Carteira'}
        anonymous_rows.append(anonymous)
        metric_rows.append((index, 'Descoberta', 'Tokens de origem', _text(candidate.get('origins', []))))
        for token in deep.get('tokens', []) or []:
            if isinstance(token, dict):
                metric_rows.append((index, 'Resultado por token em SOL', _text(token.get('token')), _text(token)))
        for key, value in _map(candidate.get('score_components')).items():
            metric_rows.append((index, 'Pontuação', _text(key), _text(value)))
        for key, label in METRICS:
            metric_rows.append((index, 'FIFO em SOL / cenário conservador', label, _text(metrics.get(key))))
        for label, prefilter in (('Birdeye 7d — fornecido, não FIFO', pre7), ('Birdeye 30d — fornecido, não FIFO', pre30)):
            for key in ('pnl_usd', 'tokens_count', 'win_rate', 'profit_factor', 'top1_share', 'top3_share', 'trade_count', 'trades_per_day', 'complete'):
                metric_rows.append((index, label, key, _text(prefilter.get(key))))
    notes = [('Informação', 'Como interpretar'),
             ('Níveis A/B/C', 'A: cumpre os critérios verificáveis nesta avaliação; B: pré-filtro e análise profunda parcial; C: só pré-filtro. Nenhum nível é aprovação integral ou autorização Telegram.'),
             ('Objetivo do ranking', 'Mostrar até três posições ordenadas com evidência e motivos, mesmo sem carteiras de nível A. Posição e pontuação não compensam uma verificação reprovada ou desconhecida.'),
             ('Unidade SOL', 'Resultado recalculado em SOL. A evolução do câmbio SOL/USD pode mudar os sinais e as métricas; o resultado em USD permanece NÃO VERIFICADO.'),
             ('PnL do fornecedor', 'Os valores USD de 7d/30d são métricas Birdeye WAC fornecidas, usadas para ordenar e pré-filtrar. Não são a contabilidade própria em SOL nem prova de lucro líquido.'),
             ('Posições abertas', 'O cenário conservador subtrai todo o custo restante das posições abertas ao resultado realizado. Não afirma que o valor de mercado seja zero. O valor marcado, quando conhecido, é distinto.'),
             ('Custos e taxas', 'Taxas SOL identificadas são mostradas separadamente. Custos ou movimentos não reconciliados continuam desconhecidos; transferências não se tornam compras gratuitas.'),
             ('Persistência', 'PnL positivo em 7d e 30d é apenas uma indicação do fornecedor: são janelas sobrepostas e não demonstram consistência em períodos independentes.'),
             ('Identidades e privacidade', 'Endereços apenas neste Excel privado e no estado cifrado. CSV/JSON/HTML usam posições anónimas. O resumo publicável contém só contagens e custos.'),
             ('Datas', 'Datas apresentadas em Europe/Lisbon; armazenamento UTC. As datas referem-se aos dados da avaliação, sem informação futura.'),
             ('Telegram', 'Desligado nesta experiência; política estrita original preservada.'),
             ('Utilização', 'Usa os filtros da primeira linha. Seleciona uma célula para ler o texto completo na barra do Excel. Todas as células são texto, sem fórmulas ou macros.')]
    paths = {kind: output/filename for kind, filename in (
        ('xlsx', 'ranking_carteiras.xlsx'), ('csv', 'ranking_anonimo.csv'),
        ('json', 'ranking_anonimo.json'), ('html', 'ranking_anonimo.html'))}
    _xlsx(paths['xlsx'], [('Ranking', excel_rows, [12, 15, 18, 58]+[30]*(len(HEADERS)-4)),
                            ('Métricas', metric_rows, [12, 48, 54, 70]), ('Notas', notes, [30, 115])])
    anonymous_headers = [header for header in HEADERS if header != 'Carteira']
    with paths['csv'].open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=anonymous_headers, delimiter=';')
        writer.writeheader()
        for row in anonymous_rows:
            writer.writerow({key: "'"+value if value.lstrip().startswith(('=', '+', '-', '@')) else value for key, value in row.items()})
    summary = public_summary(telemetry, ranking)
    paths['json'].write_text(dumps({'ranking': anonymous_rows, 'summary': summary,
                                    'identities': 'Apenas no Excel privado e no estado cifrado.'}), encoding='utf-8')
    html = ['<!doctype html><html lang="pt-PT"><meta charset="utf-8"><title>Ranking anónimo</title>',
            '<style>body{font:16px system-ui;max-width:1200px;margin:2rem auto;padding:1rem}article{border:1px solid #ccc;padding:1rem;margin:1rem 0}dt{font-weight:700;margin-top:.7rem}dd{overflow-wrap:anywhere;white-space:pre-wrap}</style>',
            '<h1>Ranking com evidência — sem endereços</h1><p>Identidades apenas no Excel privado e no estado cifrado. A/B/C não autorizam Telegram. PnL USD do fornecedor não substitui FIFO em SOL.</p>']
    if not ranking:
        html.append('<p>Não existem posições avaliadas para apresentar. Não foram inventadas carteiras.</p>')
    for row in anonymous_rows:
        html.append('<article><dl>')
        for key, value in row.items():
            html.append('<dt>'+escape(key)+'</dt><dd>'+escape(value)+'</dd>')
        html.append('</dl></article>')
    html.append('</html>')
    paths['html'].write_text('\n'.join(html), encoding='utf-8')
    for path in paths.values():
        os.chmod(path, 0o600)
    return dict(paths, public_summary=summary)
