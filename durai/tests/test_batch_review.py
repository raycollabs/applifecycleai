"""Sample: Check the review runner with a reference package before it reads generated candidate bytes."""
import sys
import tempfile
import unittest
from pathlib import Path
from durai.qualification import ROOT, TEMPLATES, fixture_files, load
from durai.task_evaluator import SandboxEvaluator

sys.path.insert(0, str(ROOT / 'tools'))
from graph_candidate_checks import check_candidate


class BatchReviewTests(unittest.TestCase):
    def test_reference_operator_and_restore_checks(self):
        # Sample: Apply reference P-1 and S-1. Check narrow owner rights and exact restored graph views.
        folder = TEMPLATES / 'task-graph-tenancy-module-v8'
        files = dict(fixture_files(folder), **load(folder / 'reference'))
        suite = load(folder / 'protected')
        with tempfile.TemporaryDirectory(prefix='durai-batch-review-proof-') as scratch:
            result = check_candidate(files, suite, SandboxEvaluator(Path(scratch)))
        self.assertEqual(result['restore'], 'PASS_WITH_EXPLICIT_AGE_OID_REPAIR')
        self.assertFalse(result['operatorMetadata']['publicExecute'])
