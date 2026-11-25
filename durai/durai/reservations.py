"""Sample: Hold A-1 for order O-1. Confirm revision 1. Read the confirmed order at revision 2."""
from uuid import UUID, uuid4

from psycopg.errors import CheckViolation

from .common import Conflict
from .db import transaction
from .idempotency import execute_request
from .outbox import check_name, enqueue

COLUMNS = 'order_id, seat_id, state, order_status, revision, expires_at'
TRANSITIONS = {
    'confirm': ('CONFIRMED', 'order.confirmed'),
    'release': ('RELEASED', 'order.cancelled'),
    'expire': ('EXPIRED', 'order.expired'),
}


def order_document(row):
    # Sample: Read a held seat order. Return PENDING as its order status.
    return {
        'orderId': str(row['order_id']), 'seatId': row['seat_id'],
        'reservationState': row['state'], 'orderStatus': row['order_status'],
        'revision': row['revision'], 'expiresAt': row['expires_at'].isoformat(),
    }


def record_event(db, row, event_type):
    # Sample: Save event 2 for the confirmation of O-1 at revision 2.
    order = order_document(row)
    event = enqueue(db, 'order.' + order['orderId'], event_type, order)
    if event['sequence'] != order['revision']:
        raise Conflict('Order revision and event sequence differ')
    return {'order': order, 'event': event}


def order_identity(value):
    # Sample: Read a canonical order UUID. Refuse a different identifier form.
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError('A canonical order UUID is required')
    return value


def hold_seat(seat_id, ttl_seconds, request_key, database='durai_test', role='durai_runtime'):
    # Sample: Hold seat A-1 for 600 seconds with K-1. Retry K-1 and read the same order.
    check_name(seat_id)
    if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 86400:
        raise ValueError('Hold time must be 1 to 86400 seconds')

    def hold(db, body):
        # Sample: Insert one order for A-1. Return SEAT_UNAVAILABLE if another live order has the seat.
        seat = db.execute(
            'SELECT seat_id FROM durai_reservations.seat '
            'WHERE application_id=durai_core.application_id() AND seat_id=%s', (body['seatId'],),
        ).fetchone()
        if seat is None:
            raise KeyError('Seat unavailable in this tenant')
        row = db.execute(
            'INSERT INTO durai_reservations.seat_order (order_id,seat_id,ttl_seconds) VALUES (%s,%s,%s) '
            "ON CONFLICT (application_id,seat_id) WHERE state IN ('HELD','CONFIRMED') DO NOTHING "
            'RETURNING ' + COLUMNS,
            (uuid4(), body['seatId'], body['ttlSeconds']),
        ).fetchone()
        if row is None:
            return {'result': 'SEAT_UNAVAILABLE', 'seatId': body['seatId']}
        return {'result': 'HELD', **record_event(db, row, 'order.held')}

    return execute_request('reservation.hold', request_key, {'seatId': seat_id, 'ttlSeconds': ttl_seconds},
                           hold, database, role)


def change_hold(action, order_id, expected_revision, request_key, database='durai_test', role='durai_runtime'):
    # Sample: Confirm O-1 at revision 1. Check a second transition at revision 1 is refused.
    order_identity(order_id)
    if not isinstance(action, str) or action not in TRANSITIONS:
        raise ValueError('Use confirm, release, or expire')
    if type(expected_revision) is not int or not 1 <= expected_revision < 2**31:
        raise ValueError('A positive order revision is required')
    state, event_type = TRANSITIONS[action]

    def change(db, body):
        # Sample: Change a held order at its expected revision. Save the next order event.
        row = db.execute(
            'UPDATE durai_reservations.seat_order SET state=%s '
            'WHERE application_id=durai_core.application_id() AND order_id=%s '
            "AND revision=%s AND state='HELD' RETURNING " + COLUMNS,
            (state, body['orderId'], body['expectedRevision']),
        ).fetchone()
        if row is None:
            raise Conflict('Order is unavailable, stale, or terminal')
        return {'result': state, **record_event(db, row, event_type)}

    try:
        return execute_request('reservation.' + action, request_key,
                               {'orderId': order_id, 'expectedRevision': expected_revision}, change, database, role)
    except CheckViolation as exc:
        if exc.diag.constraint_name == 'seat_order_deadline':
            raise Conflict('Hold deadline does not permit this action') from None
        raise


def read_order(order_id, database='durai_test', role='durai_runtime'):
    # Sample: Read O-1 through the order tenant login. Refuse a different tenant login.
    with transaction(role, database) as db:
        row = db.execute(
            'SELECT ' + COLUMNS + ' FROM durai_reservations.seat_order '
            'WHERE application_id=durai_core.application_id() AND order_id=%s', (order_identity(order_id),),
        ).fetchone()
    if row is None:
        raise KeyError('Order unavailable in this tenant')
    return order_document(row)


def due_holds(database='durai_test', role='durai_runtime', *, limit=20):
    # Sample: Read the two earliest due holds. Check their order IDs and revisions.
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Due hold limit must be 1 to 100')
    with transaction(role, database) as db:
        rows = db.execute(
            'SELECT ' + COLUMNS + ' FROM durai_reservations.seat_order '
            'WHERE application_id=durai_core.application_id() '
            "AND state='HELD' AND expires_at<=statement_timestamp() ORDER BY expires_at,order_id LIMIT %s",
            (limit,),
        ).fetchall()
    return [order_document(row) for row in rows]
