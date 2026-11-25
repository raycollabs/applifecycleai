"""Sample: Run suite H-7.1a on the shell. Check one business failure. Run it on the reference. Check nine passing goals."""
from pathlib import Path
import tempfile
import unittest
from durai.db import prepare
from durai.harness import prepare_checkpoints
from durai.qualification import TEMPLATES, fixture_spec, load, run_fixture
from durai.task_contract import build_task
from durai.task_evaluator import SandboxEvaluator
from test_workspace_worker import SequenceModel

DATABASE = 'durai_test'
NAME = 'task-7-1a-policy-catalog'
FOLDER = TEMPLATES / NAME
MODULE = '/workspace/policy_catalog.py'
BASE = load(FOLDER / 'workspace')
SUITE = load(FOLDER / 'protected')
REFERENCE = {MODULE: (FOLDER / 'reference/policy_catalog.py').read_bytes()}


class PolicyCatalogFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply migrations and checkpoint tables to durai_test. Seal task 7.1a with the locked image.
        prepare(DATABASE)
        prepare_checkpoints(DATABASE)
        cls.evaluator = SandboxEvaluator(Path(tempfile.mkdtemp(prefix='durai-7-1a-')))
        cls.task = build_task(fixture_spec(NAME, DATABASE, cls.evaluator.image))['task']

    def test_shell_fails_business_goal(self):
        # Sample: Run suite H-7.1a on the placeholder shell. Check compile PASS and a failing worker view assertion.
        verdict = self.evaluator.evaluate(self.task, BASE, SUITE, baseline=BASE)
        states = {item['command']: item for item in verdict['results']}
        self.assertEqual(states['python-compile']['state'], 'PASS')
        self.assertEqual(states['python-unittest']['state'], 'FAIL')
        self.assertIn('FAIL: test_worker_view_filters_audience', states['python-unittest']['output'])
        self.assertNotIn('ImportError', states['python-unittest']['output'])

    def test_reference_passes_protected_suite(self):
        # Sample: Run suite H-7.1a on the reference. Check nine passing goals and the comment delta check.
        verdict = self.evaluator.evaluate(self.task, REFERENCE, SUITE, baseline=BASE)
        self.assertEqual(verdict['verdict'], 'PASS', verdict['results'])
        self.assertIn('Ran 9 tests', verdict['results'][1]['output'])

    def test_scripted_loop_passes_with_reference(self):
        # Sample: Replace the shell with the reference through one edit. Run the loop. Check WAITING_FOR_REVIEW.
        step = ('edit_file', {'file_path': MODULE, 'old_string': BASE[MODULE].decode(), 'new_string': REFERENCE[MODULE].decode()})
        result = run_fixture(NAME, lambda attempt: SequenceModel(steps=[step], replies=[]),
                             Path(tempfile.mkdtemp(prefix='durai-7-1a-loop-')), DATABASE)
        self.assertEqual(result['outcome'], 'PASSED')
        self.assertEqual([(item['verdict'], item['state']) for item in result['attempts']], [('PASS', 'WAITING_FOR_REVIEW')])
        self.assertEqual(result['module'], REFERENCE[MODULE].decode())


if __name__ == '__main__':
    unittest.main()
