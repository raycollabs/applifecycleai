"""Sample: Apply request K-1 twice. Return the retained outcome and one local business effect."""
import hashlib
import json
import re
from psycopg.types.json import Jsonb
from .common import Conflict
from .db import transaction

MAX_BYTES = 65536


def json_object(value):
    # Sample: Read 100 units. Refuse non-string keys, a non-finite amount, or an oversized document.
    def check(node):
        # Sample: Read a nested list of order lines. Check each object key and JSON value.
        if isinstance(node, dict):
            if any(not isinstance(k, str) or '\x00' in k for k in node):
                raise ValueError('JSON keys must be strings')
            for item in node.values():
                check(item)
        elif isinstance(node, list):
            for item in node:
                check(item)
        elif isinstance(node, str) and '\x00' in node:
            raise ValueError('JSON text contains a null character')
        elif node is not None and type(node) not in (str, int, float, bool):
            raise ValueError('Unsupported JSON value')
    if not isinstance(value, dict):
        raise ValueError('Request and outcome must be JSON objects')
    try:
        check(value)
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    except (TypeError, ValueError, RecursionError, UnicodeError):
        raise ValueError('Invalid JSON object') from None
    if len(encoded)>MAX_BYTES:
        raise ValueError('JSON object exceeds its size or character contract')
    return json.loads(encoded), encoded


def request_digest(encoded):
    # Sample: Encode the same request with sorted keys. Check the same SHA-256 digest.
    return hashlib.sha256(encoded).hexdigest()


def execute_request(operation, request_key, payload, handler, database='durai_test', role='durai_runtime'):
    # Sample: Claim K-1 for 100 units. Retry K-1. Return one retained outcome without a second handler call.
    if (not isinstance(operation, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}', operation) or
            len(operation.encode('utf-8'))>128 or not isinstance(request_key, str) or not request_key.strip() or
            len(request_key.encode('utf-8'))>256 or '\x00' in request_key or not callable(handler) or
            role not in ('durai_runtime', 'durai_foreign')):
        raise ValueError('Invalid command identity, handler, or runtime role')
    body, encoded = json_object(payload)
    fingerprint = request_digest(encoded)
    with transaction(role, database) as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
        row = db.execute('INSERT INTO durai_idempotency.request(operation,request_key,request_digest,request_body) '
            'VALUES(%s,%s,%s,%s) ON CONFLICT(application_id,operation,request_key) DO NOTHING RETURNING request_digest',
            (operation, request_key, fingerprint, Jsonb(body))).fetchone()
        replayed = row is None
        if replayed:
            saved = db.execute('SELECT request_digest,request_body,state,outcome FROM durai_idempotency.request '
                'WHERE application_id=durai_core.application_id() AND operation=%s AND request_key=%s',
                (operation, request_key)).fetchone()
            if not saved or saved['state']!='COMPLETED':
                raise Conflict('Request outcome is unavailable; retry the same identity')
            if saved['request_digest']!=fingerprint or saved['request_body']!=body:
                raise Conflict('Request identity was reused with a different payload')
            outcome = saved['outcome']
        else:
            outcome, _ = json_object(handler(db, body))
            updated = db.execute("UPDATE durai_idempotency.request SET state='COMPLETED',outcome=%s "
                'WHERE application_id=durai_core.application_id() AND operation=%s AND request_key=%s '
                "AND state='IN_PROGRESS' RETURNING request_digest", (Jsonb(outcome), operation, request_key)).fetchone()
            if not updated:
                raise Conflict('Request claim changed before completion')
    return {'format':'durai-idempotency-result/v1', 'operation':operation, 'requestKey':request_key,
        'requestDigest':fingerprint, 'replayed':replayed, 'outcome':outcome}
