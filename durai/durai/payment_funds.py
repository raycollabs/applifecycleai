"""Sample: Capture 1000 minor units. Refund 600. Check 400 refundable."""
import re
from uuid import UUID, uuid4

from .common import Conflict
from .db import transaction
from .idempotency import execute_request
from .outbox import check_name, enqueue
from .payment_intents import read_row

TERMINAL = ('SUCCEEDED', 'DECLINED')
SCOPE = 'application_id=durai_core.application_id()'
BALANCES = 'captured, refund_reserved, refunded'
OP_FIELDS = 'operation_id, payment_id, kind, amount_minor, target_reference, state, revision, provider_reference'


def check_amount(value):
    # Sample: Accept 600 minor units. Refuse zero.
    if type(value) is not int or not 1 <= value <= 999999999:
        raise ValueError('Amount must be an integer from 1 to 999999999 minor units')


def insert_funds(db, intent, account, reference, captured):
    # Sample: Bind the provider account and reference to the payment once.
    payment_id = str(intent['operation_id'])
    inserted = db.execute(
        'INSERT INTO durai_payments.funds (payment_id,account,source_reference,capture_reference,captured) '
        'VALUES (%s,%s,%s,%s,%s) ON CONFLICT (application_id,payment_id) DO NOTHING RETURNING payment_id',
        (payment_id, account, reference, reference if captured else None, captured),
    ).fetchone()
    if inserted:
        enqueue(db, 'funds.' + payment_id, 'payment.funds_registered',
                {'paymentId': payment_id, 'currency': intent['currency'], 'capturedMinor': captured})
    row = read_funds(db, payment_id)
    if (row['account'], row['source_reference']) != (account, reference):
        raise Conflict('Payment already uses a different provider account or reference')
    return {'paymentId': payment_id, 'capturedMinor': row['captured']}


def register_authorization(order_id, amount_minor, currency, proof, request_key,
                           database='durai_test', role='durai_runtime'):
    # Sample: Match an authorization for O-1 and reserve its local order identity.
    check_name(order_id)
    check_amount(amount_minor)
    if not isinstance(currency, str) or not re.fullmatch(r'[A-Z]{3}', currency):
        raise ValueError('Use a three-letter uppercase currency code')
    if (not isinstance(proof, dict) or proof.get('orderId') != order_id
            or type(proof.get('amountMinor')) is not int or proof['amountMinor'] != amount_minor
            or proof.get('currency') != currency or proof.get('state') != 'AUTHORIZED'):
        raise Conflict('Authorization does not match the order')
    account, reference = proof.get('account'), proof.get('providerReference')
    check_name(account)
    check_name(reference)

    def register(db, _body):
        # Sample: Insert a manual intent for O-1. Refuse an existing direct charge identity.
        if proof.get('applicationId') != db.execute('SELECT durai_core.application_id() AS id').fetchone()['id']:
            raise Conflict('Authorization tenant differs')
        db.execute(
            "INSERT INTO durai_payments.intent (operation_id,order_id,amount_minor,currency,payment_mode) "
            "VALUES (%s,%s,%s,%s,'MANUAL_CAPTURE') ON CONFLICT (application_id,order_id) DO NOTHING",
            (uuid4(), order_id, amount_minor, currency),
        )
        intent = db.execute('SELECT operation_id,payment_mode,amount_minor,currency FROM durai_payments.intent WHERE '
                            + SCOPE + ' AND order_id=%s FOR UPDATE', (order_id,)).fetchone()
        if (intent['payment_mode'], intent['amount_minor'], intent['currency']) != ('MANUAL_CAPTURE', amount_minor, currency):
            raise Conflict('Order already has a payment with different terms or mode')
        return insert_funds(db, intent, account, reference, 0)

    body = {'orderId': order_id, 'amountMinor': amount_minor, 'currency': currency, 'account': account,
            'reference': reference, 'applicationId': proof.get('applicationId')}
    return execute_request('payment.register_authorization', request_key, body, register, database, role)


