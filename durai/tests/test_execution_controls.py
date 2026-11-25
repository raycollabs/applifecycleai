"""Sample: Share two model calls across M-1 and M-2. Replay edit O-1 once. Kill worker W-1 and child C-1."""
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from langchain_core.messages import HumanMessage
from durai.common import Conflict, uid
from durai.coordination import Coordination
from durai.db import connect, prepare
from durai.execution_budget import BudgetedModel
from durai.harness import prepare_checkpoints
from durai.process_owner import OwnedProcess, alive
from durai.providers import Providers
from durai.runtime_adapter import invoke
from durai.runtime_deepagents import DeepAgentsWorker
import durai.runtime_deepagents as runtime_module
from durai.task_contract import build_task
from durai.task_ledger import TaskLedger
from durai.tool_ledger import apply_once, operations
from test_dev_loop import BASE, FIX, loop_spec, pins_for
from test_workspace_worker import SequenceModel

DATABASE = 'durai_test'


def claimed(budget=None, runtime=None):
    # Sample: Seal T-1 with two model calls. Create attempt 1. Claim it as W-1. Return the ledger, attempt, and generation.
    changes = {}
    if budget:
        changes['budget'] = budget
    if runtime:
        changes['runtime'] = runtime
    spec = loop_spec(**changes)
    task = build_task(spec)['task']
    ledger = TaskLedger(DATABASE)
    attempt = ledger.create_attempt(task, 'run-' + uid(), pins_for(spec))
    return ledger, task, attempt, ledger.claim(attempt['id'], 'W-1', seconds=60)


def budgeted(ledger, attempt, generation, label, inner):
    # Sample: Wrap route M-1 with the T-1 budget and the W-1 lease.
    return BudgetedModel(inner=inner, ledger=ledger, budget_id=attempt['budget_id'], attempt_id=attempt['id'],
                         owner='W-1', generation=generation, label=label, lease_seconds=60)


class BudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply core_0009 to durai_test.
        prepare(DATABASE)

    def test_routes_share_one_request_budget(self):
        # Sample: Reserve two calls for T-1. Use both on M-1. Check that M-2 receives no call.
        ledger, task, attempt, generation = claimed({'modelCalls': 2, 'toolCalls': 40, 'seconds': 600, 'attempts': 2})
        first, second = SequenceModel(steps=[], replies=[]), SequenceModel(steps=[], replies=[])
        route_one = budgeted(ledger, attempt, generation, 'M-1', first)
        route_two = budgeted(ledger, attempt, generation, 'M-2', second)
        route_one.invoke([HumanMessage('Book 2 lots.')])
        route_one.invoke([HumanMessage('Book 2 lots.')])
        with self.assertRaisesRegex(Conflict, 'Model-call budget'):
            route_two.invoke([HumanMessage('Book 2 lots.')])
        self.assertEqual((first.calls, second.calls), (2, 0))
        self.assertEqual(ledger.usage(attempt['budget_id'])['modelCalls'], 2)

    def test_pool_reservations_do_not_double_count(self):
        # Sample: Set six shared workers and two reserved evaluators. Fill the shared pool. Check two evaluators remain and the total is eight.
        coordinator, shared, evaluators = Coordination(DATABASE), 'shared-' + uid()[:8], 'evaluators-' + uid()[:8]
        coordinator.pool(shared, 6)
        coordinator.pool(evaluators, 2)
        granted = [coordinator.reserve(shared, 'W-' + str(number)) for number in range(7)]
        reserved = [coordinator.reserve(evaluators, 'E-' + str(number)) for number in range(3)]
        self.assertEqual(granted, [True] * 6 + [False])
        self.assertEqual(reserved, [True, True, False])
        self.assertEqual(granted.count(True) + reserved.count(True), 8)

    def test_cancelled_attempt_stops_model_calls(self):
        # Sample: Claim attempt 1. Move it to CANCELLED. Call the model. Check refusal and zero inner calls.
        ledger, task, attempt, generation = claimed()
        inner = SequenceModel(steps=[], replies=[])
        ledger.cancel(attempt['id'], {'operator': 'test'})
        with self.assertRaisesRegex(Conflict, 'Stale'):
            budgeted(ledger, attempt, generation, 'M-1', inner).invoke([HumanMessage('Book 2 lots.')])
        self.assertEqual(inner.calls, 0)
        self.assertEqual(ledger.get(attempt['id'])['state'], 'CANCELLED')


class RecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply core_0009 and checkpoint tables to durai_test.
        prepare(DATABASE)
        prepare_checkpoints(DATABASE)

    def test_completed_tool_replay_has_one_effect(self):
        # Sample: Apply operation O-1 once. Ignore its result. Replay O-1. Check one file mutation and the same result.
        target = Path(tempfile.mkdtemp(prefix='durai-op-')) / 'a.py'
        target.write_text('')
        operation = 'O-' + uid()

        def append():
            # Sample: Append one line to a.py. Return the line count 1.
            with target.open('a') as handle:
                handle.write('return 200\n')
            return {'content': str(len(target.read_text().splitlines()))}

        first = apply_once(operation, {'path': 'a.py'}, append, DATABASE)
        second = apply_once(operation, {'path': 'a.py'}, append, DATABASE)
        self.assertEqual((first['content'], second['content'], second['replayed']), ('1', '1', True))
        self.assertEqual(target.read_text(), 'return 200\n')
        with self.assertRaisesRegex(Conflict, 'different input'):
            apply_once(operation, {'path': 'b.py'}, append, DATABASE)

    def test_interrupted_turn_resumes_without_duplicate(self):
        # Sample: Edit shares.py through operation O-1. Fail before the tool result is saved. Resume. Check one ledger row and 200 shares.
        from langgraph.checkpoint.postgres import PostgresSaver
        ledger, task, attempt, generation = claimed()
        scratch = Path(tempfile.mkdtemp(prefix='durai-resume-'))
        real, failures = runtime_module.apply_once, []

        def fail_once(*args, **kwargs):
            # Sample: Apply O-1 and save it. Fail before the tool message returns. Pass on the second call.
            result = real(*args, **kwargs)
            if not failures:
                failures.append(result)
                raise RuntimeError('Worker process stopped after the tool effect')
            return result

        with connect('durai_runtime', DATABASE, autocommit=True) as connection:
            connection.execute('SET search_path=durai_checkpoints')
            saver = PostgresSaver(connection)
            first = DeepAgentsWorker(SequenceModel(steps=[FIX], replies=[]), scratch, attempt['id'], saver, DATABASE)
            with patch.object(runtime_module, 'apply_once', fail_once), self.assertRaises(RuntimeError):
                invoke(first, 'execute', task, dict(BASE))
            model = SequenceModel(steps=[FIX], replies=[])
            second = DeepAgentsWorker(model, scratch, attempt['id'], saver, DATABASE)
            result = invoke(second, 'resume', task, dict(BASE))
        self.assertEqual(result['state'], 'CANDIDATE')
        self.assertEqual(result['files']['/workspace/shares.py'], BASE['/workspace/shares.py'].replace(b' - 1', b''))
        self.assertEqual(len(operations(attempt['id'], DATABASE)), 1)
        self.assertNotIn('Error', model.replies[0])
        self.assertFalse(second.root().exists())

    def test_cancel_stops_owned_children(self):
        # Sample: Start worker W-1 and child C-1. Cancel W-1. Check both stopped and one CANCELLED outcome.
        ledger, task, attempt, generation = claimed()
        marker = Path(tempfile.mkdtemp(prefix='durai-tree-')) / 'child.pid'
        script = ('import subprocess,sys,time;'
                  'c=subprocess.Popen([sys.executable,"-c","import time; time.sleep(120)"]);'
                  'open(sys.argv[1],"w").write(str(c.pid));time.sleep(120)')
        process = OwnedProcess([sys.executable, '-c', script, str(marker)])
        deadline = time.monotonic() + 20
        while not (marker.exists() and marker.read_text()) and time.monotonic() < deadline:
            time.sleep(.1)
        child = int(marker.read_text())
        self.assertTrue(alive(child))
        process.cancel()
        ledger.cancel(attempt['id'], {'processTree': 'killed'})
        time.sleep(.5)
        self.assertFalse(alive(process.process.pid))
        self.assertFalse(alive(child))
        self.assertEqual(ledger.get(attempt['id'])['state'], 'CANCELLED')

    def test_runtime_version_is_pinned_on_resume(self):
        # Sample: Pause R-1 on V-1. Register V-2. Resume R-1. Check V-1. Remove V-1. Check an unavailable-version result.
        providers = Providers()
        providers.register('coding-worker', 'deepagents@V-1', 'worker V-1')
        providers.register('coding-worker', 'deepagents@V-2', 'worker V-2')
        with providers.resolve('coding-worker', 'deepagents@V-1') as provider:
            self.assertEqual(provider, 'worker V-1')
        with providers.acquire('coding-worker') as provider:
            self.assertEqual(provider, 'worker V-2')
        providers.deregister('coding-worker', 'deepagents@V-1')
        with self.assertRaisesRegex(Conflict, 'unavailable'):
            with providers.resolve('coding-worker', 'deepagents@V-1'):
                pass

    def test_replacement_drains_pinned_runs(self):
        # Sample: Start R-1 on V-1. Register V-2. Refuse removal of V-1 until R-1 ends.
        version = 'V-1-' + uid()[:8]
        ledger, task, attempt, generation = claimed(runtime={'name': 'deepagents', 'version': version})
        providers = Providers()
        providers.register('coding-worker', 'deepagents@' + version, 'worker V-1')
        providers.register('coding-worker', 'deepagents@V-2', 'worker V-2')
        with self.assertRaisesRegex(Conflict, 'still pin'):
            providers.retire('coding-worker', 'deepagents@' + version, ledger.active_pins('deepagents', version))
        ledger.move(attempt['id'], 'W-1', generation, 'FAILED')
        providers.retire('coding-worker', 'deepagents@' + version, ledger.active_pins('deepagents', version))
        with providers.acquire('coding-worker') as provider:
            self.assertEqual(provider, 'worker V-2')


if __name__ == '__main__':
    unittest.main()
