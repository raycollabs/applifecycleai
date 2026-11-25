"""Sample: Refuse an edit with 0 matches and name the closest file lines. Retry a stalled worker with the last refusal text."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from langchain_core.messages import ToolMessage
from durai.edit_guard import EditGuardMiddleware, WorkerStalled
from durai.failure_triage import TRIAGE_RULES_V14, FailureTriage
from durai.workspace_manifest import materialize
from durai.task_contract import build_task
from durai.qualification import FIXTURE, MODULE, load
from test_task_contract import feature_spec


class TestEditRecovery(unittest.TestCase):
    def setUp(self):
        # Sample: Materialize the shell, replace its file with 20 lines, and put a protected test file next to it.
        self.temporary = tempfile.TemporaryDirectory(prefix='durai-recovery-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root, self.files = Path(self.temporary.name), load(FIXTURE / 'workspace')
        materialize(self.root, self.files)
        lines = ['x = %d' % number for number in range(1, 21)]
        lines[7], lines[19], lines[11] = '    return None', '    return None', 'def hold_seat(order_id, seat_id):'
        (self.root / MODULE.lstrip('/')).write_text('\n'.join(lines) + '\n', encoding='utf-8')
        (self.root / 'workspace/tests').mkdir(exist_ok=True)
        (self.root / 'workspace/tests/test_money.py').write_text('assert balance == 10000\n', encoding='utf-8')
        self.task = build_task(feature_spec(writePaths=[MODULE], editScope={MODULE: ['validate_change']}))['task']
        self.guard = EditGuardMiddleware(self.task, self.files, self.root, lambda kind, payload: None)

    def refuse(self, old, replace_all=False):
        # Sample: Send old_string "return None" with replace_all false. Return the tool error text.
        request = SimpleNamespace(tool_call={'id': 'C-1', 'name': 'edit_file', 'args': {
            'file_path': MODULE, 'old_string': old, 'new_string': 'x', 'replace_all': replace_all}})
        result = self.guard.wrap_tool_call(request, lambda call: ToolMessage(content='applied', tool_call_id='C-1'))
        self.assertEqual(result.status, 'error')
        return result.content

    def test_absent_match_shows_closest_lines(self):
        # Sample: File has line 12 "def hold_seat(order_id, seat_id):". Send old_string "def hold_seat(seat_id, order_id):". Check "0 matches", "12:" and that line.
        text = self.refuse('def hold_seat(seat_id, order_id):')
        self.assertIn('0 matches', text)
        self.assertIn('12: def hold_seat(order_id, seat_id):', text)
        self.assertIn('copy old_string exactly', text)

    def test_ambiguous_match_shows_match_lines(self):
        # Sample: File has "    return None" on lines 8 and 20. Send old_string "return None" with replace_all false. Check "2 matches", "8, 20" and "replace_all".
        text = self.refuse('return None')
        self.assertIn('2 matches at lines 8, 20', text)
        self.assertIn('replace_all', text)

    def test_refusal_has_no_protected_text(self):
        # Sample: Put "assert balance == 10000" in a protected test file. Send a 0-match edit. Check the refusal has no "assert balance".
        text = self.refuse('assert balance == 10000')
        self.assertNotIn('assert balance', text)
        self.assertIn('0 matches', text)

    def test_equal_proposals_give_equal_text_and_stall(self):
        # Sample: Send one 0-match edit 3 times. Check equal text twice, then STALLED on call 3.
        first = self.refuse('def hold_seat(seat_id, order_id):')
        self.assertEqual(self.refuse('def hold_seat(seat_id, order_id):'), first)
        with self.assertRaises(WorkerStalled):
            self.refuse('def hold_seat(seat_id, order_id):')


def stall_facts(attempts=1, calls=20):
    # Sample: Make facts for a guard stall with 1 attempt, 20 calls and 300 s left. Return the fact map.
    return {'verdict': None, 'controlFault': 'STALLED', 'remainingAttempts': attempts, 'remainingCalls': calls,
            'remainingSeconds': 300, 'evaluatorRetries': 0, 'packageDigest': None, 'taskDigest': 'T-1'}


class TestStallTriage(unittest.TestCase):
    def decide(self, **changes):
        # Sample: Decide a guard stall with the v14 rules. Return the decision.
        return FailureTriage(TRIAGE_RULES_V14, lambda *args: None, 1, 5).decide(stall_facts(**changes))

    def test_stall_with_attempts_left_retries(self):
        # Sample: Triage origin guard with attempts, calls and time left. Check RETRY_CANDIDATE and rule stalled-retry.
        decision = self.decide()
        self.assertEqual(decision['choice'], 'RETRY_CANDIDATE')
        self.assertEqual(decision['reasons'], ['stalled-retry'])
        self.assertEqual(decision['origin'], 'guard')

    def test_stall_without_attempts_stops(self):
        # Sample: Triage origin guard with attempts 0. Check STOP_STALLED. With 4 calls left, check STOP_STALLED too.
        self.assertEqual(self.decide(attempts=0)['choice'], 'STOP_STALLED')
        self.assertEqual(self.decide(calls=4)['choice'], 'STOP_STALLED')

    def test_pinned_pack_rules_equal_v14_rules(self):
        # Sample: Build the transaction pack. Check its failure-triage rules equal TRIAGE_RULES_V14 and stalled-retry sits right before stalled-worker.
        from durai.blueprint_patterns import transaction_pack
        from durai.packs import generic_pack
        record = next(item for item in transaction_pack(generic_pack('14'))['records'] if item['id'] == 'decision.failure-triage')
        self.assertEqual(record['body']['rules'], TRIAGE_RULES_V14)
        ids = [rule['id'] for rule in record['body']['rules']]
        self.assertEqual(ids.index('stalled-retry') + 1, ids.index('stalled-worker'))

    def test_stall_retry_feedback_has_refusal(self):
        # Sample: Attempt 1 sends the same 0-match edit 3 times and stalls. Check a fresh attempt 2 passes and the attempt 1 note has "0 matches".
        from durai.db import prepare
        from durai.harness import prepare_checkpoints
        from test_dev_loop import FIX, LoopHarness, loop_spec
        from test_failure_triage import decisions
        prepare('durai_test')
        prepare_checkpoints('durai_test')
        from durai.blueprint_patterns import transaction_pack
        from durai.common import uid
        from durai.packs import generic_pack
        from durai.worker_contract import resolve_contract
        from tools.build_transaction_patterns import install
        identity = 'stall-retry-' + uid()[:8]
        install(transaction_pack(generic_pack('14')), 'durai_test', binding=identity)
        task = {'binding': {'bindingId': identity}, 'pins': {'contractDigest': resolve_contract(identity, database='durai_test')['digest']}}
        absent = ('edit_file', {'file_path': '/workspace/shares.py', 'old_string': 'return lots * lot_size - 9', 'new_string': 'return 1'})
        harness = LoopHarness([[absent, absent, absent], [FIX]])
        spec = loop_spec(binding={'bindingId': task['binding']['bindingId'], 'contractDigest': task['pins']['contractDigest']})
        state = harness.run(spec)
        self.assertEqual(state['outcome'], 'PASSED')
        self.assertIn('0 matches', state['history'][0]['feedback'])
        self.assertIn('STALLED', state['history'][0]['feedback'])
        self.assertEqual([(row['choice'], row['reasons']) for row in decisions(harness.run_id)][0], ('RETRY_CANDIDATE', ['stalled-retry']))
        self.assertEqual(len(harness.models), 2)
        self.assertIsNone(state.get('resume_package'))


if __name__ == '__main__':
    unittest.main()
