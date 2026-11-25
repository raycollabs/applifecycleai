"""Sample: Register T-1. Create attempt 1. Claim it as W-1. Reserve 2 of 2 model calls. Refuse a third."""
from psycopg.types.json import Jsonb
from .common import Conflict, digest
from .execution_events import record as event
from .db import transaction
from .runtime_adapter import transition
from .task_contract import verify_task


class TaskLedger:
    def __init__(self, database='durai_local', role='durai_runtime'):
        # Sample: Open the ledger on durai_test as durai_runtime. Store no connection.
        self.database, self.role = database, role

    def register_task(self, task):
        # Sample: Register T-1 twice with digest D-1. Store one immutable row.
        body = {key: value for key, value in task.items() if key != 'digest'}
        if digest(body) != task.get('digest'):
            raise Conflict('Task digest differs')
        with transaction(self.role, self.database) as db:
            db.execute('INSERT INTO durai_tasks.task(application_id,digest,id,kind,document) VALUES(durai_core.application_id(),%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                       (task['digest'], task['taskId'], task['kind'], Jsonb(task)))
            db.execute('INSERT INTO durai_tasks.budget(application_id,id,limits) VALUES(durai_core.application_id(),%s,%s) ON CONFLICT DO NOTHING',
                       ('task:' + task['digest'], Jsonb(task['budget'])))
        return 'task:' + task['digest']

    def create_attempt(self, task, run_id, current):
        # Sample: Create attempt 1 for T-1 on deepagents 0.7.38. Reserve 1 of 2 attempts. Return READY.
        verify_task(task, current)
        budget_id = self.register_task(task)
        with transaction(self.role, self.database) as db:
            db.execute("INSERT INTO durai_agents.run(application_id,id,state) VALUES(durai_core.application_id(),%s,'QUEUED') ON CONFLICT DO NOTHING", (run_id,))
            db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('durai-task:' + task['digest'],))
            number = db.execute('SELECT count(*)+1 AS n FROM durai_tasks.attempt WHERE task_digest=%s', (task['digest'],)).fetchone()['n']
            attempt_id = task['taskId'] + ':' + task['digest'][:12] + ':' + str(number)
            if not self._reserve(db, budget_id, 'attempt:' + attempt_id, 'attempts', 1):
                raise Conflict('Attempt budget is exhausted')
            db.execute("INSERT INTO durai_tasks.attempt(application_id,id,task_digest,attempt,run_id,state,runtime,runtime_version,contract_digest,source_digest,budget_id) VALUES(durai_core.application_id(),%s,%s,%s,%s,'READY',%s,%s,%s,%s,%s)",
                       (attempt_id, task['digest'], number, run_id, task['runtime']['name'], task['runtime']['version'],
                        task['pins']['contractDigest'], task['pins']['sourceRevision'], budget_id))
            event(db, run_id, 'ATTEMPT_READY', {'attemptId': attempt_id, 'taskDigest': task['digest'], 'attempt': number})
        return self.get(attempt_id)

    def get(self, attempt_id):
        # Sample: Read attempt T-1 1. Return its pins, owner, generation, and state.
        with transaction(self.role, self.database) as db:
            row = db.execute('SELECT * FROM durai_tasks.attempt WHERE id=%s', (attempt_id,)).fetchone()
        if row is None:
            raise KeyError('Attempt is unavailable in this scope')
        return row

    def active_pins(self, runtime, version):
        # Sample: Pin attempt 1 to deepagents 0.7.38 in RUNNING. Return 1. Fail it. Return 0.
        with transaction(self.role, self.database) as db:
            return db.execute("SELECT count(*) AS n FROM durai_tasks.attempt WHERE runtime=%s AND runtime_version=%s AND state NOT IN ('SUCCEEDED','FAILED','CANCELLED')",
                              (runtime, version)).fetchone()['n']

    def claim(self, attempt_id, owner, seconds=30):
        # Sample: Claim READY attempt 1 as W-1. Return generation 1 and state RUNNING.
        if not 1 <= seconds <= 3600:
            raise ValueError('Lease duration is outside bounds')
        with transaction(self.role, self.database) as db:
            row = db.execute('SELECT *, lease_until < clock_timestamp() AS expired FROM durai_tasks.attempt WHERE id=%s FOR UPDATE', (attempt_id,)).fetchone()
            if row is None:
                raise KeyError('Attempt is unavailable in this scope')
            if row['state'] == 'RUNNING' and not row['expired']:
                return None
            if row['state'] == 'RUNNING':
                event(db, row['run_id'], 'ATTEMPT_LOST', {'attemptId': attempt_id, 'owner': row['owner'], 'generation': row['generation']})
                row['state'] = transition('RUNNING', 'LOST')
            transition(row['state'], 'RUNNING')
            claimed = db.execute("UPDATE durai_tasks.attempt SET state='RUNNING',owner=%s,generation=generation+1,lease_until=clock_timestamp()+%s*interval '1 second' WHERE id=%s RETURNING generation",
                                 (owner, seconds, attempt_id)).fetchone()
            event(db, row['run_id'], 'ATTEMPT_CLAIMED', {'attemptId': attempt_id, 'owner': owner, 'generation': claimed['generation']})
            return claimed['generation']

    def renew(self, attempt_id, owner, generation, seconds=30):
        # Sample: Renew W-1 generation 1 for 30 seconds. Refuse W-1 after W-2 holds generation 2.
        if not 1 <= seconds <= 3600:
            raise ValueError('Lease duration is outside bounds')
        with transaction(self.role, self.database) as db:
            row = db.execute("UPDATE durai_tasks.attempt SET lease_until=clock_timestamp()+%s*interval '1 second' WHERE id=%s AND owner=%s AND generation=%s AND state='RUNNING' AND lease_until>clock_timestamp() RETURNING id",
                             (seconds, attempt_id, owner, generation)).fetchone()
            if not row:
                raise Conflict('Stale or expired attempt lease')

    def move(self, attempt_id, owner, generation, state, evidence=None):
        # Sample: Move attempt 1 from RUNNING to WAITING_FOR_REVIEW as W-1 generation 1. Record one event.
        with transaction(self.role, self.database) as db:
            row = db.execute('SELECT * FROM durai_tasks.attempt WHERE id=%s AND owner=%s AND generation=%s AND lease_until>clock_timestamp() FOR UPDATE',
                             (attempt_id, owner, generation)).fetchone()
            if not row:
                raise Conflict('Stale or expired attempt owner cannot change state')
            transition(row['state'], state)
            db.execute('UPDATE durai_tasks.attempt SET state=%s WHERE id=%s', (state, attempt_id))
            event(db, row['run_id'], 'ATTEMPT_' + state, {'attemptId': attempt_id, 'generation': generation, 'evidence': evidence or {}})
        return state

    def review(self, attempt_id, decision, evidence=None):
        # Sample: Approve attempt 1 in WAITING_FOR_REVIEW. Keep the state and record one approval event. Reject attempt 2. Record FAILED.
        state = {'APPROVE': 'WAITING_FOR_REVIEW', 'REJECT': 'FAILED', 'CANCEL': 'CANCELLED'}.get(decision)
        if state is None:
            raise Conflict('Review decision must be APPROVE, REJECT, or CANCEL')
        with transaction(self.role, self.database) as db:
            row = db.execute('SELECT * FROM durai_tasks.attempt WHERE id=%s FOR UPDATE', (attempt_id,)).fetchone()
            if row is None:
                raise KeyError('Attempt is unavailable in this scope')
            if row['state'] != 'WAITING_FOR_REVIEW':
                raise Conflict('Only a candidate at review can receive a review decision')
            if decision == 'APPROVE':
                event(db, row['run_id'], 'ATTEMPT_REVIEW_APPROVED', {'attemptId': attempt_id, 'decision': decision,
                                                                     'releaseAuthority': False, 'evidence': evidence or {}})
                return state
            transition(row['state'], state, actor='review')
            db.execute('UPDATE durai_tasks.attempt SET state=%s WHERE id=%s', (state, attempt_id))
            event(db, row['run_id'], 'ATTEMPT_' + state, {'attemptId': attempt_id, 'decision': decision, 'evidence': evidence or {}})
        return state

    def cancel(self, attempt_id, evidence=None):
        # Sample: Cancel RUNNING attempt 1 as the operator. Record CANCELLED. Stop the next model call.
        with transaction(self.role, self.database) as db:
            row = db.execute('SELECT * FROM durai_tasks.attempt WHERE id=%s FOR UPDATE', (attempt_id,)).fetchone()
            if row is None:
                raise KeyError('Attempt is unavailable in this scope')
            transition(row['state'], 'CANCELLED', actor='operator')
            db.execute("UPDATE durai_tasks.attempt SET state='CANCELLED' WHERE id=%s", (attempt_id,))
            event(db, row['run_id'], 'ATTEMPT_CANCELLED', {'attemptId': attempt_id, 'evidence': evidence or {}})
        return 'CANCELLED'

    def reserve(self, budget_id, reservation_id, resource, amount):
        # Sample: Reserve 2 of 2 model calls. Return True. Reserve 1 more. Return False.
        with transaction(self.role, self.database) as db:
            return self._reserve(db, budget_id, reservation_id, resource, amount)

    def _reserve(self, db, budget_id, reservation_id, resource, amount):
        # Sample: Repeat reservation R-1 for 1 model call. Return True without a second entry.
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            raise ValueError('Reservation amount must be a positive integer')
        db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('durai-budget:' + budget_id,))
        budget = db.execute('SELECT limits FROM durai_tasks.budget WHERE id=%s', (budget_id,)).fetchone()
        if budget is None or resource not in budget['limits']:
            raise KeyError('Budget resource is unavailable')
        before = db.execute('SELECT resource,amount FROM durai_tasks.budget_entry WHERE budget_id=%s AND reservation_id=%s', (budget_id, reservation_id)).fetchone()
        if before:
            if (before['resource'], before['amount']) != (resource, amount):
                raise Conflict('Reservation ID has different input')
            return True
        used = db.execute("SELECT coalesce(sum(CASE WHEN state='RESERVED' THEN amount ELSE actual END),0) AS n FROM durai_tasks.budget_entry WHERE budget_id=%s AND resource=%s",
                          (budget_id, resource)).fetchone()['n']
        if used + amount > budget['limits'][resource]:
            return False
        db.execute("INSERT INTO durai_tasks.budget_entry(application_id,budget_id,reservation_id,resource,amount,state) VALUES(durai_core.application_id(),%s,%s,%s,%s,'RESERVED')",
                   (budget_id, reservation_id, resource, amount))
        return True

    def reconcile(self, budget_id, reservation_id, actual):
        # Sample: Reconcile R-1 with 1 actual call. Refuse a second reconciliation of R-1.
        with transaction(self.role, self.database) as db:
            row = db.execute("UPDATE durai_tasks.budget_entry SET state='RECONCILED',actual=%s,reconciled_at=clock_timestamp() WHERE budget_id=%s AND reservation_id=%s AND state='RESERVED' AND %s BETWEEN 0 AND amount RETURNING amount",
                             (actual, budget_id, reservation_id, actual)).fetchone()
            if not row:
                raise Conflict('Reservation is unknown, already reconciled, or over its amount')
            return {'reserved': row['amount'], 'actual': actual}

    def usage(self, budget_id):
        # Sample: Reserve R-1 for 2 model calls. Reconcile R-1 with 1 call. Return modelCalls 1.
        with transaction(self.role, self.database) as db:
            rows = db.execute("SELECT resource,sum(CASE WHEN state='RESERVED' THEN amount ELSE actual END) AS n FROM durai_tasks.budget_entry WHERE budget_id=%s GROUP BY resource",
                              (budget_id,)).fetchall()
        return {row['resource']: row['n'] for row in rows}
