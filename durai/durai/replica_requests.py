"""Durable local replica ownership and terminal replay."""
from psycopg.types.json import Jsonb
from .common import Conflict, digest, uid
from .db import transaction

NAMESPACE = 'replica.phase1'


class ReplicaRequests:
    def __init__(self, database='durai_local'):
        # Sample: Keep replica ownership in the selected local database.
        self.database = database

    def claim(self, key, request):
        # Sample: Claim K-1 once. Return its terminal result on a later call.
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            raise ValueError('Request key must contain 1 to 128 characters')
        checksum, owner = digest(request), uid()
        with transaction(database=self.database) as db:
            db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (NAMESPACE + ':' + key,))
            row = db.execute("SELECT *, (value->>'deadline')::timestamptz<=clock_timestamp() AS expired FROM durai_coordination.kv WHERE namespace=%s AND key=%s FOR UPDATE", (NAMESPACE, key)).fetchone()
            if row:
                value = row['value']
                if value['requestDigest'] != checksum:
                    raise Conflict('Replica request key has different input')
                if value['state'] == 'RUNNING':
                    if not row['expired']:
                        raise Conflict('Replica request has an active owner')
                    value.update(state='LOST', result={'state': 'LOST', 'ownerId': value['owner'], 'runId': value.get('runId'), 'errorType': 'OwnerDeadlineExpired', 'approvalCreated': False})
                    db.execute('UPDATE durai_coordination.kv SET revision=revision+1,value=%s WHERE namespace=%s AND key=%s', (Jsonb(value), NAMESPACE, key))
                return None, value['result']
            deadline = db.execute("SELECT clock_timestamp()+interval '15 minutes' AS deadline").fetchone()['deadline'].isoformat()
            value = {'state': 'RUNNING', 'owner': owner, 'deadline': deadline, 'requestDigest': checksum}
            db.execute('INSERT INTO durai_coordination.kv VALUES(durai_core.application_id(),%s,%s,1,%s)', (NAMESPACE, key, Jsonb(value)))
        return owner, None

    def attach(self, key, owner, run_id):
        # Sample: Bind the engineering run to K-1 before the runner starts.
        with transaction(database=self.database) as db:
            row = db.execute("UPDATE durai_coordination.kv SET revision=revision+1,value=jsonb_set(value,'{runId}',%s) WHERE namespace=%s AND key=%s AND value->>'owner'=%s AND value->>'state'='RUNNING' AND (value->>'deadline')::timestamptz>clock_timestamp() RETURNING key", (Jsonb(run_id), NAMESPACE, key, owner)).fetchone()
            if not row:
                raise Conflict('Replica owner is stale or expired')

    def finish(self, key, owner, result):
        # Sample: Save one result for K-1. Reject an expired or replaced owner.
        if result.get('state') not in ('FAILED', 'WAITING_FOR_REVIEW'):
            raise Conflict('Replica result must be terminal')
        with transaction(database=self.database) as db:
            row = db.execute("SELECT *, (value->>'deadline')::timestamptz<=clock_timestamp() AS expired FROM durai_coordination.kv WHERE namespace=%s AND key=%s FOR UPDATE", (NAMESPACE, key)).fetchone()
            if not row or row['value']['state'] != 'RUNNING' or row['value']['owner'] != owner or row['expired']:
                raise Conflict('Replica owner is stale or expired')
            value = dict(row['value'], state=result['state'], result=result)
            db.execute('UPDATE durai_coordination.kv SET revision=revision+1,value=%s WHERE namespace=%s AND key=%s', (Jsonb(value), NAMESPACE, key))
        return result
