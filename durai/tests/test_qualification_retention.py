"""Sample: Retain Task B after scratch cleanup. Review its exact digest and save one receipt."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from durai.common import Conflict
from durai.db import prepare
from durai.harness import prepare_checkpoints
from durai.qualification import ROOT, run_task_b, scripted_steps
from durai.candidate_store import CandidateStore
from durai.execution_events import replay
from test_workspace_worker import SequenceModel


def review_tool():
    # Sample: Load the reviewer command without running its CLI.
    spec = importlib.util.spec_from_file_location('review_tool_tests', ROOT / 'tools/review_task_b.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class QualificationRetentionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Prepare only durai_test and its checkpoint schema.
        prepare('durai_test')
        prepare_checkpoints('durai_test')

    def test_default_store_is_retained(self):
        # Sample: Finish Task B. Remove scratch. Restore the exact retained package.
        with tempfile.TemporaryDirectory() as durable:
            with tempfile.TemporaryDirectory() as scratch:
                with patch('durai.qualification.ROOT', Path(durable)):
                    result = run_task_b(lambda attempt: SequenceModel(steps=scripted_steps(attempt), replies=[]), Path(scratch))
            package = result['package']
            self.assertEqual(result['outcome'], 'PASSED')
            self.assertTrue(Path(package['path']).is_relative_to(Path(durable) / 'private/candidates'))
            self.assertIn('/workspace/change_manifest.py', CandidateStore(Path(durable) / 'private/candidates').restore(**package))


class ReviewReceiptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Prepare the test database for a scripted review with no release authority.
        prepare('durai_test')
        prepare_checkpoints('durai_test')

    def test_review_writes_exact_candidate_receipt(self):
        # Sample: Approve Task B as U-1. Repeat the command. Check one approval event and one exact receipt.
        with tempfile.TemporaryDirectory() as durable, tempfile.TemporaryDirectory() as scratch:
            root = Path(durable)
            with patch('durai.qualification.ROOT', root):
                result = run_task_b(lambda attempt: SequenceModel(steps=scripted_steps(attempt), replies=[]), Path(scratch))
            receipt = dict(result, database='durai_test')
            (root / 'receipts').mkdir()
            (root / 'receipts/test-live.json').write_text(json.dumps(receipt))
            tool = review_tool()
            with patch.object(tool, 'ROOT', root), patch('sys.argv', ['review_task_b.py', 'APPROVE', '--reviewer', 'U-1', '--receipt', 'test-live.json']):
                with patch('builtins.print'):
                    tool.main()
                    tool.main()
            reviewed = json.loads((root / 'receipts/test-review.json').read_text())
            self.assertEqual(reviewed['package']['digest'], result['package']['digest'])
            self.assertEqual(reviewed['verdictDigest'], result['verdict']['digest'])
            self.assertEqual(reviewed['reviewer'], 'U-1')
            self.assertFalse(reviewed['releaseAuthority'])
            self.assertEqual(len(list((root / 'private/reviews').glob('*.json'))), 1)
            approvals = [row for row in replay(result['runId'], database='durai_test') if row['kind'] == 'ATTEMPT_REVIEW_APPROVED']
            self.assertEqual(len(approvals), 1)
