"""Sample: Claim a job lease for 30 seconds, commit idempotently with a generation token, and update coordination KV."""
from psycopg.types.json import Jsonb
from .common import Conflict, audit, digest, event
from .db import transaction


class Coordination:
    def __init__(self, database='durai_local'):
        self.database = database

    def enqueue(self, job_id):
        with transaction(database=self.database) as db:
            db.execute('INSERT INTO durai_coordination.job(application_id,id) VALUES(durai_core.application_id(),%s) ON CONFLICT DO NOTHING', (job_id,))

    def claim(self, job_id, owner, seconds=30):
        if not 1 <= seconds <= 3600:
            raise ValueError('Lease duration is outside bounds')
        with transaction(database=self.database) as db:
            row = db.execute("UPDATE durai_coordination.job SET state='RUNNING',owner=%s,generation=generation+1,lease_until=clock_timestamp()+%s*interval '1 second' WHERE id=%s AND (state='QUEUED' OR (state='RUNNING' AND lease_until<clock_timestamp())) RETURNING generation", (owner, seconds, job_id)).fetchone()
            return row['generation'] if row else None

    def renew(self, job_id, owner, generation, seconds=30):
        if not 1 <= seconds <= 3600:
            raise ValueError('Lease duration is outside bounds')
        with transaction(database=self.database) as db:
            row = db.execute("UPDATE durai_coordination.job SET lease_until=clock_timestamp()+%s*interval '1 second' WHERE id=%s AND owner=%s AND generation=%s AND state='RUNNING' AND lease_until>clock_timestamp() RETURNING id", (seconds, job_id, owner, generation)).fetchone()
            if not row:
                raise Conflict('Stale or expired lease')

    def commit(self, job_id, owner, generation, result, run_id=None):
        with transaction(database=self.database) as db:
            row = db.execute("UPDATE durai_coordination.job SET state='SUCCEEDED',result=%s WHERE id=%s AND owner=%s AND generation=%s AND state='RUNNING' AND lease_until>clock_timestamp() RETURNING id", (Jsonb(result), job_id, owner, generation)).fetchone()
            if not row:
                raise Conflict('Stale or expired lease cannot commit')
            if run_id:
                db.execute("UPDATE durai_agents.run SET checkpoint=%s WHERE id=%s", (Jsonb(result), run_id))
                event(db, run_id, 'WORK_COMMITTED', result)
            audit(db, 'FENCED_COMMIT', {'jobId': job_id, 'generation': generation})

    def run(self, run_id):
        with transaction(database=self.database) as db:
            db.execute("INSERT INTO durai_agents.run(application_id,id,state) VALUES(durai_core.application_id(),%s,'RUNNING') ON CONFLICT DO NOTHING", (run_id,))
            return db.execute('SELECT * FROM durai_agents.run WHERE id=%s', (run_id,)).fetchone()

    def checkpoint(self, run_id, value, state='RUNNING'):
        with transaction(database=self.database) as db:
            before = db.execute('SELECT checkpoint,state FROM durai_agents.run WHERE id=%s FOR UPDATE', (run_id,)).fetchone()
            if not before:
                raise KeyError('Run is unavailable')
            if before['checkpoint'] == value and before['state'] == state:
                return
            db.execute('UPDATE durai_agents.run SET checkpoint=%s,state=%s WHERE id=%s', (Jsonb(value), state, run_id))
            event(db, run_id, 'CHECKPOINT', {'state': state, 'checkpoint': value})

    def kv_put(self, namespace, key, value, expected_revision=0):
        with transaction(database=self.database) as db:
            if expected_revision == 0:
                row = db.execute('INSERT INTO durai_coordination.kv VALUES(durai_core.application_id(),%s,%s,1,%s) ON CONFLICT DO NOTHING RETURNING revision', (namespace, key, Jsonb(value))).fetchone()
            else:
                row = db.execute('UPDATE durai_coordination.kv SET revision=revision+1,value=%s WHERE namespace=%s AND key=%s AND revision=%s RETURNING revision', (Jsonb(value), namespace, key, expected_revision)).fetchone()
            if not row:
                raise Conflict('KV revision differs')
            return row['revision']

    def pool(self, name, capacity):
        with transaction(database=self.database) as db:
            db.execute('INSERT INTO durai_coordination.quota VALUES(durai_core.application_id(),%s,%s) ON CONFLICT DO NOTHING', (name, capacity))
            row = db.execute('SELECT capacity FROM durai_coordination.quota WHERE pool=%s', (name,)).fetchone()
            if row['capacity'] != capacity:
                raise Conflict('Capacity change needs a new pool definition')

    def reserve(self, pool, reservation_id, seconds=30):
        if not 1 <= seconds <= 3600:
            raise ValueError('Reservation duration is outside bounds')
        with transaction(database=self.database) as db:
            db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('trading:quota:' + pool,))
            row = db.execute('SELECT capacity FROM durai_coordination.quota WHERE pool=%s', (pool,)).fetchone()
            if not row:
                raise KeyError('Pool is unavailable')
            db.execute('DELETE FROM durai_coordination.reservation WHERE pool=%s AND expires_at<=clock_timestamp()', (pool,))
            existing = db.execute('SELECT 1 FROM durai_coordination.reservation WHERE pool=%s AND id=%s', (pool, reservation_id)).fetchone()
            if existing:
                return True
            count = db.execute('SELECT count(*) AS n FROM durai_coordination.reservation WHERE pool=%s', (pool,)).fetchone()['n']
            if count >= row['capacity']:
                return False
            db.execute("INSERT INTO durai_coordination.reservation VALUES(durai_core.application_id(),%s,%s,clock_timestamp()+%s*interval '1 second')", (pool, reservation_id, seconds))
            return True

    def book_once(self, key, trade_id, lots, lot_size, source_version):
        if not isinstance(lots, int) or isinstance(lots, bool) or lots == 0 or not isinstance(lot_size, int) or isinstance(lot_size, bool) or lot_size <= 0:
            raise ValueError('Lots and lot size must be nonzero/positive integers')
        value = {'tradeId': trade_id, 'lots': lots, 'lotSize': lot_size, 'sourceVersion': source_version}
        checksum = digest(value)
        with transaction(database=self.database) as db:
            # A keyed transaction lock serializes equal keys, including the first insertion.
            db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('trading:' + key,))
            before = db.execute('SELECT * FROM durai_coordination.operation WHERE key=%s', (key,)).fetchone()
            if before:
                if before['input_digest'] != checksum:
                    raise Conflict('Idempotency key has different input')
                return before['result']
            shares = lots * lot_size
            db.execute('INSERT INTO app_trading.trade VALUES(durai_core.application_id(),%s,%s,%s,%s,%s)', (trade_id, lots, lot_size, shares, source_version))
            result = dict(value, shares=shares)
            db.execute('INSERT INTO durai_coordination.operation VALUES(durai_core.application_id(),%s,%s,%s)', (key, checksum, Jsonb(result)))
            audit(db, 'BOOK_TRADE', result)
            return result
