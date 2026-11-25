"""Sample: Hold 2 of 3 units. Confirm the hold. Check that 2 units become allocated."""
from uuid import UUID, uuid4

from .common import Conflict
from .db import transaction
from .idempotency import execute_request
from .outbox import check_name, enqueue

HOLD_COLUMNS = 'hold_id, item_id, quantity, state, revision, expires_at'
STATES = {'confirm': 'CONFIRMED', 'release': 'RELEASED', 'expire': 'EXPIRED'}


def hold_document(row):
    # Sample: Read H-1 with 2 units. Return its state, revision, and deadline.
    return {'holdId': str(row['hold_id']), 'itemId': row['item_id'], 'quantity': row['quantity'],
            'state': row['state'], 'revision': row['revision'], 'expiresAt': row['expires_at'].isoformat()}


def record_result(db, row):
    # Sample: Record inventory.confirmed with revision 2 on the H-1 stream.
    hold = hold_document(row)
    event = enqueue(db, 'inventory.' + hold['holdId'], 'inventory.' + hold['state'].lower(), hold)
    if event['sequence'] != hold['revision']:
        raise Conflict('Inventory event sequence differs from its hold revision')
    return {'result': hold['state'], 'hold': hold, 'event': event}


def hold_units(item_id, quantity, ttl_seconds, request_key, database='durai_test', role='durai_runtime'):
    # Sample: Hold 2 units with K-1. Repeat K-1 and read the same hold.
    check_name(item_id)
    if type(quantity) is not int or not 1 <= quantity <= 2147483647:
        raise ValueError('Quantity must be a positive integer within stock capacity limits')
    if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 86400:
        raise ValueError('Hold time must be 1 to 86400 seconds')

    def reserve(db, _body):
        # Sample: Start with 3 units. Reserve 2 units. Refuse a second request for 2 units.
        stock = db.execute("""
            UPDATE durai_inventory.stock SET available=available-%s, held=held+%s
            WHERE application_id=durai_core.application_id() AND item_id=%s AND available >= %s
            RETURNING item_id
        """, (quantity, quantity, item_id, quantity)).fetchone()
        if stock is None:
            exists = db.execute('SELECT item_id FROM durai_inventory.stock WHERE item_id=%s', (item_id,)).fetchone()
            if exists is None:
                raise KeyError('Item unavailable in this tenant')
            return {'result': 'INSUFFICIENT_STOCK', 'itemId': item_id, 'quantity': quantity}
        row = db.execute(
            'INSERT INTO durai_inventory.item_hold (hold_id,item_id,quantity,expires_at) '
            'VALUES (%s,%s,%s,clock_timestamp()+make_interval(secs => %s)) RETURNING ' + HOLD_COLUMNS,
            (uuid4(), item_id, quantity, ttl_seconds),
        ).fetchone()
        return record_result(db, row)

    body = {'itemId': item_id, 'quantity': quantity, 'ttlSeconds': ttl_seconds}
    return execute_request('inventory.hold', request_key, body, reserve, database, role)


def change_hold(action, hold_id, expected_revision, request_key, database='durai_test', role='durai_runtime'):
    # Sample: Release H-1 at revision 1. Check that a second terminal change is refused.
    if not isinstance(hold_id, str) or str(UUID(hold_id)) != hold_id:
        raise ValueError('A canonical hold UUID is required')
    if not isinstance(action, str) or action not in STATES:
        raise ValueError('Use confirm, release, or expire')
    if type(expected_revision) is not int or not 1 <= expected_revision < 2**31:
        raise ValueError('A positive hold revision is required')
    state = STATES[action]

    def change(db, _body):
        # Sample: Lock H-1. Check its deadline and move its 2 units once.
        locked = db.execute(
            'SELECT hold_id FROM durai_inventory.item_hold '
            'WHERE application_id=durai_core.application_id() AND hold_id=%s AND revision=%s '
            "AND state='HELD' FOR UPDATE", (hold_id, expected_revision),
        ).fetchone()
        if locked is None:
            raise Conflict('Hold is unavailable, stale, or terminal')
        row = db.execute(
            'UPDATE durai_inventory.item_hold SET state=%s,revision=revision+1 '
            'WHERE application_id=durai_core.application_id() AND hold_id=%s '
            'AND (expires_at<=clock_timestamp())=%s RETURNING ' + HOLD_COLUMNS,
            (state, hold_id, action == 'expire'),
        ).fetchone()
        if row is None:
            raise Conflict('Hold deadline does not permit this action')
        quantity = row['quantity']
        stock = db.execute("""
            UPDATE durai_inventory.stock SET available=available+%s, held=held-%s, allocated=allocated+%s
            WHERE application_id=durai_core.application_id() AND item_id=%s AND held >= %s
            RETURNING item_id
        """, (0 if action == 'confirm' else quantity, quantity, quantity if action == 'confirm' else 0,
              row['item_id'], quantity)).fetchone()
        if stock is None:
            raise Conflict('Stock does not match the held quantity')
        return record_result(db, row)

    body = {'holdId': hold_id, 'expectedRevision': expected_revision}
    return execute_request('inventory.' + action, request_key, body, change, database, role)


def read_stock(item_id, database='durai_test', role='durai_runtime'):
    # Sample: Read item I-1. Return capacity and its three quantity balances.
    check_name(item_id)
    with transaction(role, database) as db:
        row = db.execute('SELECT capacity, available, held, allocated FROM durai_inventory.stock '
                         'WHERE application_id=durai_core.application_id() AND item_id=%s', (item_id,)).fetchone()
    if row is None:
        raise KeyError('Item unavailable in this tenant')
    return row


def due_holds(database='durai_test', role='durai_runtime', *, limit=20):
    # Sample: Read at most 20 due holds in deadline order.
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Due hold limit must be 1 to 100')
    with transaction(role, database) as db:
        rows = db.execute('SELECT ' + HOLD_COLUMNS + ' FROM durai_inventory.item_hold '
                          'WHERE application_id=durai_core.application_id() '
                          "AND state='HELD' AND expires_at<=statement_timestamp() ORDER BY expires_at,hold_id LIMIT %s",
                          (limit,)).fetchall()
    return [hold_document(row) for row in rows]
