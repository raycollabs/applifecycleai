"""Sample: Apply edit O-1 to a.py once. Replay O-1. Return the saved result without a second edit."""
from psycopg.types.json import Jsonb
from .common import Conflict, digest
from .db import transaction


def apply_once(operation_id, arguments, effect, database='durai_local', role='durai_runtime'):
    # Sample: Apply O-1 with input I-1. Save result R-1. Replay O-1 with I-1. Return R-1. Replay with I-2. Refuse.
    key = 'tool:' + operation_id
    checksum = digest(arguments)
    with transaction(role, database) as db:
        db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (key,))
        before = db.execute('SELECT input_digest,result FROM durai_coordination.operation WHERE key=%s', (key,)).fetchone()
        if before:
            if before['input_digest'] != checksum:
                raise Conflict('Operation ID has different input: ' + operation_id)
            return dict(before['result'], replayed=True)
        result = effect()
        db.execute('INSERT INTO durai_coordination.operation VALUES(durai_core.application_id(),%s,%s,%s)',
                   (key, checksum, Jsonb(result)))
        return dict(result, replayed=False)


def operations(prefix, database='durai_local', role='durai_runtime'):
    # Sample: List operations for attempt T-1 1. Return O-1 and O-2 in key order.
    with transaction(role, database) as db:
        return db.execute('SELECT key,input_digest,result FROM durai_coordination.operation WHERE key LIKE %s ORDER BY key',
                          ('tool:' + prefix + '%',)).fetchall()