def register_direct_payment(payment_id, account, database='durai_test', role='durai_runtime'):
    # Sample: Use a confirmed direct charge as the source for refunds.
    check_name(account)
    with transaction(role, database) as db:
        intent = read_row(db, payment_id, lock=True)
        if intent['state'] != 'SUCCEEDED':
            raise Conflict('Refunds require a confirmed direct payment')
        return insert_funds(db, intent, account, intent['provider_reference'], intent['amount_minor'])


def read_funds(db, payment_id, lock=False):
    # Sample: Read the captured and reserved balances under the payment tenant.
    if not isinstance(payment_id, str) or str(UUID(payment_id)) != payment_id:
        raise ValueError('A canonical payment UUID is required')
    row = db.execute(
        'SELECT f.*, i.currency, i.amount_minor, i.payment_mode FROM durai_payments.funds f '
        'JOIN durai_payments.intent i ON (i.application_id,i.operation_id)=(f.application_id,f.payment_id) '
        'WHERE f.application_id=durai_core.application_id() AND f.payment_id=%s' + (' FOR UPDATE OF f' if lock else ''),
        (payment_id,),
    ).fetchone()
    if row is None:
        raise KeyError('Payment funds unavailable in this tenant')
    return row


def balances(payment_id, database='durai_test', role='durai_runtime'):
    # Sample: Read 1000 captured, 600 reserved, and 0 refunded.
    with transaction(role, database) as db:
        row = read_funds(db, payment_id)
        return {key: row[key] for key in BALANCES.split(', ')}


def operation_document(row, funds):
    # Sample: Read R-1 with its original provider account, target, and amount.
    return {'applicationId': funds['application_id'], 'operationId': str(row['operation_id']),
            'paymentId': str(row['payment_id']), 'account': funds['account'], 'currency': funds['currency'],
            'kind': row['kind'], 'amountMinor': row['amount_minor'], 'targetReference': row['target_reference'],
            'state': row['state'], 'revision': row['revision'], 'providerReference': row['provider_reference']}


def save_event(db, doc):
    # Sample: Write refund.succeeded as event 3 for R-1.
    event = enqueue(db, 'payment.op.' + doc['operationId'],
                    'payment.' + doc['kind'].lower() + '.' + doc['state'].lower(), doc)
    if event['sequence'] != doc['revision']:
        raise Conflict('Operation event sequence differs from its revision')
    return event


def create_operation(kind, payment_id, amount_minor, request_key, database='durai_test', role='durai_runtime'):
    # Sample: Reserve 600 of 1000 captured units for a refund.
    if kind not in ('CAPTURE', 'REFUND'):
        raise ValueError('Use CAPTURE or REFUND')
    check_amount(amount_minor)

    def create(db, _body):
        # Sample: Lock the payment balance. Reserve one amount and store one refund event.
        funds = read_funds(db, payment_id, lock=True)
        if kind == 'CAPTURE':
            if funds['payment_mode'] != 'MANUAL_CAPTURE' or amount_minor > funds['amount_minor']:
                raise Conflict('Capture requires an authorization for the requested amount')
            target = funds['source_reference']
        else:
            available = funds['captured'] - funds['refund_reserved'] - funds['refunded']
            if amount_minor > available:
                return {'result': 'INSUFFICIENT_REFUND_BALANCE'}
            target = funds['capture_reference']
            db.execute('UPDATE durai_payments.funds SET refund_reserved=refund_reserved+%s WHERE '
                       + SCOPE + ' AND payment_id=%s', (amount_minor, payment_id))
        row = db.execute(
            'INSERT INTO durai_payments.funds_operation (operation_id,payment_id,kind,amount_minor,target_reference) '
            "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (application_id,payment_id) WHERE kind='CAPTURE' "
            'DO NOTHING RETURNING ' + OP_FIELDS,
            (uuid4(), payment_id, kind, amount_minor, target),
        ).fetchone()
        if row is None:
            raise Conflict('Payment already has a capture operation')
        doc = operation_document(row, funds)
        return {'result': 'READY', 'operation': doc, 'event': save_event(db, doc)}

    body = {'paymentId': payment_id, 'amountMinor': amount_minor}
    return execute_request('payment.' + kind.lower() + '.create', request_key, body, create, database, role)


