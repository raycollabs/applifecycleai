"""Sample: Run suite H-7.1c in an ephemeral database on the shell and on the reference. Check one business failure, then eight passes."""
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
NAME = 'task-7-1c-policy-graph'
FOLDER = TEMPLATES / NAME
MODULE = '/workspace/durai/policy_graph.py'
MIGRATION = '/workspace/migrations/core/core_0011.sql'
BASE = fixture_files(FOLDER)
SUITE = load(FOLDER / 'protected')
REFERENCE = dict(BASE, **load(FOLDER / 'reference'))


class PolicyGraphFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply migrations to durai_test. Seal task 7.1c with the evaluation image and the database option.
        prepare(DATABASE)
        prepare_checkpoints(DATABASE)
        cls.evaluator = SandboxEvaluator(Path(tempfile.mkdtemp(prefix='durai-7-1c-')))
        cls.task = build_task(fixture_spec(NAME, DATABASE, cls.evaluator.image, cls.evaluator.database_image))['task']

    def test_shell_fails_business_goal(self):
        # Sample: Run suite H-7.1c on the shell. Check database-setup PASS and a failing projection assertion.
        verdict = self.evaluator.evaluate(self.task, BASE, SUITE, baseline=BASE)
        states = {item['command']: item for item in verdict['results']}
        self.assertEqual(states['database-setup']['state'], 'PASS', states['database-setup'])
        self.assertIn('FAIL: test_project_creates_one_node_per_policy', states['python-unittest']['output'])

    def test_reference_passes_protected_suite(self):
        # Sample: Run suite H-7.1c on the reference migration and module. Check eight passing goals.
        verdict = self.evaluator.evaluate(self.task, REFERENCE, SUITE, baseline=BASE)
        self.assertEqual(verdict['verdict'], 'PASS', verdict['results'])
        unit = next(item for item in verdict['results'] if item['command'] == 'python-unittest')
        self.assertIn('Ran 8 tests', unit['output'])

    def test_scripted_loop_passes_with_reference(self):
        # Sample: Replace the shell module and write core_0011.sql through two tool calls. Run the loop. Check WAITING_FOR_REVIEW.
        steps = [('edit_file', {'file_path': MODULE, 'old_string': BASE[MODULE].decode(), 'new_string': REFERENCE[MODULE].decode()}),
                 ('write_file', {'file_path': MIGRATION, 'content': REFERENCE[MIGRATION].decode()})]
        result = run_fixture(NAME, lambda attempt: SequenceModel(steps=steps, replies=[]),
                             Path(tempfile.mkdtemp(prefix='durai-7-1c-loop-')), DATABASE)
        self.assertEqual(result['outcome'], 'PASSED', result['verdict'])
        self.assertEqual([(item['verdict'], item['state']) for item in result['attempts']], [('PASS', 'WAITING_FOR_REVIEW')])


if __name__ == '__main__':
    unittest.main()
