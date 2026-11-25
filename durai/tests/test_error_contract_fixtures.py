"""Sample: Read 6 v10 fixtures. Check every expected error is named and every qualified file is unchanged."""
import json
import tempfile
import unittest
from pathlib import Path
from durai.error_contract import enforced, unnamed_errors
from durai.qualification import TEMPLATES, fixture_files, fixture_spec, load
from durai.task_contract import build_task
from durai.task_evaluator import SandboxEvaluator

PAIRS = {'task-7-1b-policy-store': 'task-7-1b-policy-store-v10', 'task-7-1c-policy-graph': 'task-7-1c-policy-graph-v10',
         'task-decision-edit-guard': 'task-decision-edit-guard-v10', 'task-graph-tenancy': 'task-graph-tenancy-v10',
         'task-graph-tenancy-migration-v9': 'task-graph-tenancy-migration-v10',
         'task-graph-tenancy-module-v9': 'task-graph-tenancy-module-v10'}


def config(name):
    # Sample: Read task.json of task-decision-edit-guard-v10. Return its objective, binding, and budget.
    return json.loads((TEMPLATES / name / 'task.json').read_text(encoding='utf-8'))


class ErrorContractFixtureTests(unittest.TestCase):
    def test_v10_fixtures_name_every_error(self):
        # Sample: Read task-graph-tenancy-module-v10. Check binding foundation-generic-v10 and no unnamed error.
        for target in PAIRS.values():
            self.assertEqual(config(target)['binding'], {'id': 'foundation-generic-v10', 'version': '10'}, target)
            self.assertEqual(unnamed_errors(config(target)['objective'], load(TEMPLATES / target / 'protected')), [], target)

    def test_v10_fixtures_keep_qualified_files(self):
        # Sample: Compare task-graph-tenancy-module-v9 with v10. Check equal suite, shell, and reference bytes and one appended sentence.
        for source, target in PAIRS.items():
            for part in ('protected', 'workspace', 'reference'):
                self.assertEqual(load(TEMPLATES / source / part), load(TEMPLATES / target / part), target + ' ' + part)
            before, after = config(source), config(target)
            self.assertTrue(after['objective'].startswith(before['objective'].rstrip() + ' Error contract: '), target)
            for key in set(before) | set(after):
                if key not in ('objective', 'binding', 'taskPrefix'):
                    self.assertEqual(before.get(key), after.get(key), target + ' ' + key)

    def test_source_fixtures_keep_working(self):
        # Sample: Read task-graph-tenancy-module-v9 on binding foundation-generic-v7. Check unnamed errors and no enforcement.
        source = 'task-graph-tenancy-module-v9'
        self.assertTrue(unnamed_errors(config(source)['objective'], load(TEMPLATES / source / 'protected')))
        with tempfile.TemporaryDirectory() as scratch:
            evaluator = SandboxEvaluator(Path(scratch))
            task = build_task(fixture_spec(source, 'durai_test', evaluator.image, evaluator.database_image))['task']
        self.assertFalse(enforced(task, 'durai_test', 'durai_runtime'))

    def test_v10_fixture_is_enforced_and_qualifies(self):
        # Sample: Seal task-decision-edit-guard-v10 on pack v10. Check enforcement, shell RED in test_first_matching_rule, and reference PASS with 8 tests.
        name = 'task-decision-edit-guard-v10'
        folder = TEMPLATES / name
        base, suite = fixture_files(folder), load(folder / 'protected')
        with tempfile.TemporaryDirectory(prefix='durai-error-contract-fixture-') as scratch:
            evaluator = SandboxEvaluator(Path(scratch))
            task = build_task(fixture_spec(name, 'durai_test', evaluator.image))['task']
            self.assertTrue(enforced(task, 'durai_test', 'durai_runtime'))
            red = evaluator.evaluate(task, base, suite, baseline=base)
            green = evaluator.evaluate(task, dict(base, **load(folder / 'reference')), suite, baseline=base)
        unit = next(item for item in red['results'] if item['command'] == 'python-unittest')
        self.assertIn('test_first_matching_rule', unit['output'])
        self.assertEqual(unit['fault']['reasonCode'], 'CANDIDATE_ASSERTION')
        self.assertEqual(green['verdict'], 'PASS', green['results'])
        self.assertIn('Ran 8 tests', next(item['output'] for item in green['results'] if item['command'] == 'python-unittest'))


if __name__ == '__main__':
    unittest.main()
