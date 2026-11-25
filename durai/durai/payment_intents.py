"""Sample: Send P-1 once. Lose its response. Read the original success by lookup."""
import re
from uuid import UUID, uuid4

from .common import Conflict
from .db import transaction
from .idempotency import execute_request
from .outbox import check_name, enqueue

COLUMNS = 'application_id, operation_id, order_id, amount_minor, currency, state, revision, provider_reference'
SCOPE = 'application_id=durai_core.application_id() AND operation_id=%s'
TERMINAL = ('SUCCEEDED', 'DECLINED')


def document(row):
    # Sample: Read the fixed order, amount, and operation data for P-1.
    return {'applicationId': row['application_id'], 'operationId': str(row['operation_id']),
            'orderId': row['order_id'], 'amountMinor': row['amount_minor'], 'currency': row['currency'],
            'state': row['state'], 'revision': row['revision'], 'providerReference': row['provider_reference']}


def read_row(db, operation_id, lock=False):
    # Sample: Read P-1 in the current tenant. Lock it when recording a result.
    if not isinstance(operation_id, str) or str(UUID(operation_id)) != operation_id:
        raise ValueError('A canonical operation UUID is required')
    row = db.execute('SELECT ' + COLUMNS + ' FROM durai_payments.intent WHERE ' + SCOPE
                     + (' FOR UPDATE' if lock else ''), (operation_id,)).fetchone()
    if row is None:
        raise KeyError('Payment intent unavailable in this tenant')
    return row


def record_event(db, row):
    # Sample: Write payment.succeeded at revision 3 on the P-1 stream.
    intent = document(row)
    event = enqueue(db, 'payment.' + intent['operationId'], 'payment.' + intent['state'].lower(), intent)
    if event['sequence'] != intent['revision']:
        raise Conflict('Payment event sequence differs from its revision')
    return event


def create_intent(order_id, amount_minor, currency, request_key, database='durai_test', role='durai_runtime'):
    # Sample: Create the intent for O-1. Retry the same terms and keep the same operation ID.
    check_name(order_id)
    if type(amount_minor) is not int or not 1 <= amount_minor <= 999999999:
        raise ValueError('Amount must be an integer from 1 to 999999999 minor units')
    if not isinstance(currency, str) or not re.fullmatch(r'[A-Z]{3}', currency):
        raise ValueError('Use a three-letter uppercase currency code')

    def create(db, _body):
        # Sample: Create a READY row for O-1. Refuse different terms for the same order.
        row = db.execute(
            'INSERT INTO durai_payments.intent (operation_id,order_id,amount_minor,currency) VALUES (%s,%s,%s,%s) '
            'ON CONFLICT (application_id,order_id) DO NOTHING RETURNING ' + COLUMNS,
            (uuid4(), order_id, amount_minor, currency),
        ).fetchone()
        if row is not None:
            return {'intent': document(row), 'event': record_event(db, row)}
        existing = db.execute('SELECT ' + COLUMNS + ' FROM durai_payments.intent '
                              'WHERE application_id=durai_core.application_id() AND order_id=%s',
                              (order_id,)).fetchone()
        if (existing['amount_minor'], existing['currency']) != (amount_minor, currency):
            raise Conflict('Order already has an intent with different terms')
        return {'intent': document(existing), 'event': None}

    body = {'orderId': order_id, 'amountMinor': amount_minor, 'currency': currency}
    return execute_request('payment.create', request_key, body, create, database, role)


def read_intent(operation_id, database='durai_test', role='durai_runtime'):
    # Sample: Read P-1 after the provider response is lost. Check UNCERTAIN.
    with transaction(role, database) as db:
        return document(read_row(db, operation_id))


def record_result(operation_id, proof, database='durai_test', role='durai_runtime'):
    # Sample: Match the amount and provider reference for P-1. Retain one terminal event.
    with transaction(role, database) as db:
        row = read_row(db, operation_id, lock=True)
        current = document(row)
        fields = ('applicationId', 'operationId', 'amountMinor', 'currency')
        if (not isinstance(proof, dict) or type(proof.get('amountMinor')) is not int
                or any(proof.get(field) != current[field] for field in fields)
                or proof.get('state') not in TERMINAL):
            raise Conflict('Provider result does not match the payment operation')
        reference = proof.get('providerReference')
        check_name(reference)
        if row['state'] in TERMINAL:
            if (row['state'], row['provider_reference']) != (proof['state'], reference):
                raise Conflict('Payment already has a different terminal result')
            return current
        if row['state'] != 'UNCERTAIN':
            raise Conflict('Payment operation has no durable charge claim')
        row = db.execute('UPDATE durai_payments.intent SET state=%s,provider_reference=%s WHERE ' + SCOPE
                         + ' RETURNING ' + COLUMNS, (proof['state'], reference, operation_id)).fetchone()
        record_event(db, row)
        return document(row)


def dispatch_charge(operation_id, provider, database='durai_test', role='durai_runtime'):
    # Sample: Commit the claim for P-1. Send it once after the database transaction ends.
    with transaction(role, database) as db:
        row = read_row(db, operation_id, lock=True)
        if row['state'] != 'READY':
            return document(row)
        row = db.execute("UPDATE durai_payments.intent SET state='UNCERTAIN' WHERE " + SCOPE
                         + ' RETURNING ' + COLUMNS, (operation_id,)).fetchone()
        record_event(db, row)
    try:
        proof = provider.charge(document(row))
    except (TimeoutError, ConnectionError):
        return read_intent(operation_id, database, role)
    if proof is None:
        return read_intent(operation_id, database, role)
    return record_result(operation_id, proof, database, role)


def reconcile(operation_id, provider, database='durai_test', role='durai_runtime'):
    # Sample: Look up P-1 with its original identity. Keep UNCERTAIN when no final result is available.
    current = read_intent(operation_id, database, role)
    if current['state'] != 'UNCERTAIN':
        return current
    proof = provider.lookup(dict(current))
    if proof is None:
        return read_intent(operation_id, database, role)
    return record_result(operation_id, proof, database, role)
