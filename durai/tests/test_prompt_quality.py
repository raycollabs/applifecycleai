"""Sample: Check the seats prompt. Remove the sequence rule. Stop before the provider call."""
from contextlib import nullcontext
import unittest
from unittest.mock import Mock, patch

from durai.context_pack import build_pack, index_text, sql_index
from durai.context_tools import describe_table
from durai.development_loop import build_loop
from durai.prompt_quality import blueprint_guidance, classify_receipt
from durai.qualification import TEMPLATES, fixture_config, fixture_files, fixture_spec, fixture_suite, pins
from durai.task_contract import build_task
from tools.check_prompt_quality import audit_fixture, candidate_framework, matching_revision


class PromptQualityTests(unittest.TestCase):
    def setUp(self):
        # Sample: Build the pack from the catalog. Pin the task to the matching revision for this test.
        patcher = patch('durai.prompt_quality.ACTIVE_REVISION', matching_revision())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_classify_receipts(self):
        # Sample: Classify one nullable parameter, one missing grant, and five interface or rule failures.
        evidence = {
            'generic_sql_rule': 'psycopg.errors.IndeterminateDatatype: could not determine data type of parameter',
            'environment_privilege': 'psycopg.errors.InsufficientPrivilege: permission denied for table seat',
            'invariant_not_delivered': 'Conflict: Payment event sequence differs from its revision',
            'callable_call_form': "AttributeError: 'Provider' object has no attribute 'dispatch'",
            'input_shape': 'ValueError: invalid statement line fields',
            'error_type': 'AssertionError: Conflict not raised',
            'return_shape': "KeyError: 'outcome'",
        }
        for label, output in evidence.items():
            with self.subTest(label=label):
                receipt = {'outcome': 'FAILED', 'verdict': {'results': [
                    {'state': 'FAIL', 'command': 'python-unittest', 'output': output}]}}
                result = classify_receipt(receipt)
                self.assertIn(label, result['failures'][0]['categories'])
                self.assertNotIn(output, repr(result))

    def test_missing_fact_stops_worker(self):
        # Sample: Remove the sequence rule. Give its words as prior memory. Check BLOCKED and zero model calls.
        folder = TEMPLATES / 'task-tx-seats-v14-plain'
        with patch('durai.qualification.contract_digest', return_value='a' * 64):
            spec = fixture_spec(folder.name, 'durai_test', 'synthetic', 'synthetic')
        files, suite = fixture_files(folder), fixture_suite(folder)
        task = build_task(spec)['task']
        framework = candidate_framework()
        guidance = blueprint_guidance(task, files, framework).replace('event.sequence equals order.revision', '')
        ledger = Mock(database='durai_test', role='durai_runtime')
        ledger.create_attempt.return_value = {'id': 'attempt-1', 'budget_id': 'budget-1'}
        sources = Mock()
        sources.policies.return_value = []
        sources.files.return_value = files
        sources.suite.return_value = suite
        provider, advisor = Mock(), Mock()
        graph = build_loop(ledger, Mock(), Mock(), Mock(), sources, provider, None, advisor).compile()
        with patch('durai.context_pack.context_procedure', return_value={'maxCharacters': 60000, 'tools': []}), \
                patch('durai.context_delivery.record_attempt_context'), \
                patch('durai.context_delivery.attempt_memory', return_value=('event.sequence equals order.revision', [])), \
                patch('durai.advisor_select.framework_patterns', return_value=framework), \
                patch('durai.prompt_quality.blueprint_guidance', return_value=guidance), \
                patch('durai.db.transaction', return_value=nullcontext(Mock())), \
                patch('durai.execution_events.record') as event:
            result = graph.invoke({'spec': spec, 'current': pins(spec), 'run_id': 'quality-run'})
        self.assertEqual(result['outcome'], 'BLOCKED')
        self.assertIn('seats-event-sequence', result['detail'])
        provider.assert_not_called()
        advisor.advise.assert_not_called()
        self.assertEqual(event.call_args.args[2], 'PROMPT_FACT_AUDIT')
        self.assertEqual(ledger.move.call_args.args[3], 'FAILED')

    def test_fixture_examples(self):
        # Sample: Render both variants of funds. Check an example from the funds pattern in both prompts.
        framework = candidate_framework()
        patterns = {pattern['id']: pattern for pattern in framework['patterns']}
        for folder in sorted(TEMPLATES.glob('task-tx-*-v14*')):
            with self.subTest(folder=folder.name), patch('durai.qualification.contract_digest', return_value='a' * 64):
                task = build_task(fixture_spec(folder.name, 'durai_test', 'synthetic', 'synthetic'))['task']
                case = fixture_config(folder)['case']
                examples = patterns['blueprint.' + case + '.correctness']['examples'][:2]
                guidance = blueprint_guidance(task, fixture_files(folder), framework)
                self.assertTrue(examples)
                self.assertEqual(guidance.split('## Public worked examples\n')[1].splitlines(), examples)

    def test_blueprint_delivery(self):
        # Sample: Render the 22 qualified task prompts. Check that all required facts pass.
        framework = candidate_framework()
        paths = sorted(TEMPLATES.glob('task-tx-*-v14*'))
        self.assertEqual(len(paths), 22)
        for folder in paths:
            with self.subTest(folder=folder.name):
                result = audit_fixture(folder, framework)
                self.assertEqual(result['missing'], [])
                self.assertEqual(result['protectedLineMatches'], 0)
                self.assertLessEqual(result['packCharacters'], 60000)

    def test_sql_facts(self):
        # Sample: Grant SELECT on seat and grant UPDATE of state on seat_order. Check the declared facts and source.
        name = '/workspace/reservations.sql'
        text = ('CREATE TABLE r.seat (id integer);\nCREATE TABLE r.seat_order (state text);\n'
                'GRANT SELECT ON r.seat TO durai_runtime;\n'
                'GRANT SELECT, UPDATE (state)\nON TABLE r.seat_order TO durai_runtime;\n'
                'REVOKE UPDATE (state) ON r.seat_order FROM durai_runtime;')
        index = sql_index({name: text})
        answer = describe_table(index, {name: text}, 'r.seat')
        for rendered in (answer, index_text(index)):
            self.assertIn('%s::text IS NULL', rendered)
            self.assertIn('GRANT SELECT ON r.seat TO durai_runtime;', rendered)
        self.assertNotIn('UPDATE (state)', answer)
        other = describe_table(index, {name: text}, 'seat_order')
        self.assertIn(name + ':4 GRANT SELECT, UPDATE (state)', other)
        self.assertIn('REVOKE UPDATE (state) ON r.seat_order FROM durai_runtime;', other)
        pack = build_pack({'writePaths': []}, {name: text.encode('utf-8')})
        self.assertIn('Declared table privileges', pack['text'])
