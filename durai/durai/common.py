import hashlib
import json
import uuid
from psycopg.types.json import Jsonb


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else canonical(value)).hexdigest()


def uid():
    return str(uuid.uuid4())


def audit(db, action, payload):
    db.execute('INSERT INTO durai_governance.audit(application_id,action,payload) VALUES(durai_core.application_id(),%s,%s)',
               (action, Jsonb(payload)))


def event(db, run_id, kind, payload):
    # The locked run is the single event-sequence owner. Consumers replay at least once.
    row = db.execute('UPDATE durai_agents.run SET revision=revision+1 WHERE application_id=durai_core.application_id() AND id=%s RETURNING revision', (run_id,)).fetchone()
    if not row:
        raise ValueError('Run is unavailable')
    db.execute('INSERT INTO durai_coordination.event VALUES(durai_core.application_id(),%s,%s,%s,%s,%s)',
               (run_id, row['revision'], uid(), kind, Jsonb(payload)))


class Conflict(ValueError):
    pass
