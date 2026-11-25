"""Sample: Create attempt 1 for T-1. Fence W-1 after W-2 claims. Refuse a third model call."""
import time
import unittest
from psycopg import sql
from durai.common import Conflict, uid
from durai.db import prepare, transaction
from durai.task_contract import build_task
from durai.task_ledger import TaskLedger
from test_task_contract import current_pins, feature_spec

DATABASE = 'durai_test'


class TaskLedgerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply core_0009 to durai_test. Keep durai_local unchanged.
        prepare(DATABASE)

    def setUp(self):
        # Sample: Clear durai_tasks rows in durai_test. Seal T-1 with a new task ID.
        with transaction('postgres', DATABASE) as db:
            tables = db.execute("SELECT schemaname,tablename FROM pg_tables WHERE schemaname='durai_tasks'").fetchall()
            db.execute(sql.SQL('TRUNCATE {} CASCADE').format(sql.SQL(',').join(sql.Identifier(r['schemaname'], r['tablename']) for r in tables)))
        self.ledger = TaskLedger(DATABASE)
        self.task = build_task(feature_spec(taskId='T-' + uid()[:8]))['task']

    def test_attempt_pins_are_persisted(self):
        # Sample: Create attempt 1 for T-1 on deepagents 0.7.38. Read it back. Check all pins.
        attempt = self.ledger.create_attempt(self.task, 'run-' + uid(), current_pins())
        self.assertEqual((attempt['attempt'], attempt['state'], attempt['runtime'], attempt['runtime_version']),
                         (1, 'READY', 'deepagents', '0.7.38'))
        self.assertEqual(attempt['contract_digest'], self.task['pins']['contractDigest'])
        self.assertEqual(attempt['source_digest'], self.task['sourceRevision'])
        self.assertEqual(self.ledger.usage(attempt['budget_id']), {'attempts': 1})
        self.ledger.create_attempt(self.task, 'run-' + uid(), current_pins())
        with self.assertRaisesRegex(Conflict, 'Attempt budget'):
            self.ledger.create_attempt(self.task, 'run-' + uid(), current_pins())
        with self.assertRaisesRegex(Conflict, 'stale'):
            self.ledger.create_attempt(self.task, 'run-' + uid(), current_pins(runtime='deepagents@0.8.0'))

    def test_stale_owner_cannot_transition(self):
        # Sample: Claim as W-1 for 1 second. Wait 1.2 seconds. Claim as W-2. Move as W-1. Check refusal.
        attempt = self.ledger.create_attempt(self.task, 'run-' + uid(), current_pins())
        first = self.ledger.claim(attempt['id'], 'W-1', seconds=1)
        self.assertIsNone(self.ledger.claim(attempt['id'], 'W-2'))
        time.sleep(1.2)
        second = self.ledger.claim(attempt['id'], 'W-2', seconds=30)
        self.assertEqual((first, second), (1, 2))
        with self.assertRaisesRegex(Conflict, 'Stale'):
            self.ledger.move(attempt['id'], 'W-1', first, 'WAITING_FOR_REVIEW')
        with self.assertRaisesRegex(Conflict, 'Stale'):
            self.ledger.renew(attempt['id'], 'W-1', first)
        self.assertEqual(self.ledger.move(attempt['id'], 'W-2', second, 'WAITING_FOR_REVIEW'), 'WAITING_FOR_REVIEW')
        with self.assertRaisesRegex(Conflict, 'release controller'):
            self.ledger.move(attempt['id'], 'W-2', second, 'SUCCEEDED')
        with transaction(database=DATABASE) as db:
            kinds = [row['kind'] for row in db.execute('SELECT kind FROM durai_coordination.event WHERE run_id=%s ORDER BY sequence',
                                                       (attempt['run_id'],)).fetchall()]
        self.assertEqual(kinds, ['ATTEMPT_READY', 'ATTEMPT_CLAIMED', 'ATTEMPT_LOST', 'ATTEMPT_CLAIMED', 'ATTEMPT_WAITING_FOR_REVIEW'])

    def test_budget_reserves_then_reconciles_once(self):
        # Sample: Set 2 model calls. Reserve 2. Reserve a third. Check refusal. Reconcile R-1 twice. Check the second refusal.
        budget = self.ledger.register_task(build_task(feature_spec(taskId='T-' + uid()[:8], budget={'modelCalls': 2, 'toolCalls': 40, 'seconds': 600, 'attempts': 2}))['task'])
        self.assertTrue(self.ledger.reserve(budget, 'R-1', 'modelCalls', 1))
        self.assertTrue(self.ledger.reserve(budget, 'R-1', 'modelCalls', 1))
        self.assertTrue(self.ledger.reserve(budget, 'R-2', 'modelCalls', 1))
        self.assertFalse(self.ledger.reserve(budget, 'R-3', 'modelCalls', 1))
        with self.assertRaisesRegex(Conflict, 'different input'):
            self.ledger.reserve(budget, 'R-1', 'modelCalls', 2)
        self.assertEqual(self.ledger.reconcile(budget, 'R-1', 0), {'reserved': 1, 'actual': 0})
        with self.assertRaisesRegex(Conflict, 'already reconciled'):
            self.ledger.reconcile(budget, 'R-1', 1)
        self.assertTrue(self.ledger.reserve(budget, 'R-3', 'modelCalls', 1))
        self.assertEqual(self.ledger.usage(budget), {'modelCalls': 2})

    def test_foreign_application_cannot_read_attempt(self):
        # Sample: Create an attempt as durai_runtime. Read it as durai_foreign. Check zero rows.
        attempt = self.ledger.create_attempt(self.task, 'run-' + uid(), current_pins())
        foreign = TaskLedger(DATABASE, role='durai_foreign')
        with self.assertRaises(KeyError):
            foreign.get(attempt['id'])
        with transaction('durai_foreign', DATABASE) as db:
            self.assertEqual(db.execute('SELECT count(*) AS n FROM durai_tasks.task').fetchone()['n'], 0)
            self.assertEqual(db.execute('SELECT count(*) AS n FROM durai_tasks.budget_entry').fetchone()['n'], 0)


if __name__ == '__main__':
    unittest.main()
