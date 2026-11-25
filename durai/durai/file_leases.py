"""Sample: Claim journal.py as member M-1. Refuse M-2. Transfer to M-2 with generation 2. Let it expire. Check LOST."""
import threading
import time
from dataclasses import dataclass
from .common import Conflict
from .db import transaction
from .execution_events import record

READ_ONLY_ROLES = ('reviewer',)
LIVE, LOST, NONE = 'LIVE', 'LOST', 'NONE'
MAX_SECONDS = 86400
COLUMNS = ("worktree,path,generation,workflow_id,step_id,owner,role,state,extract(epoch FROM expires_at)::float8 AS expires")


@dataclass(frozen=True)
class Lease:
    worktree: str
    path: str
    owner: str
    role: str
    generation: int
    expires_at: float
    workflow_id: str = None
    step_id: str = None


def lease_of(row):
    # Sample: Read row journal.py generation 2 held by M-2. Return a Lease with the same fields.
    return Lease(row['worktree'], row['path'], row['owner'], row['role'], row['generation'], row['expires'],
                 row['workflow_id'], row['step_id'])


class FileLeases:
    def __init__(self, database='durai_local', role='durai_runtime', clock=time.time):
        # Sample: Open the leases on durai_test as durai_runtime with the wall clock. Store no connection.
        self.database, self.role, self.clock = database, role, clock

    def seconds(self, value):
        # Sample: Read 600. Return 600. Read 0 or 90000. Refuse it.
        if type(value) is not int or not 1 <= value <= MAX_SECONDS:
            raise ValueError('Lease duration is outside bounds')
        return value

    def latest(self, db, worktree, path):
        # Sample: Read the highest generation of journal.py in worktree W-1. Return it, or None when never claimed.
        return db.execute('SELECT ' + COLUMNS + ' FROM durai_coordination.file_lease WHERE application_id=durai_core.application_id() '
                          'AND worktree=%s AND path=%s ORDER BY generation DESC LIMIT 1', (worktree, path)).fetchone()

    def lock(self, db, worktree, path):
        # Sample: Serialize two claims of journal.py in W-1. The second waits for the first commit.
        db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('file-lease:' + worktree + '\n' + path,))

    def note(self, db, kind, row, **extra):
        # Sample: Record LEASE_CLAIMED for M-1 and journal.py in the run of workflow W-1 with stepId S-1 and memberId M-1.
        if not row['workflow_id']:
            return
        db.execute("INSERT INTO durai_agents.run(application_id,id,state) VALUES(durai_core.application_id(),%s,'RUNNING') ON CONFLICT DO NOTHING",
                   (row['workflow_id'],))
        record(db, row['workflow_id'], kind, dict({'stepId': row['step_id'], 'memberId': row['owner'], 'role': row['role'], 'worktree': row['worktree'],
                                                    'path': row['path'], 'generation': row['generation']}, **extra))

    def lose(self, db, row):
        # Sample: Read an expired HELD lease of M-1 generation 1. Mark it LOST. Record LEASE_LOST once.
        changed = db.execute("UPDATE durai_coordination.file_lease SET state='LOST' WHERE application_id=durai_core.application_id() "
                             "AND worktree=%s AND path=%s AND generation=%s AND state='HELD' RETURNING 1",
                             (row['worktree'], row['path'], row['generation'])).fetchone()
        if changed:
            self.note(db, 'LEASE_LOST', row, expiresAt=row['expires'])

    def insert(self, db, row, owner, role, generation, expires, workflow_id, step_id):
        # Sample: Insert journal.py generation 2 for M-2 in state HELD. Return the Lease.
        db.execute("INSERT INTO durai_coordination.file_lease(application_id,worktree,path,generation,workflow_id,step_id,owner,role,state,expires_at) "
                   "VALUES(durai_core.application_id(),%s,%s,%s,%s,%s,%s,%s,'HELD',to_timestamp(%s))",
                   (row['worktree'], row['path'], generation, workflow_id, step_id, owner, role, expires))
        return Lease(row['worktree'], row['path'], owner, role, generation, expires, workflow_id, step_id)

    def claim(self, worktree, path, owner, role, seconds=600, workflow_id=None, step_id=None):
        # Sample: Claim journal.py of W-1 as M-1 for 600 seconds. Return generation 1. Claim as M-2. Raise Conflict. Claim again as M-1. Keep generation 1.
        seconds = self.seconds(seconds)
        if role in READ_ONLY_ROLES:
            raise Conflict('LEASE_REFUSED: role ' + role + ' has no write leases')
        now, problem, result = self.clock(), None, None
        base = {'worktree': worktree, 'path': path}
        with transaction(self.role, self.database) as db:
            self.lock(db, worktree, path)
            row = self.latest(db, worktree, path)
            if row and row['state'] == 'HELD' and row['expires'] > now:
                if row['owner'] != owner:
                    problem = Conflict('LEASE_CONFLICT: ' + path + ' is held by ' + row['owner'] + ' generation ' + str(row['generation']))
                else:
                    db.execute("UPDATE durai_coordination.file_lease SET expires_at=to_timestamp(%s) WHERE application_id=durai_core.application_id() "
                               "AND worktree=%s AND path=%s AND generation=%s AND state='HELD'", (now + seconds, worktree, path, row['generation']))
                    result = Lease(worktree, path, owner, row['role'], row['generation'], now + seconds, row['workflow_id'], row['step_id'])
            else:
                if row and row['state'] == 'HELD':
                    self.lose(db, row)
                result = self.insert(db, base, owner, role, (row['generation'] if row else 0) + 1, now + seconds, workflow_id, step_id)
                self.note(db, 'LEASE_CLAIMED', dict(base, owner=owner, role=role, generation=result.generation, workflow_id=workflow_id, step_id=step_id),
                          expiresAt=result.expires_at)
        if problem:
            raise problem
        return result

    def renew(self, lease, seconds=600):
        # Sample: Renew M-1 generation 1 for 600 seconds. Keep generation 1. Refuse a LOST, RELEASED, or replaced lease.
        seconds = self.seconds(seconds)
        now, problem, result = self.clock(), None, None
        with transaction(self.role, self.database) as db:
            self.lock(db, lease.worktree, lease.path)
            row = self.latest(db, lease.worktree, lease.path)
            if not row or row['generation'] != lease.generation or row['owner'] != lease.owner or row['state'] != 'HELD':
                problem = Conflict('LEASE_STALE: ' + lease.path + ' generation ' + str(lease.generation) + ' is no longer held by ' + lease.owner)
            elif row['expires'] <= now:
                self.lose(db, row)
                problem = Conflict('LEASE_LOST: ' + lease.path + ' generation ' + str(lease.generation) + ' expired; claim it again')
            else:
                db.execute("UPDATE durai_coordination.file_lease SET expires_at=to_timestamp(%s) WHERE application_id=durai_core.application_id() "
                           "AND worktree=%s AND path=%s AND generation=%s AND state='HELD'", (now + seconds, lease.worktree, lease.path, lease.generation))
                result = Lease(lease.worktree, lease.path, lease.owner, lease.role, lease.generation, now + seconds, lease.workflow_id, lease.step_id)
        if problem:
            raise problem
        return result

    def release(self, lease):
        # Sample: Release M-1 generation 1 of journal.py. Record LEASE_RELEASED. Refuse a second release.
        now, problem = self.clock(), None
        with transaction(self.role, self.database) as db:
            self.lock(db, lease.worktree, lease.path)
            row = self.latest(db, lease.worktree, lease.path)
            if not row or row['generation'] != lease.generation or row['owner'] != lease.owner or row['state'] != 'HELD':
                problem = Conflict('LEASE_STALE: ' + lease.path + ' generation ' + str(lease.generation) + ' is no longer held by ' + lease.owner)
            elif row['expires'] <= now:
                self.lose(db, row)
                problem = Conflict('LEASE_LOST: ' + lease.path + ' generation ' + str(lease.generation) + ' expired before release')
            else:
                db.execute("UPDATE durai_coordination.file_lease SET state='RELEASED' WHERE application_id=durai_core.application_id() "
                           "AND worktree=%s AND path=%s AND generation=%s AND state='HELD'", (lease.worktree, lease.path, lease.generation))
                self.note(db, 'LEASE_RELEASED', row)
        if problem:
            raise problem

    def transfer(self, lease, new_owner, new_role):
        # Sample: Transfer journal.py from test writer M-2 generation 1 to implementer M-1. Old RELEASED, new HELD generation 2, one LEASE_TRANSFERRED.
        if new_role in READ_ONLY_ROLES:
            raise Conflict('LEASE_REFUSED: role ' + new_role + ' has no write leases')
        now, problem, result = self.clock(), None, None
        with transaction(self.role, self.database) as db:
            self.lock(db, lease.worktree, lease.path)
            row = self.latest(db, lease.worktree, lease.path)
            if not row or row['generation'] != lease.generation or row['owner'] != lease.owner or row['state'] != 'HELD':
                problem = Conflict('LEASE_STALE: ' + lease.path + ' generation ' + str(lease.generation) + ' is no longer held by ' + lease.owner)
            elif row['expires'] <= now:
                self.lose(db, row)
                problem = Conflict('LEASE_LOST: ' + lease.path + ' generation ' + str(lease.generation) + ' expired before transfer')
            else:
                db.execute("UPDATE durai_coordination.file_lease SET state='RELEASED' WHERE application_id=durai_core.application_id() "
                           "AND worktree=%s AND path=%s AND generation=%s AND state='HELD'", (lease.worktree, lease.path, lease.generation))
                result = self.insert(db, row, new_owner, new_role, lease.generation + 1, row['expires'], row['workflow_id'], row['step_id'])
                self.note(db, 'LEASE_TRANSFERRED', dict(row, owner=new_owner, role=new_role, generation=result.generation),
                          fromMemberId=lease.owner, fromRole=lease.role, fromGeneration=lease.generation)
        if problem:
            raise problem
        return result

    def check(self, worktree, path, owner):
        # Sample: Check journal.py for M-1. Return LIVE with the lease. After expiry return LOST and record LEASE_LOST. For M-2 return NONE.
        now = self.clock()
        with transaction(self.role, self.database) as db:
            self.lock(db, worktree, path)
            row = self.latest(db, worktree, path)
            if row and row['state'] == 'HELD' and row['expires'] <= now:
                self.lose(db, row)
                row = dict(row, state='LOST')
            if row and row['owner'] == owner and row['state'] == 'HELD':
                return {'status': LIVE, 'lease': lease_of(row), 'heldBy': owner}
            if row and row['owner'] == owner and row['state'] == 'LOST':
                return {'status': LOST, 'lease': None, 'heldBy': None}
            held = row['owner'] if row and row['state'] == 'HELD' else None
            return {'status': NONE, 'lease': None, 'heldBy': held}

    def heartbeat(self, lease, every, stop, seconds=600, on_lost=None):
        # Sample: Renew M-1 generation 1 of journal.py every 1 second for 3 seconds. Stop when the stop flag is set. On a refused renew call on_lost with the error and end.
        def beat():
            # Sample: Wait 1 second. Renew. Repeat until the stop flag is set.
            while not stop.wait(every):
                try:
                    self.renew(lease, seconds)
                except Conflict as error:
                    if not stop.is_set() and on_lost is not None:
                        on_lost(lease, error)
                    return
                except Exception as error:
                    if not stop.is_set() and on_lost is not None:
                        on_lost(lease, Conflict('LEASE_LOST: heartbeat failed: ' + type(error).__name__))
                    return
        thread = threading.Thread(target=beat, daemon=True, name='lease-heartbeat-' + lease.owner)
        thread.start()
        return thread
