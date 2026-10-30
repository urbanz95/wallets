"""Excel privado e publicações anónimas; nenhuma dependência ou chamada de rede."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import csv
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from wallets.profit_reporting import HEADERS, public_summary, write_profit_reports

NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
WALLETS = ['A'*44, 'C'*44, 'D'*44]
TOKEN = 'B'*44
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def candidate(index=0, level='C', **overrides):
    result = {
        'wallet': WALLETS[index], 'rank': index+1, 'score': Decimal('72.15')-index,
        'evidence_level': level, 'assessment_passed': False,
        'origins': [TOKEN], 'score_components': {'profit_factor': Decimal('12.345')},
        'reasons': ['unknown_costs', 'Referência '+WALLETS[index]],
        'unverified': ['Liquidez de saída', 'Taxas USD por apurar'],
        'prefilter7': {'pnl_usd': Decimal('1234.567890123456789'), 'complete': True},
        'prefilter30': {'pnl_usd': None, 'complete': False},
        'deep': {'complete': level == 'A', 'period_start': NOW-timedelta(days=7),
                 'period_end': NOW, 'fetched_at': NOW,
                 'metrics': {'realized_sol': Decimal('1.123456789123456789'),
                             'worst_case_sol': Decimal('-0.05'),
                             'open_cost_sol': Decimal('1.173456789123456789'),
                             'known_fees_sol': Decimal('0.000005'),
                             'profit_factor': Decimal('1.3'), 'win_rate': Decimal('0.4'),
                             'median_return': Decimal('-0.1'), 'top1_share': Decimal('0.25'),
                             'top3_share': Decimal('0.6'), 'without_best_sol': Decimal('0.4'),
                             'without_top3_sol': Decimal('0.2'), 'unknown_cost_fraction': Decimal('0.1'),
                             'tokens_count': 40, 'known_tokens_count': 36},
                 'tokens': [{'token': TOKEN, 'worst_case_result_sol': Decimal('-0.5')}]},
    }
    result.update(overrides)
    return result


def telemetry():
    return {'started_at': NOW, 'stages': {'candidates': 90, 'pre_filter': 7,
            'pnl_details': 10, 'deep': 2, 'top3': 3},
            'rejections': {'too_many_trades': 12, 'unknown_costs': 2},
            'budget': {'discovery': {'state': 'settled', 'birdeye': {
                'reserved': 230, 'attempted': 90, 'refunded': 140, 'requests': 3},
                'helius': {'reserved': 0, 'attempted': 0, 'refunded': 0, 'requests': 0}}}}


class ProfitReportingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'reports'
        for target in ('socket.socket.connect', 'urllib.request.urlopen', 'urllib.request.OpenerDirector.open'):
            blocker = patch(target, side_effect=AssertionError('Rede proibida'))
            blocker.start(); self.addCleanup(blocker.stop)

    def sheet_rows(self, path, number=1):
        with ZipFile(path) as archive:
            document = ET.fromstring(archive.read(f'xl/worksheets/sheet{number}.xml'))
        return [[cell.find('s:is/s:t', NS).text or '' for cell in row.findall('s:c', NS)]
                for row in document.findall('s:sheetData/s:row', NS)]

    def test_three_available_records_stay_visible_when_no_assessment_passes(self):
        data = [candidate(0,'B'), candidate(1,'C'), candidate(2,'C')]
        result = write_profit_reports(data, telemetry(), self.root)
        self.assertEqual(set(result), {'xlsx','json','csv','html','public_summary'})
        rows = self.sheet_rows(result['xlsx'])
        self.assertEqual(len(rows), 4)
        self.assertEqual([row[3] for row in rows[1:]], WALLETS)
        self.assertTrue(all('Não concluída' in row[4] for row in rows[1:]))
        self.assertEqual(result['public_summary']['ranking_levels'], {'A':0,'B':1,'C':2})
        self.assertEqual(result['public_summary']['stages']['top3'], 3)
        self.assertEqual(result['public_summary']['telegram_sent'], 0)

    def test_xlsx_is_valid_zip_xml_with_full_addresses_precise_values_and_freeze(self):
        result = write_profit_reports([candidate()], telemetry(), self.root)
        rows = self.sheet_rows(result['xlsx'])
        self.assertEqual(rows[1][3], WALLETS[0])
        self.assertIn('1.123456789123456789', rows[1])
        self.assertIn('Por apurar', rows[1])
        with ZipFile(result['xlsx']) as archive:
            self.assertIsNone(archive.testzip())
            for name in archive.namelist():
                ET.fromstring(archive.read(name))
            workbook = ET.fromstring(archive.read('xl/workbook.xml'))
            self.assertEqual([s.attrib['name'] for s in workbook.find('s:sheets',NS)], ['Ranking','Métricas','Notas'])
            first = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
            self.assertEqual(first.find('s:sheetViews/s:sheetView/s:pane',NS).attrib['state'], 'frozen')
            self.assertIsNotNone(first.find('s:autoFilter',NS))
            self.assertIn(TOKEN, archive.read('xl/worksheets/sheet2.xml').decode())
        self.assertEqual(stat.S_IMODE(result['xlsx'].stat().st_mode), 0o600)

    def test_identity_is_only_in_excel_not_other_reports_or_public_summary(self):
        data = [candidate(0),candidate(1),candidate(2)]
        result = write_profit_reports(data, telemetry(), self.root)
        for key in ('csv','json','html'):
            value = result[key].read_text(encoding='utf-8-sig')
            for identity in WALLETS+[TOKEN]:
                self.assertNotIn(identity, value)
        self.assertNotIn('Carteira', json.loads(result['json'].read_text())['ranking'][0])
        public = json.dumps(result['public_summary'])
        for identity in WALLETS+[TOKEN]:self.assertNotIn(identity, public)

    def test_public_summary_drops_arbitrary_input_and_private_histogram_keys(self):
        values = telemetry()
        values['wallet'] = WALLETS[0]
        values['tokens'] = [TOKEN]
        values['stages'][WALLETS[1]] = 2
        values['budget']['deep'] = {'wallet':WALLETS[2], 'birdeye': {'reserved':True,'requests':3,'arbitrary':TOKEN}}
        values['rejections'].update({WALLETS[0]: 1, 'rejected_'+WALLETS[1]: 2,
                                    'see '+TOKEN: 3, 'valid_reason_code': 2})
        result = public_summary(values, [candidate()])
        text = json.dumps(result)
        for identity in WALLETS+[TOKEN]:self.assertNotIn(identity, text)
        self.assertEqual(result['rejections']['other'],6)
        self.assertEqual(result['rejections']['valid_reason_code'],2)
        self.assertNotIn('reserved',result['budget']['deep']['birdeye'])
        self.assertEqual(result['budget']['discovery']['birdeye']['attempted'],90)
        self.assertNotIn('state',result['budget']['discovery'])

    def test_formulas_markup_and_controls_are_literal_or_escaped(self):
        value = candidate(reasons=['=HYPERLINK("https://example.invalid")','<script>alert(1)</script>\x00'])
        result=write_profit_reports([value],telemetry(),self.root)
        with ZipFile(result['xlsx']) as archive:
            for name in archive.namelist():
                document=ET.fromstring(archive.read(name))
                self.assertFalse(any(element.tag.endswith('}f') for element in document.iter()))
        rows=self.sheet_rows(result['xlsx'])
        self.assertIn('=HYPERLINK', rows[1][-2])
        html=result['html'].read_text()
        self.assertNotIn('<script>',html)
        self.assertIn('&lt;script&gt;',html)
        with result['csv'].open(encoding='utf-8-sig',newline='') as handle:
            row=next(csv.DictReader(handle,delimiter=';'))
        self.assertTrue(row['Motivos e limitações'].startswith("'="))

    def test_empty_input_does_not_fabricate_three_entries(self):
        result=write_profit_reports([],{'stages':{},'rejections':{}},self.root)
        self.assertEqual(len(self.sheet_rows(result['xlsx'])),1)
        self.assertEqual(json.loads(result['json'].read_text())['ranking'],[])
        self.assertIn('Não existem posições',result['html'].read_text())
        self.assertEqual(result['public_summary']['stages']['top3'],0)

    def test_limits_output_to_three_preserving_input_order(self):
        result=write_profit_reports([candidate(0),candidate(1),candidate(2),candidate(0)],telemetry(),self.root)
        self.assertEqual(len(self.sheet_rows(result['xlsx'])),4)

    def test_level_a_still_contains_unverified_and_never_claims_telegram_approval(self):
        value=candidate(0,'A',assessment_passed=True)
        result=write_profit_reports([value],telemetry(),self.root)
        rows=self.sheet_rows(result['xlsx'])
        self.assertEqual(rows[1][2],'A')
        self.assertIn('Taxas USD',rows[1][-1])
        notes=' '.join(' '.join(row) for row in self.sheet_rows(result['xlsx'],3))
        self.assertIn('Nenhum nível é aprovação integral',notes)
        self.assertIn('Desligado nesta experiência',notes)
        self.assertIn('câmbio SOL/USD',notes)

    def test_date_presentation_uses_lisbon_and_public_time_is_validated(self):
        value=candidate(); value['deep']['fetched_at']=datetime(2026,7,1,12,tzinfo=timezone.utc)
        result=write_profit_reports([value],telemetry(),self.root)
        self.assertIn('01/07/2026 13:00:00 WEST',self.sheet_rows(result['xlsx'])[1])
        bad=telemetry();bad['started_at']=WALLETS[0]
        self.assertNotIn('started_at',public_summary(bad))

    def test_unknown_costs_remain_unknown_while_open_loss_scenario_is_signed(self):
        value=candidate();value['deep']['metrics']['realized_sol']=None
        result=write_profit_reports([value],telemetry(),self.root)
        row=self.sheet_rows(result['xlsx'])[1]
        self.assertEqual(row[list(HEADERS).index('Resultado realizado identificado (SOL)')],'Por apurar')
        self.assertEqual(row[list(HEADERS).index('Resultado conservador, incluindo abertos (SOL)')],'-0.05')
        self.assertEqual(row[list(HEADERS).index('Custo restante das posições abertas (SOL)')],'1.173456789123456789')


if __name__ == '__main__':
    unittest.main()
