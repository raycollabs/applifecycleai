"""Sample: Record events 1 to 4 with key sk-test-123456789. Replay after event 2. Return events 3 and 4 without the key."""
import re
from .common import event
from .db import settings, transaction

SECRET_NAME = re.compile(r'(api[_-]?key|token|secret|password|authorization|credential|review[_-]?key)', re.I)
SECRET_TEXT = re.compile(r'(?<![A-Za-z0-9])(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9._\-]+)')


def known_secrets():
    # Sample: Read the local runtime password P-1. Return it as one known secret.
    try:
        values = settings()
    except OSError:
        return set()
    found = set(values.get('passwords', {}).values())
    found.update(value for key, value in values.items() if isinstance(value, str))
    return {value for value in found if len(value) >= 8}


def redact(value, secrets=None):
    # Sample: Redact apiKey sk-test-123456789 and a known password. Keep shares 200.
    secrets = known_secrets() if secrets is None else secrets
    if isinstance(value, dict):
        return {key: '[REDACTED]' if SECRET_NAME.search(str(key)) else redact(child, secrets) for key, child in value.items()}
    if isinstance(value, list):
        return [redact(child, secrets) for child in value]
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, '[REDACTED]')
        return SECRET_TEXT.sub('[REDACTED]', value)
    return value


def record(db, run_id, kind, payload):
    # Sample: Record ATTEMPT_CLAIMED with token T-9. Store the event with token [REDACTED].
    event(db, run_id, kind, redact(payload))


def replay(run_id, after=0, database='durai_local', role='durai_runtime'):
    # Sample: Replay run R-1 after sequence 2. Return sequences 3 and 4 in order.
    with transaction(role, database) as db:
        rows = db.execute('SELECT sequence,kind,payload FROM durai_coordination.event WHERE run_id=%s AND sequence>%s ORDER BY sequence',
                          (run_id, after)).fetchall()
    return [dict(row) for row in rows]
