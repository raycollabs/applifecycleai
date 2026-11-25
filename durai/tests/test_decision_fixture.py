"""Sample: Run the decision shell and reference. Check a business failure and eight passing reference goals."""
import tempfile
import unittest
from pathlib import Path
from durai.qualification import TEMPLATES, fixture_files, fixture_spec, load
from durai.task_contract import build_task
from durai.task_evaluator import SandboxEvaluator


class DecisionFixtureTests(unittest.TestCase):
    def test_shell_and_reference(self):
        # Sample: Check KEEP differs from DEFAULT in the shell. Run the reference and check all eight goals pass.
        name = 'task-decision-edit-guard'
        folder = TEMPLATES / name
        base, suite = fixture_files(folder), load(folder / 'protected')
        with tempfile.TemporaryDirectory(prefix='durai-decision-fixture-') as scratch:
            evaluator = SandboxEvaluator(Path(scratch))
            task = build_task(fixture_spec(name, 'durai_test', evaluator.image))['task']
            red = evaluator.evaluate(task, base, suite, baseline=base)
            states = {item['command']: item for item in red['results']}
            self.assertEqual(states['python-compile']['state'], 'PASS')
            self.assertEqual(states['comment-policy']['state'], 'PASS')
            self.assertIn('test_first_matching_rule', states['python-unittest']['output'])
            self.assertIn('AssertionError', states['python-unittest']['output'])
            green = evaluator.evaluate(task, dict(base, **load(folder / 'reference')), suite, baseline=base)
            self.assertEqual(green['verdict'], 'PASS', green['results'])
            self.assertIn('Ran 8 tests', next(item['output'] for item in green['results'] if item['command'] == 'python-unittest'))
