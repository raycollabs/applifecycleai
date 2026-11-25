"""Sample: Fail the recovery shell, then pass the trusted reference."""
from pathlib import Path
import tempfile
import unittest

from durai.qualification import TEMPLATES, fixture_files, fixture_spec, load
from durai.task_contract import build_task
from durai.task_evaluator import SandboxEvaluator

NAME = 'task-payment-recovery'


class RecoveryFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Seal the recovery source and protected suite under one task contract.
        folder = TEMPLATES/NAME
        cls.base = fixture_files(folder)
        cls.suite = load(folder/'protected')
        cls.reference = dict(cls.base, **load(folder/'reference'))
        temp = tempfile.TemporaryDirectory(prefix='durai-payment-recovery-')
        cls.addClassCleanup(temp.cleanup)
        cls.evaluator = SandboxEvaluator(Path(temp.name))
        spec = fixture_spec(NAME,'durai_test',cls.evaluator.image,cls.evaluator.database_image)
        cls.task = build_task(spec)['task']

    def test_shell_fails_and_reference_passes(self):
        # Sample: Run both versions. Verify a business failure and a six-case pass.
        first = self.evaluator.evaluate(self.task,self.base,self.suite,baseline=self.base)
        second = self.evaluator.evaluate(self.task,self.reference,self.suite,baseline=self.base)
        self.assertEqual(first['verdict'],'FAIL')
        unit = next(row for row in first['results'] if row['command']=='python-unittest')
        self.assertIn('test_uncertain_uses_lookup_and_preserves_identity',unit['output'])
        self.assertEqual(second['verdict'],'PASS',second['results'])

    def test_protected_files_are_outside_worker_input(self):
        # Sample: Inspect the task's file map and its one writable module.
        self.assertEqual(self.task['writePaths'],['/workspace/recovery.py'])
        self.assertIn('/workspace/tests/**',self.task['deniedPaths'])
        self.assertTrue(self.suite)
        self.assertTrue(self.base.keys().isdisjoint(self.suite))
