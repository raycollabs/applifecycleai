import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from durai.db import connect, use_server
from durai.db_sandbox import EphemeralDatabase, docker
from durai.fixture_batch import FixtureBatch
from durai.reservations import change_hold, hold_seat, read_order

DATABASE = 'durai_test'
ROLE = 'durai_foreign'
APP = 'reservation-pilot'
SCHEMA = 'seat-reservation/v1'
BATCH = FixtureBatch(APP, SCHEMA, {'seats': [
    {'label': 'A1', 'state': 'HELD', 'ttl': 3600},
    {'label': 'A2', 'state': 'CONFIRMED', 'ttl': 3600},
    {'label': 'A3', 'state': 'EXPIRED', 'ttl': 1},
]})


def load_fixture(target, batch=BATCH):
    # Sample: Load A1, A2, and A3 twice on a disposable server. Read the same three order IDs.
    if not isinstance(target, EphemeralDatabase) or not target.name.startswith('durai-eval-db-'):
        raise ValueError('Use a disposable reservation server')
    with use_server('127.0.0.1', target.port, target.settings_path), connect(ROLE, DATABASE) as reader:
        tenant = reader.execute('SELECT durai_core.application_id() AS id').fetchone()['id']
        if tenant != 'foreign':
            raise ValueError('Use the second test tenant')

        def apply(data, digest):
            created, orders = 0, []
            seats = data['seats']
            if any(not re.fullmatch(r'[A-Z][0-9]{1,4}', row['label'])
                   or row['state'] not in {'HELD', 'CONFIRMED', 'EXPIRED'}
                   or type(row['ttl']) is not int
                   or not (row['ttl'] == 1 if row['state'] == 'EXPIRED' else 30 <= row['ttl'] <= 86400)
                   for row in seats):
                raise ValueError('Invalid reservation fixture row')
            if len({row['label'] for row in seats}) != len(seats):
                raise ValueError('Duplicate reservation fixture seat')
            for row in seats:
                label = row['label']
                seat = f'fixture.{digest[:16]}:{label}'
                with connect('postgres', DATABASE) as admin:
                    admin.execute('INSERT INTO durai_reservations.seat VALUES (%s,%s) ON CONFLICT DO NOTHING',
                                  (tenant, seat))
                held = hold_seat(seat, row['ttl'], seat + ':hold', DATABASE, ROLE)
                created += int(not held['replayed'])
                initial = held['outcome']['order']
                state = row['state']
                if state == 'EXPIRED':
                    reader.execute("SELECT pg_sleep(GREATEST(0, EXTRACT(EPOCH FROM (%s::timestamptz - clock_timestamp()))))",
                                   (initial['expiresAt'],))
                    change_hold('expire', initial['orderId'], initial['revision'], seat + ':expire', DATABASE, ROLE)
                if state == 'CONFIRMED':
                    change_hold('confirm', initial['orderId'], initial['revision'], seat + ':confirm', DATABASE, ROLE)
                order = read_order(initial['orderId'], DATABASE, ROLE)
                if order['reservationState'] != state:
                    raise ValueError('Reservation state differs from the fixture')
                orders.append({'id': order['orderId'], 'state': state})
            return {'orders': orders, 'created_orders': created}

        return batch.load(APP, SCHEMA, apply)


def install(target):
    # Sample: Install the reservation blueprint in a fresh disposable database.
    with use_server('127.0.0.1', target.port, target.settings_path), connect('postgres', DATABASE) as db:
        for path in ('idempotency/v1.sql', 'events/v1.sql', 'reservations/v1.sql'):
            db.execute((ROOT / 'blueprints' / path).read_text(encoding='utf-8'), prepare=False)


def main():
    # Sample: Run the fixture on a new Docker database. Print the first load and replay.
    with TemporaryDirectory(prefix='durai-reservation-fixture-') as folder, EphemeralDatabase(Path(folder)) as target:
        docker('network', 'connect', 'bridge', target.name)
        target.wait()
        install(target)
        first, replay = load_fixture(target), load_fixture(target)
        if first['load']['orders'] != replay['load']['orders'] or replay['load']['created_orders'] != 0:
            raise ValueError('Fixture replay changed reservation orders')
        print(json.dumps({'first': first, 'replay': replay}, indent=2))


if __name__ == '__main__':
    main()