def read_operation(db, operation_id, lock=False):
    # Sample: Read R-1 in the current tenant. Lock it before a state change.
    if not isinstance(operation_id, str) or str(UUID(operation_id)) != operation_id:
        raise ValueError('A canonical operation UUID is required')
    row = db.execute('SELECT ' + OP_FIELDS + ' FROM durai_payments.funds_operation WHERE ' + SCOPE
                     + ' AND operation_id=%s' + (' FOR UPDATE' if lock else ''), (operation_id,)).fetchone()
    if row is None:
        raise KeyError('Payment operation unavailable in this tenant')
    return row


def read_result(operation_id, database='durai_test', role='durai_runtime'):
    # Sample: Read R-1 after a lost response and check UNCERTAIN.
    with transaction(role, database) as db:
        row = read_operation(db, operation_id)
        return operation_document(row, read_funds(db, str(row['payment_id'])))


def record_result(operation_id, proof, database='durai_test', role='durai_runtime'):
    # Sample: Match the proof for R-1. Move 600 reserved units to refunded once.
    with transaction(role, database) as db:
        row = read_operation(db, operation_id, lock=True)
        payment_id = str(row['payment_id'])
        funds = read_funds(db, payment_id, lock=True)
        current = operation_document(row, funds)
        keys = ('applicationId', 'operationId', 'kind', 'amountMinor', 'currency', 'account', 'targetReference')
        if (not isinstance(proof, dict) or type(proof.get('amountMinor')) is not int
                or any(proof.get(key) != current[key] for key in keys) or proof.get('state') not in TERMINAL):
            raise Conflict('Provider result does not match the payment operation')
        state, ref = proof['state'], proof.get('providerReference')
        check_name(ref)
        if row['state'] in TERMINAL:
            if (row['state'], row['provider_reference']) != (state, ref):
                raise Conflict('Payment operation has a different terminal result')
            return current
        if row['state'] != 'UNCERTAIN':
            raise Conflict('Payment operation has no durable provider claim')
        amount = row['amount_minor']
        if row['kind'] == 'REFUND':
            db.execute('UPDATE durai_payments.funds SET refund_reserved=refund_reserved-%s, refunded=refunded+%s '
                       'WHERE ' + SCOPE + ' AND payment_id=%s', (amount, amount if state == 'SUCCEEDED' else 0, payment_id))
        elif state == 'SUCCEEDED':
            db.execute('UPDATE durai_payments.funds SET captured=%s,capture_reference=%s WHERE '
                       + SCOPE + ' AND payment_id=%s', (amount, ref, payment_id))
        row = db.execute('UPDATE durai_payments.funds_operation SET state=%s,revision=revision+1,provider_reference=%s WHERE '
                         + SCOPE + ' AND operation_id=%s RETURNING ' + OP_FIELDS, (state, ref, operation_id)).fetchone()
        result = operation_document(row, funds)
        save_event(db, result)
        return result


def dispatch(operation_id, provider, database='durai_test', role='durai_runtime'):
    # Sample: Claim R-1 before sending it. A second worker reads its current state.
    with transaction(role, database) as db:
        row = read_operation(db, operation_id, lock=True)
        funds = read_funds(db, str(row['payment_id']))
        if row['state'] != 'READY':
            return operation_document(row, funds)
        row = db.execute("UPDATE durai_payments.funds_operation SET state='UNCERTAIN',revision=revision+1 WHERE "
                         + SCOPE + ' AND operation_id=%s RETURNING ' + OP_FIELDS, (operation_id,)).fetchone()
        doc = operation_document(row, funds)
        save_event(db, doc)
    try:
        proof = provider.submit(dict(doc))
    except (TimeoutError, ConnectionError):
        return read_result(operation_id, database, role)
    if proof is None:
        return read_result(operation_id, database, role)
    return record_result(operation_id, proof, database, role)


def reconcile(operation_id, provider, database='durai_test', role='durai_runtime'):
    # Sample: Look up R-1. Leave its reserved amount in place when the result is absent.
    current = read_result(operation_id, database, role)
    if current['state'] != 'UNCERTAIN':
        return current
    proof = provider.lookup(dict(current))
    if proof is None:
        return read_result(operation_id, database, role)
    return record_result(operation_id, proof, database, role)
