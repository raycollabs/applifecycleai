"""Scoped sessions and append-only migration receipts."""
from contextlib import contextmanager
import hashlib
import json
import os
import re
import threading
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
OVERRIDE = threading.local()


def server():
    # Sample: Read no override and no environment. Return 127.0.0.1, port 54410, and private/local.json.
    value = getattr(OVERRIDE, 'server', None)
    if value:
        return value
    return {'host': os.environ.get('DURAI_DB_HOST', '127.0.0.1'), 'port': int(os.environ.get('DURAI_DB_PORT', '54410')),
            'settings': os.environ.get('DURAI_SETTINGS', str(ROOT / 'private/local.json'))}


@contextmanager
def use_server(host, port, settings_path):
    # Sample: Point this thread at 127.0.0.1 port 55001 with settings S-1. Restore the default after the block.
    previous = getattr(OVERRIDE, 'server', None)
    OVERRIDE.server = {'host': host, 'port': int(port), 'settings': str(settings_path)}
    try:
        yield OVERRIDE.server
    finally:
        OVERRIDE.server = previous


def settings():
    # Sample: Set DURAI_SETTINGS to /run/durai/settings.json. Return its passwords.
    return json.loads(Path(server()['settings']).read_text(encoding='utf-8'))


def connect(role='durai_runtime', database='durai_local', **kwargs):
    # Sample: Connect as postgres to durai_test on port 54410. Log in as supabase_admin.
    # The Supabase image reserves extension ownership for supabase_admin.
    login = 'supabase_admin' if role == 'postgres' else role
    target = server()
    return psycopg.connect(host=target['host'], port=target['port'], user=login,
                           password=settings()['passwords'][role], dbname=database,
                           row_factory=dict_row, connect_timeout=5, **kwargs)


@contextmanager
def transaction(role='durai_runtime', database='durai_local'):
    with connect(role, database) as connection:
        connection.execute("SET statement_timeout='10s'; SET lock_timeout='5s'")
        yield connection


def prepare(database='durai_local', skip=()):
    # Sample: Prepare durai_test with skip core_0010.sql. Apply core_0001.sql to core_0009.sql and core_0011.sql. Leave core_0010.sql unapplied.
    if database not in ('durai_local', 'durai_test') and not re.fullmatch(r'durai_restore_[a-f0-9]{12}', database):
        raise ValueError('Database is outside local Durai ownership')
    with connect('postgres', 'postgres', autocommit=True) as admin:
        for role, password in settings()['passwords'].items():
            if role == 'postgres':
                continue
            if not admin.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (role,)).fetchone():
                admin.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD {}')
                              .format(sql.Identifier(role), sql.Literal(password)))
        if not admin.execute('SELECT 1 FROM pg_database WHERE datname=%s', (database,)).fetchone():
            admin.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0 ENCODING \'UTF8\'').format(sql.Identifier(database)))
        admin.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(sql.Identifier(database)))
        for role in settings()['passwords']:
            if role != 'postgres':
                admin.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}')
                              .format(sql.Identifier(database), sql.Identifier(role)))
    migrations = []
    with connect('postgres', database) as admin:
        admin.execute('CREATE SCHEMA IF NOT EXISTS durai_core')
        admin.execute('CREATE TABLE IF NOT EXISTS durai_core.migration(name text PRIMARY KEY, digest text NOT NULL, applied_at timestamptz NOT NULL DEFAULT clock_timestamp())')
        admin.execute("SELECT pg_advisory_xact_lock(hashtext('durai-migration'))")
        for path in sorted((ROOT / 'migrations/retired').glob('*.sql')):
            before = admin.execute('SELECT digest FROM durai_core.migration WHERE name=%s', (path.name,)).fetchone()
            if before and before['digest'] != hashlib.sha256(path.read_bytes()).hexdigest():
                raise ValueError('Applied retired migration changed: ' + path.name)
        for path in sorted(item for item in (ROOT / 'migrations/core').glob('*.sql') if item.name not in skip):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            before = admin.execute('SELECT digest FROM durai_core.migration WHERE name=%s', (path.name,)).fetchone()
            if before and before['digest'] != digest:
                raise ValueError('Applied migration changed: ' + path.name)
            if not before:
                admin.execute(path.read_text(encoding='utf-8'), prepare=False)
                admin.execute('INSERT INTO durai_core.migration(name,digest) VALUES(%s,%s)', (path.name, digest))
            migrations.append({'name': path.name, 'sha256': digest})
    return migrations
