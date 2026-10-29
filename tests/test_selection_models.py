"""Seleção observada local não altera a elegibilidade estrita de notificação."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import unittest

from wallets.models import (
    Check, Coverage, Evaluation, OBSERVED_REQUIRED_CHECKS, OBSERVED_UNVERIFIED_NAMES, REQUIRED_CHECKS,
    Status, dumps,
)

NOW = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)


def make_check(name, status, coverage):
    return Check(name, status, 'Motivo registado.', {'amount': Decimal('12.34')},
                 coverage.start, coverage.end, asdict(coverage), NOW, 'criteria-v3')


def make_evaluation(strict_status=Status.UNKNOWN, observed=True):
    coverage = Coverage(NOW - timedelta(days=30), NOW, NOW, source='birdeye+helius')
    evaluation = Evaluation('A' * 44, NOW, 'criteria-v3', coverage,
                            [make_check(name, strict_status, coverage) for name in REQUIRED_CHECKS],
                            {}, source='birdeye+helius')
    if observed:
        evaluation.metrics['selection'] = {
            'policy': 'observed-v1',
            'checks': [asdict(make_check(name, Status.PASS, coverage))
                       for name in OBSERVED_REQUIRED_CHECKS],
            'metrics': {'gross_result_before_fees': Decimal('1000.50')},
            'unverified': [{'name': name, 'reason': 'Prova global por apurar: '+name}
                           for name in OBSERVED_UNVERIFIED_NAMES],
            'scope': 'Operações observadas fechadas, resultado bruto antes de taxas.',
        }
    return evaluation


class SelectionModelTests(unittest.TestCase):
    def test_old_snapshots_keep_the_original_strict_notification_gate(self):
        evaluation = make_evaluation(Status.PASS, observed=False)
        self.assertEqual(evaluation.selection_policy, 'strict-v1')
        self.assertTrue(evaluation.strict_eligible)
        self.assertTrue(evaluation.eligible)
        self.assertFalse(evaluation.observed_eligible)
        self.assertEqual(evaluation.decision_checks, evaluation.checks)
        self.assertEqual(evaluation.unverified_checks, [])
        evaluation.checks[0].status = Status.UNKNOWN
        self.assertFalse(evaluation.strict_eligible)
        self.assertFalse(evaluation.eligible)

    def test_observed_selection_can_pass_without_faking_global_coverage_or_allowing_delivery(self):
        evaluation = make_evaluation()
        self.assertFalse(evaluation.coverage.complete)
        self.assertFalse(evaluation.coverage.all_tokens)
        self.assertFalse(evaluation.coverage.fees_complete)
        self.assertTrue(evaluation.observed_eligible)
        self.assertFalse(evaluation.strict_eligible)
        self.assertFalse(evaluation.eligible)
        self.assertEqual(evaluation.selection_policy, 'observed-v1')
        self.assertEqual(tuple(check.name for check in evaluation.decision_checks), OBSERVED_REQUIRED_CHECKS)
        self.assertEqual(tuple(item['name'] for item in evaluation.unverified_checks),
                         OBSERVED_UNVERIFIED_NAMES)
        self.assertTrue(all(check.status == Status.UNKNOWN for check in evaluation.checks))

    def test_strict_pass_does_not_compensate_a_failed_or_unknown_local_selection_check(self):
        for index in range(len(OBSERVED_REQUIRED_CHECKS)):
            for status in (Status.FAIL, Status.UNKNOWN):
                evaluation = make_evaluation(Status.PASS)
                evaluation.metrics['selection']['checks'][index]['status'] = status
                with self.subTest(index=index, status=status):
                    self.assertTrue(evaluation.strict_eligible)
                    self.assertFalse(evaluation.observed_eligible)

    def test_proved_strict_failure_vetoes_even_a_locally_passing_selection(self):
        for index in range(len(REQUIRED_CHECKS)):
            evaluation = make_evaluation()
            evaluation.checks[index].status = Status.FAIL
            with self.subTest(index=index):
                self.assertFalse(evaluation.observed_eligible)
                self.assertFalse(evaluation.eligible)

    def test_missing_duplicate_or_injected_local_checks_fail_closed(self):
        for mutation in ('missing', 'duplicate', 'extra', 'strict-name'):
            evaluation = make_evaluation(Status.PASS)
            checks = evaluation.metrics['selection']['checks']
            if mutation == 'missing':
                checks.pop()
            elif mutation == 'duplicate':
                checks[-1] = deepcopy(checks[0])
            elif mutation == 'extra':
                checks.append(deepcopy(checks[0]))
            else:
                checks[0]['name'] = REQUIRED_CHECKS[0]
            evaluation.metrics['selection']['eligible'] = True
            with self.subTest(mutation=mutation):
                self.assertFalse(evaluation.observed_eligible)

    def test_unknown_or_malformed_selection_never_inherits_a_strict_pass(self):
        for selection in (None, [], '', {}, {'policy': None}, {'policy': 'strict-v1'},
                          {'policy': 'future-policy'}, {'policy': ''}):
            evaluation = make_evaluation(Status.PASS, observed=False)
            evaluation.metrics['selection'] = selection
            with self.subTest(selection=selection):
                self.assertTrue(evaluation.strict_eligible)
                self.assertEqual(evaluation.decision_checks, [])
                self.assertFalse(evaluation.observed_eligible)
        evaluation.metrics = None
        self.assertEqual(evaluation.selection_policy, 'invalid')
        self.assertEqual(evaluation.decision_checks, [])
        self.assertFalse(evaluation.observed_eligible)

    def test_local_check_schema_status_reason_and_evidence_are_validated(self):
        mutations = (
            lambda row: row.pop('reason'),
            lambda row: row.update(extra='cached-pass'),
            lambda row: row.update(status='UNKNOWN'),
            lambda row: row.update(status=True),
            lambda row: row.update(reason=' '),
            lambda row: row.update(name=''),
            lambda row: row.update(evidence=[]),
            lambda row: row.update(evidence={'nonfinite': float('nan')}),
            lambda row: row.update(coverage=[]),
        )
        for index, mutate in enumerate(mutations):
            evaluation = make_evaluation()
            mutate(evaluation.metrics['selection']['checks'][0])
            with self.subTest(index=index):
                self.assertEqual(evaluation.decision_checks, [])
                self.assertFalse(evaluation.observed_eligible)
                self.assertFalse(evaluation.eligible)

    def test_dates_periods_criteria_and_coverage_match_the_local_snapshot(self):
        mutations = (
            lambda row: row.update(evaluated_at=NOW-timedelta(seconds=1)),
            lambda row: row.update(evaluated_at=NOW.timestamp()),
            lambda row: row.update(evaluated_at=NOW.replace(tzinfo=None)),
            lambda row: row.update(period_start=NOW-timedelta(days=29)),
            lambda row: row.update(period_end=NOW-timedelta(seconds=1)),
            lambda row: row.update(criteria_version='another-version'),
            lambda row: row['coverage'].update(complete=True),
        )
        for index, mutate in enumerate(mutations):
            evaluation = make_evaluation()
            mutate(evaluation.metrics['selection']['checks'][0])
            with self.subTest(index=index):
                self.assertEqual(evaluation.decision_checks, [])
                self.assertFalse(evaluation.observed_eligible)
        evaluation = make_evaluation()
        evaluation.coverage.fetched_at = NOW+timedelta(seconds=1)
        self.assertFalse(evaluation.observed_eligible)

    def test_disclosures_scope_and_metrics_are_required_for_local_selection(self):
        mutations = (
            lambda selection: selection.pop('unverified'),
            lambda selection: selection.update(unverified=[]),
            lambda selection: selection.update(unverified='unknown'),
            lambda selection: selection.update(unverified=[{'name': 'fees'}]),
            lambda selection: selection.update(unverified=[{'name': 'fees', 'reason': ''}]),
            lambda selection: selection.update(unverified=[{'name': ' ', 'reason': 'unknown'}]),
            lambda selection: selection['unverified'].append(deepcopy(selection['unverified'][0])),
            lambda selection: selection.pop('scope'),
            lambda selection: selection.update(scope='  '),
            lambda selection: selection.pop('metrics'),
            lambda selection: selection.update(metrics=[]),
            lambda selection: selection.update(metrics={'nonfinite': float('inf')}),
        )
        for index, mutate in enumerate(mutations):
            evaluation = make_evaluation()
            mutate(evaluation.metrics['selection'])
            with self.subTest(index=index):
                self.assertFalse(evaluation.observed_eligible)
                self.assertFalse(evaluation.eligible)

    def test_all_eight_global_disclosures_are_required_for_local_selection(self):
        for index, name in enumerate(OBSERVED_UNVERIFIED_NAMES):
            for mutation in ('missing', 'renamed'):
                evaluation = make_evaluation()
                rows = evaluation.metrics['selection']['unverified']
                if mutation == 'missing':
                    rows.pop(index)
                else:
                    rows[index]['name'] = 'unrecognised-disclosure'
                with self.subTest(name=name, mutation=mutation):
                    self.assertEqual(evaluation.unverified_checks, [])
                    self.assertFalse(evaluation.observed_eligible)
                    self.assertFalse(evaluation.eligible)
        evaluation = make_evaluation()
        evaluation.metrics['selection']['unverified'].reverse()
        self.assertTrue(evaluation.observed_eligible)
        self.assertFalse(evaluation.eligible)

    def test_json_roundtrip_preserves_typed_local_decision_and_unknown_strict_checks(self):
        evaluation = make_evaluation()
        original = deepcopy(evaluation.metrics)
        evaluation.metrics = json.loads(dumps(evaluation.metrics))
        self.assertTrue(evaluation.observed_eligible)
        self.assertFalse(evaluation.eligible)
        self.assertFalse(evaluation.strict_eligible)
        self.assertEqual(evaluation.decision_checks[0].status, Status.PASS)
        self.assertEqual(evaluation.decision_checks[0].evaluated_at, NOW)
        self.assertEqual(evaluation.metrics['selection']['metrics']['gross_result_before_fees'], '1000.50')
        self.assertEqual(original['selection']['metrics']['gross_result_before_fees'], Decimal('1000.50'))

    def test_fixture_local_selection_never_authorizes_notification(self):
        evaluation = make_evaluation()
        evaluation.source = 'fixture'
        self.assertTrue(evaluation.observed_eligible)
        self.assertFalse(evaluation.eligible)
        self.assertFalse(evaluation.strict_eligible)
