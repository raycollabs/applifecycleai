"""Sample: Run T-1 through the loop. Attempt 1 returns 199 and fails. Attempt 2 returns 200 and waits for review."""
from pathlib import Path
from functools import lru_cache
import tempfile
import unittest
from langgraph.types import Command
from durai.candidate_store import CandidateStore
from durai.common import Conflict, digest, uid
from durai.db import prepare
from durai.development_loop import CAPABILITY, build_loop, development_graph
from durai.harness import prepare_checkpoints
from durai.providers import Providers
from durai.runtime_deepagents import DeepAgentsWorker
from durai.task_contract import build_task
from durai.task_evaluator import SandboxEvaluator
from durai.task_ledger import TaskLedger
from durai.workspace_manifest import manifest
from test_task_contract import feature_spec
from test_workspace_worker import SequenceModel

DATABASE = 'durai_test'
BASE = {'/workspace/shares.py': b'def shares(lots, lot_size):\n    # Sample: Book 2 lots of 100. Return 200 shares.\n    return lots * lot_size - 1\n'}
SUITE = {'/workspace/tests/__init__.py': b'',
         '/workspace/tests/test_shares.py': b'"""Sample: Book 2 lots of 100. Check 200 shares."""\nimport unittest\nfrom shares import shares\n\n\n'
                                            b'class SharesTests(unittest.TestCase):\n    def test_two_lots(self):\n'
                                            b'        # Sample: Book 2 lots of 100. Check 200 shares.\n        self.assertEqual(shares(2, 100), 200)\n'}
FIX = ('edit_file', {'file_path': '/workspace/shares.py', 'old_string': 'return lots * lot_size - 1', 'new_string': 'return lots * lot_size'})
WRONG = ('edit_file', {'file_path': '/workspace/shares.py', 'old_string': 'return lots * lot_size - 1', 'new_string': 'return lots * lot_size - 2'})


@lru_cache(maxsize=8)
def loop_binding(host, port, settings_path):
    # Sample: Open a fresh test server. Install G-7 and return one real contract pin for the loop fixture.
    from durai.packs import PackStore, generic_pack
    from durai.worker_contract import resolve_contract
    prepare(DATABASE)
    store = PackStore(DATABASE)
    reference = store.install(generic_pack('7'))
    identity = 'loop-fixture-generic-v7'
    store.bind(identity, [reference], 'SYNTHETIC_LOCAL')
    return identity, resolve_contract(identity, database=DATABASE)['digest']


def loop_spec(suite=None, **changes):
    # Sample: Build FEATURE T-1 for shares.py with suite H-1, the locked image, and the installed deepagents version.
    evaluator = SandboxEvaluator(tempfile.gettempdir())
    from durai.db import server
    target = server()
    identity, checksum = loop_binding(target['host'], target['port'], target['settings'])
    values = dict(taskId='T-' + uid()[:8], writePaths=['/workspace/shares.py'], deliverables=['/workspace/shares.py'],
                  binding={'bindingId': identity, 'contractDigest': checksum},
                  validationCommands=['python-compile', 'python-unittest', 'comment-policy'],
                  sourceRevision=manifest(BASE)['digest'], environment={'imageId': evaluator.image},
                  protectedSuite={'id': 'H-1', 'digest': manifest(suite or SUITE)['digest']},
                  runtime={'name': 'deepagents', 'version': DeepAgentsWorker.version})
    values.update(changes)
    return feature_spec(**values)


def pins_for(spec):
    # Sample: Read T-1 pins from its spec. Return source, contract, suite, image, and runtime.
    return {'sourceRevision': spec['sourceRevision'], 'contractDigest': spec['binding']['contractDigest'],
            'suiteDigest': spec['protectedSuite']['digest'], 'imageId': spec['environment']['imageId'],
            'runtime': spec['runtime']['name'] + '@' + spec['runtime']['version']}


class Sources:
    def files(self, task):
        # Sample: Return the baseline shares.py that books 199 shares for 2 lots of 100.
        return dict(BASE)

    def suite(self, task):
        # Sample: Return suite H-1 with the 200-share goal.
        return dict(SUITE)


class LoopHarness:
    def __init__(self, scripts):
        # Sample: Script attempt 1 with WRONG and attempt 2 with FIX. Create the ledger, store, and evaluator.
        self.scratch = Path(tempfile.mkdtemp(prefix='durai-loop-'))
        self.ledger = TaskLedger(DATABASE)
        self.store = CandidateStore(self.scratch / 'candidates')
        self.evaluator = SandboxEvaluator(self.scratch)
        self.providers = Providers()
        self.providers.register(CAPABILITY, 'deepagents@' + DeepAgentsWorker.version,
                                lambda model, scratch, attempt_id: DeepAgentsWorker(model, scratch, attempt_id))
        self.scripts, self.models = scripts, []

    def model(self, attempt):
        # Sample: Return the scripted model for attempt 2. Record it.
        model = SequenceModel(steps=self.scripts[attempt - 1], replies=[])
        self.models.append(model)
        return model

    def run(self, spec, current=None, resume=None):
        # Sample: Start run R-1 for T-1. Stop at the review interrupt. Resume with APPROVE when given.
        builder = build_loop(self.ledger, self.providers, self.evaluator, self.store, Sources(), self.model, self.scratch / 'work')
        run_id = getattr(self, 'run_id', None) or 'run-' + uid()
        self.run_id = run_id
        config = {'configurable': {'thread_id': run_id}}
        with development_graph(builder, DATABASE) as graph:
            if resume is not None:
                return graph.invoke(Command(resume=resume), config)
            return graph.invoke({'run_id': run_id, 'spec': spec, 'current': current or pins_for(spec)}, config)


