"""Sample: Run Task B. Fail the digest-only candidate. Pass the complete candidate. Restore it at WAITING_FOR_REVIEW."""
from pathlib import Path
import tempfile
import unittest
from durai.db import prepare
from durai.harness import prepare_checkpoints
from durai.qualification import CORRECT_BODY, run_task_b, scripted_steps
from test_workspace_worker import SequenceModel

DATABASE = 'durai_test'


class AcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply core_0009 and checkpoint tables to durai_test.
        prepare(DATABASE)
        prepare_checkpoints(DATABASE)

    def test_durai_module_change_waits_for_review(self):
        # Sample: Change only change_manifest.py. Pass protected checks. Restore the package and compare digests. Check WAITING_FOR_REVIEW.
        result = run_task_b(lambda attempt: SequenceModel(steps=scripted_steps(attempt), replies=[]),
                            Path(tempfile.mkdtemp(prefix='durai-task-b-')), DATABASE)
        self.assertEqual(result['outcome'], 'PASSED')
        first, second = result['attempts']
        self.assertEqual((first['verdict'], first['state']), ('FAIL', 'FAILED'))
        self.assertEqual((second['verdict'], second['state']), ('PASS', 'WAITING_FOR_REVIEW'))
        self.assertIsNotNone(first['restoredManifest'])
        self.assertIn(CORRECT_BODY, result['module'])
        self.assertEqual(second['usage'], {'attempts': 2, 'modelCalls': 6, 'toolCalls': 4})
        self.assertIn('ATTEMPT_WAITING_FOR_REVIEW', result['events'])
        self.assertNotIn('ATTEMPT_SUCCEEDED', result['events'])


if __name__ == '__main__':
    unittest.main()
