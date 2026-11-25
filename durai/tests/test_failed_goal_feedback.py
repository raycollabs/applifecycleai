"""Sample: Attempt 1 books 198 shares. Check attempt 2 feedback names test_two_lots and shows no 198 or 200."""
import json
import tempfile
import unittest
from pathlib import Path
from durai.failure_triage import triage_procedure
from durai.task_evaluator import SandboxEvaluator
from durai.workspace_manifest import materialize
from test_failure_triage import bound
from test_resume_retry import FIX_FROM_198


def run_with(version):
    # Sample: Run attempt 1 with 198 shares and attempt 2 with the fix on pack v13. Return the final state and harness.
    from test_dev_loop import WRONG, LoopHarness, loop_spec
    task = bound(version)
    harness = LoopHarness([[WRONG], [FIX_FROM_198]])
    state = harness.run(loop_spec(binding={'bindingId': task['binding']['bindingId'],
                                           'contractDigest': task['pins']['contractDigest']}))
    return state, harness


class FailedGoalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply migrations and checkpoint tables to durai_test.
        from durai.db import prepare
        from durai.harness import prepare_checkpoints
        prepare('durai_test')
        prepare_checkpoints('durai_test')

    def test_failed_goal_names_survive_truncation(self):
        # Sample: Print "FAIL: test_two_lots (tests.T.test_two_lots)", then 6000 characters. Check failedGoals ['test_two_lots'].
        with tempfile.TemporaryDirectory(prefix='durai-goal-names-') as folder:
            evaluator = SandboxEvaluator(Path(folder))
            materialize(Path(folder) / 'source', {'/workspace/a.py': b'x = 1\n'})
            program = 'print("FAIL: test_two_lots (tests.T.test_two_lots)")\nprint("x" * 6000)\nraise SystemExit(1)'
            outcome = evaluator.run(Path(folder) / 'source/workspace', ['python', '-c', program])
        self.assertNotIn('test_two_lots', outcome['output'])
        self.assertEqual(outcome['failedGoals'], ['test_two_lots'])

    def test_names_limited_to_protected_suite(self):
        # Sample: Read failedGoals test_two_lots and test_fake against suite H-1. Check only test_two_lots remains.
        from durai.development_loop import suite_goal_names
        from test_dev_loop import SUITE
        allowed = suite_goal_names(SUITE)
        self.assertEqual([name for name in ['test_two_lots', 'test_fake'] if name in allowed], ['test_two_lots'])

    def test_v13_feedback_names_goals(self):
        # Sample: Attempt 1 books 198 shares on v13. Check feedback names test_two_lots, has no 198 or 200, and one delivery event.
        from durai.execution_events import replay
        state, harness = run_with('13')
        self.assertEqual(state['outcome'], 'PASSED', state.get('verdict'))
        feedback = state['history'][0]['feedback']
        self.assertIn('Failing protected goals: test_two_lots.', feedback)
        self.assertNotIn('198', feedback)
        self.assertNotIn('200', feedback)
        delivered = [row['payload'] for row in replay(harness.run_id, database='durai_test') if row['kind'] == 'FEEDBACK_GOALS_DELIVERED']
        self.assertEqual([row['goals'] for row in delivered], [['test_two_lots']])

    def test_v12_feedback_has_no_names(self):
        # Sample: Run the same attempts on v12. Check no test_two_lots in the feedback.
        state, harness = run_with('12')
        self.assertNotIn('test_two_lots', state['history'][0]['feedback'])

    def test_v13_pins_feedback_flag(self):
        # Sample: Bind pack v13. Check feedbackGoalNames true. Bind v12. Check false.
        self.assertTrue(triage_procedure(bound('13'), 'durai_test', 'durai_runtime')['feedbackGoalNames'])
        self.assertFalse(triage_procedure(bound('12'), 'durai_test', 'durai_runtime')['feedbackGoalNames'])

    def test_v13_fixture_changes_only_binding(self):
        # Sample: Compare migration v12 and v13. Check equal files and binding foundation-generic-v13.
        from durai.qualification import TEMPLATES, load
        old, new = TEMPLATES / 'task-graph-tenancy-migration-v12', TEMPLATES / 'task-graph-tenancy-migration-v13'
        for part in ('protected', 'workspace', 'reference'):
            self.assertEqual(load(old / part), load(new / part), part)
        before, after = (json.loads((folder / 'task.json').read_text(encoding='utf-8')) for folder in (old, new))
        self.assertEqual(after['binding'], {'id': 'foundation-generic-v13', 'version': '13'})
        self.assertEqual({key: value for key, value in before.items() if key not in ('binding', 'taskPrefix')},
                         {key: value for key, value in after.items() if key not in ('binding', 'taskPrefix')})


if __name__ == '__main__':
    unittest.main()