class EvaluatorTests(unittest.TestCase):
    def setUp(self):
        # Sample: Create evaluator E-1 with an empty scratch folder.
        self.evaluator = SandboxEvaluator(Path(tempfile.mkdtemp(prefix='durai-eval-')))

    def test_modified_grader_cannot_create_pass(self):
        # Sample: Change one byte in suite H-1. Check refusal before a PASS verdict.
        task = build_task(loop_spec())['task']
        changed = dict(SUITE)
        changed['/workspace/tests/test_shares.py'] = SUITE['/workspace/tests/test_shares.py'].replace(b'200)', b'199)')
        with self.assertRaisesRegex(Conflict, 'Protected suite digest differs'):
            self.evaluator.evaluate(task, {'/workspace/shares.py': b''}, changed)

    def test_sandbox_runs_protected_suite(self):
        # Sample: Run suite H-1 on a candidate that returns 200 shares. Check PASS. Run it on 199. Check FAIL.
        task = build_task(loop_spec())['task']
        good = {'/workspace/shares.py': BASE['/workspace/shares.py'].replace(b' - 1', b'')}
        passed = self.evaluator.evaluate(task, good, SUITE)
        self.assertEqual(passed['verdict'], 'PASS', passed['results'])
        failed = self.evaluator.evaluate(task, dict(BASE), SUITE)
        self.assertEqual(failed['verdict'], 'FAIL')
        self.assertEqual([item['state'] for item in failed['results']], ['PASS', 'FAIL', 'PASS'])

    def test_candidate_outbound_access_is_denied(self):
        # Sample: Run a candidate test that opens a socket to 1.1.1.1:53. Check that the connection fails.
        suite = {'/workspace/tests/__init__.py': b'',
                 '/workspace/tests/test_network.py': b'import socket\nimport unittest\n\n\nclass NetworkTests(unittest.TestCase):\n'
                                                     b'    def test_outbound_is_blocked(self):\n'
                                                     b'        with self.assertRaises(OSError):\n'
                                                     b'            socket.create_connection(("1.1.1.1", 53), timeout=3)\n'}
        task = build_task(loop_spec(suite=suite, validationCommands=['python-unittest']))['task']
        verdict = self.evaluator.evaluate(task, dict(BASE), suite)
        self.assertEqual(verdict['verdict'], 'PASS', verdict['results'])


class LoopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply core_0009 and the LangGraph checkpoint tables to durai_test.
        prepare(DATABASE)
        prepare_checkpoints(DATABASE)

    def test_failed_candidate_is_retained_then_retry_passes(self):
        # Sample: Attempt 1 writes 198 and fails. Check a retained package. Attempt 2 writes 200. Check WAITING_FOR_REVIEW.
        harness = LoopHarness([[WRONG], [FIX]])
        spec = loop_spec()
        state = harness.run(spec)
        self.assertEqual(state['outcome'], 'PASSED')
        self.assertIn('__interrupt__', state)
        first, second = state['history']
        self.assertEqual((first['verdict'], second['verdict']), ('FAIL', 'PASS'))
        self.assertEqual(harness.ledger.get(first['attemptId'])['state'], 'FAILED')
        self.assertIn(b'- 2', harness.store.restore(**first['package'])['/workspace/shares.py'])
        self.assertEqual(harness.ledger.get(second['attemptId'])['state'], 'WAITING_FOR_REVIEW')
        final = harness.run(spec, resume={'decision': 'APPROVE', 'reviewer': 'test-reviewer'})
        self.assertEqual(final['outcome'], 'WAITING_FOR_REVIEW')
        self.assertEqual(harness.ledger.get(second['attemptId'])['state'], 'WAITING_FOR_REVIEW')
        from durai.execution_events import replay
        approvals = [row for row in replay(harness.run_id, database=DATABASE) if row['kind'] == 'ATTEMPT_REVIEW_APPROVED']
        self.assertEqual([(row['payload']['attemptId'], row['payload']['releaseAuthority']) for row in approvals],
                         [(second['attemptId'], False)])

    def test_review_reject_records_failed(self):
        # Sample: Pass attempt 1. Reject it at review. Check FAILED and the retained package digest.
        harness = LoopHarness([[FIX]])
        spec = loop_spec()
        state = harness.run(spec)
        final = harness.run(spec, resume={'decision': 'REJECT', 'reviewer': 'test-reviewer'})
        self.assertEqual(final['outcome'], 'REJECTED')
        attempt = state['history'][0]
        self.assertEqual(harness.ledger.get(attempt['attemptId'])['state'], 'FAILED')
        restored = harness.store.restore(**attempt['package'])
        self.assertEqual(restored['/workspace/shares.py'], BASE['/workspace/shares.py'].replace(b' - 1', b''))

    def test_missing_readiness_stops_before_model(self):
        # Sample: Omit failingAcceptanceGoal. Check NEEDS_CLARIFICATION and zero model calls.
        harness = LoopHarness([[FIX]])
        spec = loop_spec(readiness={'shellRunnable': True})
        state = harness.run(spec)
        self.assertEqual(state['outcome'], 'NEEDS_CLARIFICATION')
        self.assertEqual(harness.models, [])

    def test_policy_change_invalidates_readiness(self):
        # Sample: Seal the task with contract C-1. Start the loop with current contract C-2. Check refusal and zero model calls.
        harness = LoopHarness([[FIX]])
        spec = loop_spec()
        state = harness.run(spec, current=dict(pins_for(spec), contractDigest=digest({'contract': 'C-2'})))
        self.assertEqual(state['outcome'], 'STALE')
        self.assertIn('contractDigest', state['detail'])
        self.assertEqual(harness.models, [])


if __name__ == '__main__':
    unittest.main()
