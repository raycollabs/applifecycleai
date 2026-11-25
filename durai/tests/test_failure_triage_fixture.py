"""Sample: Run the failure-triage shell, reference, and a scripted file-tool run. Check RED, 11 passing goals, and review."""
import tempfile
import unittest
from pathlib import Path
from durai.db import prepare
from durai.harness import prepare_checkpoints
from durai.qualification import TEMPLATES, fixture_files, fixture_spec, load, run_fixture
from durai.task_contract import build_task
from durai.task_evaluator import SandboxEvaluator
from test_workspace_worker import SequenceModel

NAME = 'task-failure-triage'
FOLDER = TEMPLATES / NAME
MODULE = '/workspace/failure_triage_rules.py'


class FailureTriageFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply migrations and checkpoint tables to durai_test. Seal the fixture task with binding foundation-generic-v10.
        prepare('durai_test')
        prepare_checkpoints('durai_test')
        cls.scratch = tempfile.TemporaryDirectory(prefix='durai-triage-fixture-')
        cls.addClassCleanup(cls.scratch.cleanup)
        cls.evaluator = SandboxEvaluator(Path(cls.scratch.name))
        cls.task = build_task(fixture_spec(NAME, 'durai_test', cls.evaluator.image))['task']
        cls.base, cls.suite = fixture_files(FOLDER), load(FOLDER / 'protected')

    def test_shell_fails_business_action(self):
        # Sample: The shell retries a trusted fixture fault. Check compile PASS, comment PASS, and an AssertionError in test_trusted_fixture_fault_stops.
        red = self.evaluator.evaluate(self.task, self.base, self.suite, baseline=self.base)
        states = {item['command']: item for item in red['results']}
        self.assertEqual(states['python-compile']['state'], 'PASS')
        self.assertEqual(states['comment-policy']['state'], 'PASS')
        unit = states['python-unittest']
        self.assertEqual(unit['state'], 'FAIL')
        self.assertEqual(unit['fault'], {'origin': 'candidate', 'reasonCode': 'CANDIDATE_ASSERTION'})
        self.assertIn('FAIL: test_trusted_fixture_fault_stops', unit['output'])
        self.assertIn('AssertionError', unit['output'])
        self.assertNotIn('ERROR: test_', unit['output'])
        self.assertEqual(self.task['binding']['bindingId'], 'foundation-generic-v10')

    def test_reference_passes(self):
        # Sample: Run all 11 protected goals on the reference. Check PASS and "Ran 11 tests".
        green = self.evaluator.evaluate(self.task, dict(self.base, **load(FOLDER / 'reference')), self.suite, baseline=self.base)
        self.assertEqual(green['verdict'], 'PASS', green['results'])
        self.assertIn('Ran 11 tests', next(item['output'] for item in green['results'] if item['command'] == 'python-unittest'))

    def test_scripted_run_reaches_review(self):
        # Sample: Apply the reference with edit_file in one scripted call. Check PASS, WAITING_FOR_REVIEW, and SUBMIT_FOR_REVIEW with a pinned rule digest.
        shell = self.base[MODULE].decode()
        reference = load(FOLDER / 'reference')[MODULE].decode()
        steps = [('edit_file', {'file_path': MODULE, 'old_string': shell, 'new_string': reference})]
        models = []

        def model(attempt):
            # Sample: Build scripted model M-1 for attempt 1. Record it. Return M-1.
            models.append(SequenceModel(steps=steps, replies=[]))
            return models[-1]
        with tempfile.TemporaryDirectory(prefix='durai-triage-run-') as scratch:
            result = run_fixture(NAME, model, Path(scratch), 'durai_test', candidate_root=Path(scratch) / 'candidates')
        self.assertEqual(result['outcome'], 'PASSED', result.get('verdict'))
        self.assertEqual(len(models), 1)
        self.assertEqual(result['attempts'][-1]['state'], 'WAITING_FOR_REVIEW')
        self.assertEqual(result['module'], reference)
        self.assertIn('DECISION_RECORDED', result['events'])
        from durai.execution_events import replay
        triage = [row['payload'] for row in replay(result['runId'], database='durai_test')
                  if row['kind'] == 'DECISION_RECORDED' and row['payload']['point'] == 'failure-triage']
        self.assertEqual([(row['choice'], row['procedureSource']) for row in triage], [('SUBMIT_FOR_REVIEW', 'pinned')])


if __name__ == '__main__':
    unittest.main()
