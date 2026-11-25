"""Sample: Attempt 1 books 198 shares. Attempt 2 starts from that file and fixes it to 200. Check review."""
import json
import unittest
from durai.failure_triage import RESUME_REASONS, resume_package, triage_procedure
from test_failure_triage import bound

FIX_FROM_198 = ('edit_file', {'file_path': '/workspace/shares.py', 'old_string': 'return lots * lot_size - 2',
                              'new_string': 'return lots * lot_size'})


class ResumeRetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply migrations and checkpoint tables to durai_test.
        from durai.db import prepare
        from durai.harness import prepare_checkpoints
        prepare('durai_test')
        prepare_checkpoints('durai_test')

    def test_assertion_retry_resumes_previous_package(self):
        # Sample: Attempt 1 changes "- 1" to "- 2" and fails 200 shares. Attempt 2 edits "- 2" to nothing. Check PASS, ATTEMPT_RESUMED with package 1, and 2 models.
        from durai.execution_events import replay
        from test_dev_loop import WRONG, LoopHarness, loop_spec
        task = bound('12')
        harness = LoopHarness([[WRONG], [FIX_FROM_198]])
        state = harness.run(loop_spec(binding={'bindingId': task['binding']['bindingId'],
                                               'contractDigest': task['pins']['contractDigest']}))
        self.assertEqual(state['outcome'], 'PASSED', state.get('verdict'))
        self.assertEqual(len(harness.models), 2)
        first = state['history'][0]
        resumed = [row['payload'] for row in replay(harness.run_id, database='durai_test') if row['kind'] == 'ATTEMPT_RESUMED']
        self.assertEqual(len(resumed), 1)
        self.assertEqual(resumed[0]['package'], first['package'])
        self.assertEqual(resumed[0]['attemptId'], state['history'][1]['attemptId'])

    def test_setup_failure_restarts_from_shell(self):
        # Sample: Decide RETRY_CANDIDATE with reason CANDIDATE_SETUP_SQL and package P-1. Check no resume package. Use CANDIDATE_ASSERTION. Check P-1.
        procedure = {'resumeReasons': RESUME_REASONS}
        package = {'path': 'P-1.sqlite', 'digest': 'a' * 64}
        self.assertIsNone(resume_package({'choice': 'RETRY_CANDIDATE', 'reasonCode': 'CANDIDATE_SETUP_SQL'}, procedure, package))
        self.assertIsNone(resume_package({'choice': 'STOP_BUDGET', 'reasonCode': 'CANDIDATE_ASSERTION'}, procedure, package))
        self.assertEqual(resume_package({'choice': 'RETRY_CANDIDATE', 'reasonCode': 'CANDIDATE_ASSERTION'}, procedure, package), package)

    def test_older_binding_restarts_from_shell(self):
        # Sample: Resolve a v11 binding. Check resumeReasons is empty.
        self.assertEqual(triage_procedure(bound('11'), 'durai_test', 'durai_runtime')['resumeReasons'], [])

    def test_v12_binding_pins_resume_reasons(self):
        # Sample: Bind pack v12. Check resumeReasons CANDIDATE_ASSERTION and CANDIDATE_COMMENT and minimum 5 retry calls.
        procedure = triage_procedure(bound('12'), 'durai_test', 'durai_runtime')
        self.assertEqual((procedure['source'], procedure['resumeReasons'], procedure['minimumRetryCalls']),
                         ('pinned', ['CANDIDATE_ASSERTION', 'CANDIDATE_COMMENT'], 5))

    def test_v12_fixture_changes_only_binding(self):
        # Sample: Compare migration v11 and v12. Check equal files and binding foundation-generic-v12.
        from durai.qualification import TEMPLATES, load
        old, new = TEMPLATES / 'task-graph-tenancy-migration-v11', TEMPLATES / 'task-graph-tenancy-migration-v12'
        for part in ('protected', 'workspace', 'reference'):
            self.assertEqual(load(old / part), load(new / part), part)
        before, after = (json.loads((folder / 'task.json').read_text(encoding='utf-8')) for folder in (old, new))
        self.assertEqual(after['binding'], {'id': 'foundation-generic-v12', 'version': '12'})
        self.assertEqual({key: value for key, value in before.items() if key not in ('binding', 'taskPrefix')},
                         {key: value for key, value in after.items() if key not in ('binding', 'taskPrefix')})


if __name__ == '__main__':
    unittest.main()
