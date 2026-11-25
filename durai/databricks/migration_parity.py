from pathlib import Path
import os
import sys

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apps.shell.api.database import migrate

SHAPE = (
    ('columns', '''SELECT table_name, column_name, data_type, is_nullable, column_default, is_identity FROM information_schema.columns WHERE table_schema='shell' ORDER BY table_name, ordinal_position'''),
    ('constraints', '''SELECT c.relname, p.conname, pg_get_constraintdef(p.oid) AS definition FROM pg_constraint p JOIN pg_class c ON c.oid=p.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='shell' ORDER BY c.relname,p.conname'''),
    ('indexes', '''SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='shell' ORDER BY tablename,indexname'''),
)


def describe(database):
    # Sample: Open an empty PostgreSQL 17 scratch database. Apply migrations, read its shape, and roll back.
    with psycopg.connect(database, row_factory=dict_row, autocommit=True, connect_timeout=10) as conn:
        row = conn.execute("SELECT current_database() AS db, current_setting('server_version_num')::int/10000 AS major, "
                           "to_regnamespace('shell') IS NULL AS clean").fetchone()
        if not row['db'].startswith('durai_a3_parity') or row['major'] != 17 or not row['clean']:
            raise ValueError('Use an empty PostgreSQL 17 scratch database with prefix durai_a3_parity')
        with conn.transaction(force_rollback=True):
            migrate(conn)
            return {key: conn.execute(statement).fetchall() for key, statement in SHAPE}


def main():
    # Sample: Compare two scratch databases. Print a parity result without a connection string.
    local = describe(os.environ['DURAI_PARITY_LOCAL_DSN'])
    lakebase = describe(os.environ['DURAI_PARITY_LAKEBASE_DSN'])
    if local != lakebase:
        print('Migration parity failed')
        return 1
    print('Migration parity passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
