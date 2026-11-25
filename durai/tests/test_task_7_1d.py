"""Sample: Run the 7.1d shell and reference against five goals. Complete a scripted loop without paid calls."""
from pathlib import Path
import tempfile
import unittest
from durai.db import prepare
from durai.harness import prepare_checkpoints
from durai.qualification import TEMPLATES, fixture_files, fixture_spec, load, run_fixture
from durai.task_contract import build_task
from durai.task_evaluator import SandboxEvaluator
from test_workspace_worker import SequenceModel

DATABASE = 'durai_test'
NAME = 'task-7-1d-policy-delivery'
FOLDER = TEMPLATES / NAME
BASE = fixture_files(FOLDER)
SUITE = load(FOLDER / 'protected')
REFERENCE = dict(BASE, **load(FOLDER / 'reference'))


class PolicyDeliveryFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Prepare durai_test. Pin the 7.1d task to the existing no-network evaluator image.
        prepare(DATABASE)
        prepare_checkpoints(DATABASE)
        cls.temporary = tempfile.TemporaryDirectory(prefix='durai-7-1d-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.evaluator = SandboxEvaluator(Path(cls.temporary.name))
        cls.task = build_task(fixture_spec(NAME, DATABASE, cls.evaluator.image))['task']

    def test_shell_fails_business_goal(self):
        # Sample: Run the original source. Check compile PASS and a stored-policy delivery assertion failure.
        verdict = self.evaluator.evaluate(self.task, BASE, SUITE, baseline=BASE)
        states = {item['command']: item for item in verdict['results']}
        self.assertEqual(states['python-compile']['state'], 'PASS', states)
        self.assertEqual(states['python-unittest']['state'], 'FAIL', states)
        self.assertIn('FAIL: test_loop_reads_rules_from_store', states['python-unittest']['output'])
        self.assertNotIn('ImportError', states['python-unittest']['output'])

    def test_reference_passes_protected_suite(self):
        # Sample: Run the trusted reference. Check all five policy-delivery goals and scope checks pass.
        verdict = self.evaluator.evaluate(self.task, REFERENCE, SUITE, baseline=BASE)
        self.assertEqual(verdict['verdict'], 'PASS', verdict['results'])
        output = next(item['output'] for item in verdict['results'] if item['command'] == 'python-unittest')
        self.assertIn('Ran 5 tests', output)

    def test_scripted_loop_passes_with_reference(self):
        # Sample: Edit both declared files with a scripted model. Check one passing package at WAITING_FOR_REVIEW.
        paths = ['/workspace/durai/qualification.py', '/workspace/durai/development_loop.py']
        steps = [('edit_file', {'file_path': path, 'old_string': BASE[path].decode(), 'new_string': REFERENCE[path].decode()}) for path in paths]
        result = run_fixture(NAME, lambda attempt: SequenceModel(steps=steps, replies=[]),
                             Path(self.temporary.name) / 'loop', DATABASE,
                             candidate_root=Path(self.temporary.name) / 'retained')
        self.assertEqual(result['outcome'], 'PASSED', result['verdict'])
        self.assertEqual([(item['verdict'], item['state']) for item in result['attempts']], [('PASS', 'WAITING_FOR_REVIEW')])
        self.assertEqual(result['module'], REFERENCE[paths[0]].decode())


if __name__ == '__main__':
    unittest.main()
