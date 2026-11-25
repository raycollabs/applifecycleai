"""Sample: Save E-1 with order O-1. Claim E-1 and confirm it after acknowledgment."""
from copy import deepcopy
import re
from uuid import uuid4

from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from .common import Conflict
from .db import transaction
from .idempotency import json_object

FORMAT = 'durai-event/v1'
ENVELOPE_COLUMNS = 'application_id, event_id, stream_id, sequence, event_type, payload'


def check_name(value, maximum=128):
    # Sample: Accept order.created. Refuse an empty event type.
    if not isinstance(value, str) or len(value) > maximum or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]*', value):
        raise ValueError('Invalid event name')


def envelope(row):
    # Sample: Read outbox E-1. Return only its delivery fields.
    return {
        'format': FORMAT, 'applicationId': row['application_id'], 'eventId': str(row['event_id']),
        'streamId': row['stream_id'], 'sequence': row['sequence'], 'eventType': row['event_type'],
        'payload': row['payload'],
    }


def enqueue(db, stream_id, event_type, payload, max_attempts=5):
    # Sample: Enqueue order.created in stream O-1 in the order transaction.
    if db.info.transaction_status != TransactionStatus.INTRANS:
        raise ValueError('Event publication requires an active transaction')
    check_name(stream_id)
    check_name(event_type)
    body, encoded = json_object(payload)
    if len(encoded) > 32768 or type(max_attempts) is not int or not 1 <= max_attempts <= 20:
        raise ValueError('Invalid event payload size or attempt limit')
    sequence = db.execute("""
        INSERT INTO durai_events.stream (stream_id) VALUES (%s)
        ON CONFLICT (application_id, stream_id) DO UPDATE
            SET last_sequence = durai_events.stream.last_sequence + 1
        RETURNING last_sequence
    """, (stream_id,)).fetchone()['last_sequence']
    row = db.execute(
        'INSERT INTO durai_events.outbox (event_id, stream_id, sequence, event_type, payload, max_attempts) '
        'VALUES (%s,%s,%s,%s,%s,%s) RETURNING ' + ENVELOPE_COLUMNS,
        (uuid4(), stream_id, sequence, event_type, Jsonb(body), max_attempts),
    ).fetchone()
    return envelope(row)


def claim_batch(database='durai_test', role='durai_runtime', *, limit=20, lease_seconds=30, stream_id=None):
    # Sample: Claim at most 20 due stream heads with leases that expire in 30 seconds.
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Claim limit must be 1 to 100')
    if type(lease_seconds) is not int or not 1 <= lease_seconds <= 300:
        raise ValueError('Lease must be 1 to 300 seconds')
    if stream_id is not None:
        check_name(stream_id)
    with transaction(role, database) as db:
        rows = db.execute("""
            WITH heads AS (
                SELECT candidate.application_id, candidate.event_id
                FROM durai_events.outbox AS candidate
                WHERE candidate.application_id = durai_core.application_id()
                  AND (%s::text IS NULL OR candidate.stream_id = %s)
                  AND ((candidate.state = 'PENDING' AND candidate.available_at <= statement_timestamp())
                    OR (candidate.state = 'CLAIMED' AND candidate.lease_until <= statement_timestamp()))
                  AND NOT EXISTS (
                      SELECT 1 FROM durai_events.outbox AS earlier
                      WHERE earlier.application_id = candidate.application_id
                        AND earlier.stream_id = candidate.stream_id AND earlier.sequence < candidate.sequence
                        AND earlier.state <> 'PUBLISHED'
                  )
                ORDER BY candidate.available_at, candidate.created_at, candidate.event_id
                LIMIT %s FOR UPDATE OF candidate SKIP LOCKED
            ), updated AS (
                UPDATE durai_events.outbox AS event
                SET state = CASE WHEN event.attempts = event.max_attempts THEN 'FAILED' ELSE 'CLAIMED' END,
                    attempts = LEAST(event.attempts + 1, event.max_attempts),
                    lease_token = CASE WHEN event.attempts < event.max_attempts THEN gen_random_uuid() END,
                    lease_until = CASE WHEN event.attempts < event.max_attempts
                                       THEN clock_timestamp() + make_interval(secs => %s) END
                FROM heads
                WHERE event.application_id = heads.application_id AND event.event_id = heads.event_id
                RETURNING event.*
            )
            SELECT * FROM updated WHERE state = 'CLAIMED'
            ORDER BY available_at, created_at, event_id
        """, (stream_id, stream_id, limit, lease_seconds)).fetchall()
    return [{'event': envelope(row), 'leaseToken': str(row['lease_token']), 'attempt': row['attempts']} for row in rows]


def settle(claim, acknowledged, database='durai_test', role='durai_runtime', *, retry_seconds=1):
    # Sample: Confirm E-1 with token T-2. Refuse the earlier token T-1.
    if type(acknowledged) is not bool or type(retry_seconds) is not int or not 0 <= retry_seconds <= 300:
        raise ValueError('Invalid acknowledgment or retry delay')
    event = claim['event']
    with transaction(role, database) as db:
        row = db.execute("""
            UPDATE durai_events.outbox
            SET state = CASE WHEN %s THEN 'PUBLISHED' WHEN attempts = max_attempts THEN 'FAILED' ELSE 'PENDING' END,
                published_at = CASE WHEN %s THEN clock_timestamp() END,
                available_at = CASE WHEN %s THEN available_at ELSE clock_timestamp() + make_interval(secs => %s) END,
                lease_token = NULL, lease_until = NULL
            WHERE application_id = durai_core.application_id() AND application_id = %s AND event_id = %s
              AND state = 'CLAIMED' AND lease_token = %s AND lease_until > clock_timestamp()
            RETURNING state
        """, (acknowledged, acknowledged, acknowledged, retry_seconds, event['applicationId'],
              event['eventId'], claim['leaseToken'])).fetchone()
        if row is None:
            raise Conflict('Event lease is no longer current')
    return row['state']


def relay_once(publish, database='durai_test', role='durai_runtime', *,
               retry_seconds=1, lease_seconds=30, stream_id=None):
    # Sample: Publish E-1 after the claim commits. Mark E-1 published after the adapter returns True.
    if not callable(publish) or type(retry_seconds) is not int or not 0 <= retry_seconds <= 300:
        raise ValueError('A publish adapter and a bounded retry delay are required')
    claims = claim_batch(database, role, limit=1, lease_seconds=lease_seconds, stream_id=stream_id)
    if not claims:
        return None
    claim = claims[0]
    acknowledged = publish(deepcopy(claim['event']))
    state = settle(claim, acknowledged, database, role, retry_seconds=retry_seconds)
    return {'eventId': claim['event']['eventId'], 'state': state}
