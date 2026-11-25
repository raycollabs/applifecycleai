"""Sample: Split the graph task. Fail each shell, pass each reference, and keep each task to one writable file."""
from pathlib import Path
import tempfile
import unittest
from durai.db import prepare
from durai.harness import prepare_checkpoints
from durai.qualification import TEMPLATES, contract_digest, fixture_files, fixture_spec, load
from durai.task_contract import build_task
from durai.task_evaluator import SandboxEvaluator

DATABASE = 'durai_test'
MODULE = '/workspace/durai/application_policy_graph.py'
MIGRATION = '/workspace/migrations/core/core_0012.sql'


def fixture(name):
    # Sample: Read fixture task-graph-tenancy-module. Return its base files, suite, and base plus reference files.
    folder = TEMPLATES / name
    base = fixture_files(folder)
    return base, load(folder / 'protected'), dict(base, **load(folder / 'reference'))


class SplitFixtureCase(unittest.TestCase):
    NAME = None

    @classmethod
    def setUpClass(cls):
        # Sample: Prepare durai_test and record the bound policies. Seal the fixture with the locked evaluation images.
        prepare(DATABASE)
        prepare_checkpoints(DATABASE)
        contract_digest(DATABASE)
        cls.temporary = tempfile.TemporaryDirectory(prefix='durai-graph-split-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.evaluator = SandboxEvaluator(Path(cls.temporary.name))
        cls.task = build_task(fixture_spec(cls.NAME, DATABASE, cls.evaluator.image, cls.evaluator.database_image))['task']
        cls.base, cls.suite, cls.reference = fixture(cls.NAME)

    def unittest_result(self, files):
        # Sample: Evaluate files F-1 against suite H-1. Return the verdict and the python-unittest result.
        verdict = self.evaluator.evaluate(self.task, files, self.suite, baseline=self.base)
        return verdict, next(item for item in verdict['results'] if item['command'] == 'python-unittest')


class GraphMigrationV9FixtureTests(SplitFixtureCase):
    NAME = 'task-graph-tenancy-migration-v9'

    def test_shell_fails_business_goal(self):
        # Sample: Run the SQL stub. Check setup and compile PASS, then projected count 0 versus expected count 2.
        self.assertIn(MIGRATION, self.base)
        verdict, unit = self.unittest_result(self.base)
        self.assertEqual(unit['state'], 'FAIL', verdict['results'])
        states = {item['command']: item['state'] for item in verdict['results']}
        self.assertEqual(states['database-setup'], 'PASS')
        self.assertEqual(states['python-compile'], 'PASS')
        self.assertIn('test_projected_count_matches_canonical_policies', unit['output'])
        self.assertIn('AssertionError: 0 != 2', unit['output'])

    def test_reference_passes_protected_suite(self):
        # Sample: Add the reference core_0012.sql to the reference module. Check PASS with 12 tests.
        verdict, unit = self.unittest_result(self.reference)
        self.assertEqual(verdict['verdict'], 'PASS', verdict['results'])
        self.assertIn('Ran 12 tests', unit['output'])

    def test_module_is_not_writable(self):
        # Sample: Read the sealed migration task. Check writePaths equals core_0012.sql only.
        self.assertEqual(self.task['writePaths'], [MIGRATION])


class GraphModuleV9FixtureTests(SplitFixtureCase):
    NAME = 'task-graph-tenancy-module-v9'

    def test_shell_fails_business_goal(self):
        # Sample: Evaluate the shell module with the reference migration. Check the own-policy assertion failure.
        verdict, unit = self.unittest_result(self.base)
        self.assertEqual(unit['state'], 'FAIL', verdict['results'])
        states = {item['command']: item['state'] for item in verdict['results']}
        self.assertEqual(states['database-setup'], 'PASS')
        self.assertEqual(states['python-compile'], 'PASS')
        self.assertIn('test_projected_count_matches_canonical_policies', unit['output'])
        self.assertIn('AssertionError: 0 != 2', unit['output'])

    def test_reference_passes_protected_suite(self):
        # Sample: Use the reference module with the reference migration. Check PASS with 12 tests.
        verdict, unit = self.unittest_result(self.reference)
        self.assertEqual(verdict['verdict'], 'PASS', verdict['results'])
        self.assertIn('Ran 12 tests', unit['output'])

    def test_migration_is_not_writable(self):
        # Sample: Read the sealed module task. Check writePaths equals application_policy_graph.py only.
        self.assertEqual(self.task['writePaths'], [MODULE])


del SplitFixtureCase

if __name__ == '__main__':
    unittest.main()
