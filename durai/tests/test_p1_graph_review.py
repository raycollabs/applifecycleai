"""Sample: Check corrected graph files, narrow operator rights, and safe fixed database diagnoses."""
import sys
import tempfile
import unittest
from pathlib import Path
from durai.qualification import ROOT,TEMPLATES,fixture_files,load
from durai.task_evaluator import SandboxEvaluator
from durai.database_feedback import safe_database_feedback
sys.path.insert(0,str(ROOT/'tools'))
from graph_candidate_checks import check_candidate


class GraphCorrectionTests(unittest.TestCase):
    def test_pair_source_scope(self):
        # Sample: Supply two module packages as a pair. Check refusal before any package is opened.
        from graph_pair_review import review_pair
        jobs = [{'outcome':'PASSED','package':{'path':'unused','digest':'unused'},'fixture':'task-graph-tenancy-module-v9'}]*2
        with self.assertRaisesRegex(ValueError,'writable module job'):
            review_pair(jobs,{},'unused','TEST')

    def test_corrected_pair_operator_and_restore(self):
        # Sample: Apply corrected Python and SQL. Check narrow grants and equal restored views before projection.
        folder = TEMPLATES/'task-graph-tenancy-module-v9'
        files = dict(fixture_files(folder),**load(folder/'reference'))
        with tempfile.TemporaryDirectory(prefix='durai-p1-pair-') as scratch:
            result = check_candidate(files,load(folder/'protected'),SandboxEvaluator(Path(scratch)))
        self.assertFalse(result['operatorMetadata']['publicExecute'])
        self.assertEqual(result['restore'],'PASS_WITH_EXPLICIT_AGE_OID_REPAIR')


class FeedbackSafetyTests(unittest.TestCase):
    def test_fixed_diagnostics(self):
        # Sample: Include hidden input H-991 in an operator or record fault. Check only the fixed diagnosis remains.
        cases = [('UndefinedFunction','operator does not exist: ag_catalog.agtype @> ag_catalog.agtype H-991',
                  'trusted transaction-local search_path'),
                 ('UndefinedColumn','record "H-991" has no field "H-991"','separate loop variables')]
        for name,body,expected in cases:
            text = safe_database_feedback('psycopg.errors.'+name+': '+body,'setup')
            self.assertIn(expected,text)
            self.assertNotIn('H-991',text)
