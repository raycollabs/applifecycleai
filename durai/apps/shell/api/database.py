import hashlib
import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

MIGRATIONS = Path(__file__).with_name('migrations')


def connect():
    # Sample: Connect to the configured scratch database. Return rows by column name.
    dsn = os.environ.get('DURAI_SHELL_DATABASE_URL') or os.environ.get('DURAI_BUILD_DATABASE_URL') or ''
    return psycopg.connect(dsn, connect_timeout=5, row_factory=dict_row)


def migrate(connection, directory=MIGRATIONS, namespace='shell'):
    # Sample: Apply migration 001 twice. Check one ledger row on the second run.
    with connection.transaction():
        connection.execute("SELECT pg_advisory_xact_lock(hashtext('durai_app_shell_migrations'))")
        connection.execute('CREATE SCHEMA IF NOT EXISTS shell')
        connection.execute('CREATE TABLE IF NOT EXISTS shell.migration (number text PRIMARY KEY, digest text NOT NULL)')
        for path in sorted(directory.glob('[0-9][0-9][0-9]_*.sql')):
            number = namespace + ':' + path.name
            body = path.read_bytes()
            checksum = hashlib.sha256(body).hexdigest()
            row = connection.execute('SELECT digest FROM shell.migration WHERE number=%s', (number,)).fetchone()
            if row:
                if row['digest'] != checksum:
                    raise ValueError('Applied shell migration changed')
                continue
            connection.execute(body.decode('utf-8'), prepare=False)
            connection.execute('INSERT INTO shell.migration VALUES(%s,%s)', (number, checksum))


def publish_outbox(connection, count=20):
    # Sample: Select two pending events. Notify their IDs and mark both published in one transaction.
    with connection.transaction():
        pending = connection.execute('SELECT event_id FROM shell.outbox WHERE published_at IS NULL '
                                     'ORDER BY event_id LIMIT %s FOR UPDATE SKIP LOCKED', (count,)).fetchall()
        for event in pending:
            connection.execute("SELECT pg_notify('shell_outbox', %s)", (str(event['event_id']),))
            connection.execute('UPDATE shell.outbox SET published_at=now() WHERE event_id=%s', (event['event_id'],))
        return len(pending)
