"""Pré-visualizações observadas são locais; nenhum teste contacta Telegram."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import Mock, patch

from wallets.models import Check, Coverage, Evaluation, REQUIRED_CHECKS, Status, dumps
from wallets.storage import Store
from wallets.telegram import Notifier, eligible_for_delivery, format_message, format_observed_preview

NOW=datetime(2026,10,29,12,tzinfo=timezone.utc)
ADDRESS='So11111111111111111111111111111111111111112'
NAMES=('observed_data','observed_sample','observed_accounting','observed_consistency',
       'observed_concentration','observed_scope','observed_automation','observed_followability')
UNVERIFIED=('opening_inventory','fees_usd','global_coverage','open_positions',
            'liquidity','execution','coordination','labels')


def observed_snapshot():
    config=tomllib.loads((Path(__file__).parents[1]/'config.toml').read_text())
    config=deepcopy(config)
    config['selection']={'mode':'observed','min_closed_token_fraction':0.8}
    coverage=Coverage(NOW-timedelta(days=28),NOW,NOW,source='birdeye')
    strict=[Check(name,Status.UNKNOWN,'Verificação global não demonstrada.',{},coverage.start,
                  coverage.end,asdict(coverage),NOW,config['criteria_version']) for name in REQUIRED_CHECKS]
    decision=[asdict(Check(name,Status.PASS,'Critério observado cumprido.',{},coverage.start,
                         coverage.end,asdict(coverage),NOW,config['criteria_version'])) for name in NAMES]
    metrics={'closed_tokens_count':20,'observed_tokens_count':22,'open_tokens_count':1,
             'unknown_tokens_count':1,'closed_fraction':Decimal(20)/Decimal(22),
             'gross_result_before_fees':Decimal(200),'without_best':Decimal(190),
             'without_top3':Decimal(170),'without_discovery':Decimal(200),
             'top1_share':Decimal('0.05'),'top3_share':Decimal('0.15'),
             'median_holding_seconds':Decimal(900),'automation':{'rapid_close_fraction':Decimal('0.05')},
             'excluded_tokens':[{'token':'open','state':'OPEN'},{'token':'unknown','state':'UNKNOWN'}]}
    selection={'policy':'observed-v1','checks':decision,'metrics':metrics,
               'scope':'Ciclos fechados observados; resultados brutos, antes de taxas.',
               'unverified':[{'name':name,'reason':'Não verificado nesta seleção.'} for name in UNVERIFIED]}
    result=Evaluation(ADDRESS,NOW,config['criteria_version'],coverage,strict,
                      {'selection':selection,'_criteria_config':deepcopy(config),
                       '_criteria_hash':sha256(dumps(config).encode()).hexdigest(),
                       '_events_hash':sha256(dumps([]).encode()).hexdigest(),
                       'provider_metrics':{'pages_complete':{'trades':True,'transfers':True}}},source='birdeye')
    return result,config


class ObservedPreviewTests(unittest.TestCase):
    def setUp(self):
        for target in ('urllib.request.urlopen','urllib.request.OpenerDirector.open','socket.socket.connect'):
            blocked=patch(target,side_effect=AssertionError('Rede proibida neste teste'))
            blocked.start();self.addCleanup(blocked.stop)

    def test_preview_is_explicitly_local_and_does_not_describe_net_wallet_profit(self):
        result,_=observed_snapshot()
        text=format_observed_preview(result)
        self.assertIn('PRÉ-VISUALIZAÇÃO LOCAL — SEM ENVIO',text)
        self.assertIn('CARTEIRA SELECIONADA — DADOS OBSERVADOS',text)
        self.assertIn('<code>'+ADDRESS+'</code>',text)
        self.assertIn('Rede: Solana',text)
        self.assertIn('20 ciclos fechados de 22 observados',text)
        self.assertIn('antes de taxas: 200.00 USD',text)
        self.assertIn('Não é lucro líquido nem resultado global',text)
        self.assertIn('sem os três melhores: 170.00 USD',text)
        self.assertIn('1 abertos; 1 desconhecidos',text)
        self.assertIn('não apurados nesta seleção',text)
        self.assertNotIn('perdas abertas: 0',text)
        self.assertIn('8/8 critérios observáveis',text)
        self.assertLessEqual(len(text),4096)

    def test_all_essential_limitations_are_present_without_free_text_leaks(self):
        result,_=observed_snapshot()
        result.metrics['selection']['unverified'][0]['reason']='PRIVATE-SENTINEL'*10000
        text=format_observed_preview(result)
        for warning in ('inventário inicial','taxas históricas totais em USD','cobertura integral',
                        'posições abertas','liquidez de saída','execução reproduzível',
                        'coordenação/MEV','completude das etiquetas','Identidade humana não confirmada'):
            self.assertIn(warning,text)
        self.assertNotIn('PRIVATE-SENTINEL',text)
        self.assertLessEqual(len(text),4096)

    def test_preview_keeps_full_address_dates_links_and_escapes_html(self):
        result,_=observed_snapshot()
        text=format_observed_preview(result)
        self.assertIn('01/10/2026 13:00 WEST',text)
        self.assertIn('29/10/2026 12:00 WET',text)
        self.assertIn('https://solscan.io/account/'+ADDRESS,text)
        self.assertIn('https://birdeye.so/profile/'+ADDRESS,text)
        result.wallet='<wallet&"bad>'
        result.criteria_version='criteria<&'
        text=format_observed_preview(result)
        self.assertIn('&lt;wallet&amp;&quot;bad&gt;',text)
        self.assertIn('criteria&lt;&amp;',text)
        self.assertNotIn('<wallet',text)

    def test_oversized_preview_is_rejected_instead_of_truncating_address_or_warnings(self):
        result,_=observed_snapshot()
        result.criteria_version='very-long-version'*500
        with self.assertRaises(ValueError):format_observed_preview(result)

    def test_wrong_policy_cannot_be_rendered_as_observed_preview(self):
        result,_=observed_snapshot()
        del result.metrics['selection']
        with self.assertRaises(ValueError):format_observed_preview(result)

    def test_observed_checks_and_false_global_flags_do_not_authorize_delivery(self):
        result,config=observed_snapshot()
        self.assertTrue(all(check['status']==Status.PASS for check in result.metrics['selection']['checks']))
        self.assertFalse(result.coverage.complete)
        self.assertFalse(eligible_for_delivery(result,config,NOW))
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        with Store(Path(temporary.name)/'state.sqlite') as store:
            store.save_evaluation(result)
            transport=Mock(return_value='mock-id')
            checkpoint=Mock()
            notifier=Notifier(store,config,'mock-token','mock-chat',checkpoint=checkpoint,
                              transport=transport,clock=lambda:NOW)
            self.assertFalse(notifier.send(result))
            transport.assert_not_called();checkpoint.assert_not_called()
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM notifications').fetchone()[0],0)

    def test_existing_send_formatter_is_not_wired_to_observed_preview(self):
        result,_=observed_snapshot()
        with patch('wallets.telegram.format_observed_preview',side_effect=AssertionError('Prévia não pode ser enviada')):
            text=format_message(result)
        self.assertNotIn('PRÉ-VISUALIZAÇÃO LOCAL',text)
        self.assertNotIn('CARTEIRA SELECIONADA — DADOS OBSERVADOS',text)

    def test_missing_or_non_finite_financial_values_stay_unknown(self):
        result,_=observed_snapshot()
        metrics=result.metrics['selection']['metrics']
        metrics['gross_result_before_fees']=None
        metrics['without_best']='NaN'
        metrics['top1_share']='Infinity'
        metrics['median_holding_seconds']='<private-metric>'
        text=format_observed_preview(result)
        self.assertIn('antes de taxas: desconhecido',text)
        self.assertIn('Sem o melhor token: desconhecido',text)
        self.assertIn('melhor desconhecida',text)
        self.assertIn('Manutenção mediana: desconhecida',text)
        for invalid in ('NaN','Infinity','private-metric'):
            self.assertNotIn(invalid,text)
