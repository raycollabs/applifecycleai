"""Sample: Replay events 3 and 4 without key sk-test-123456789. Refuse app B checkpoint reads. Refuse a stale aggregate."""
import json
import threading
import unittest
import psycopg
from langgraph.graph import StateGraph, START, END
from typing import TypedDict
from durai.checkpoint_tenancy import TenantCheckpointer
from durai.common import Conflict, uid
from durai.composite import CompositeTask
from durai.db import connect, prepare, transaction
from durai.execution_events import record, replay
from durai.harness import prepare_checkpoints
from durai.task_contract import build_task
from durai.task_ledger import TaskLedger
from test_dev_loop import loop_spec, pins_for

DATABASE = 'durai_test'


class Shares(TypedDict, total=False):
    shares: int


class AuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply core_0009 to durai_test.
        prepare(DATABASE)

    def test_event_replay_preserves_order_and_redaction(self):
        # Sample: Record events 1 to 4 with a synthetic API key. Replay after event 2. Check events 3 and 4 in order and no key.
        run_id = 'run-' + uid()
        with transaction(database=DATABASE) as db:
            db.execute("INSERT INTO durai_agents.run(application_id,id,state) VALUES(durai_core.application_id(),%s,'RUNNING')", (run_id,))
            for number in range(1, 5):
                record(db, run_id, 'STEP_' + str(number), {'shares': 100 * number, 'apiKey': 'sk-test-123456789',
                                                          'note': 'Authorization Bearer abc.def.ghi and sk-test-123456789'})
        events = replay(run_id, after=2, database=DATABASE)
        self.assertEqual([(row['sequence'], row['kind']) for row in events], [(3, 'STEP_3'), (4, 'STEP_4')])
        self.assertEqual(events[0]['payload']['shares'], 300)
        text = json.dumps(events)
        self.assertNotIn('sk-test-123456789', text)
        self.assertNotIn('abc.def.ghi', text)


class CheckpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply core_0009 and checkpoint tables to durai_test.
        prepare(DATABASE)
        prepare_checkpoints(DATABASE)

    def test_foreign_application_cannot_read_checkpoint(self):
        # Sample: Store app A checkpoint C-1. Read as app B. Check no state and a permission error on the table.
        from langgraph.checkpoint.postgres import PostgresSaver
        spec = loop_spec()
        task = build_task(spec)['task']
        owner = TaskLedger(DATABASE)
        attempt = owner.create_attempt(task, 'run-' + uid(), pins_for(spec))
        builder = StateGraph(Shares)
        builder.add_node('book', lambda state: {'shares': 200})
        builder.add_edge(START, 'book')
        builder.add_edge('book', END)
        config = {'configurable': {'thread_id': attempt['id']}}
        with connect('durai_runtime', DATABASE, autocommit=True) as connection:
            connection.execute('SET search_path=durai_checkpoints')
            saver = PostgresSaver(connection)
            builder.compile(checkpointer=TenantCheckpointer(saver, owner)).invoke({'shares': 0}, config)
            self.assertEqual(TenantCheckpointer(saver, owner).get_tuple(config).checkpoint['channel_values']['shares'], 200)
            foreign = TenantCheckpointer(saver, TaskLedger(DATABASE, role='durai_foreign'))
            with self.assertRaisesRegex(Conflict, 'not visible'):
                foreign.get_tuple(config)
            with self.assertRaises(Conflict):
                list(foreign.list(config))
        with connect('durai_foreign', DATABASE, autocommit=True) as connection:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                connection.execute('SELECT count(*) FROM durai_checkpoints.checkpoints')


class CompositeTests(unittest.TestCase):
    def test_child_change_invalidates_cached_ancestors(self):
        # Sample: Cache parent P-1 from C-1 and C-2. Change C-2 during a parent read. Check that the old revision cannot publish.
        first, second = CompositeTask(100), CompositeTask(100)
        parent = CompositeTask(children=[first, second])
        value, revision = parent.snapshot()
        self.assertEqual(value, 200)
        published = []
        writer = threading.Thread(target=second.set, args=(150,))
        writer.start()
        writer.join()
        with self.assertRaisesRegex(Conflict, 'revision changed'):
            parent.publish(value, revision, lambda *item: published.append(item))
        value, revision = parent.snapshot()
        parent.publish(value, revision, lambda *item: published.append(item))
        self.assertEqual(published, [(250, revision)])
        self.assertEqual(parent.computations, 2)


if __name__ == '__main__':
    unittest.main()
