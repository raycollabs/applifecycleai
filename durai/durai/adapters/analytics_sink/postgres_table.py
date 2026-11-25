"""Sample: Make PostgresAnalyticsSink with a connect function. Append a row. Query it. Get the same answer as MemoryAnalyticsSink."""
import json

from ...ports.analytics_sink import TABLES
from ...ports.errors import NotFound, SchemaMismatch

SCHEMA = (
    'CREATE TABLE IF NOT EXISTS analytics_row (seq bigserial PRIMARY KEY, table_name text NOT NULL, row jsonb NOT NULL)',
    'CREATE TABLE IF NOT EXISTS analytics_key (key text PRIMARY KEY, result integer NOT NULL)',
)


class PostgresAnalyticsSink:
    def __init__(self, connect):
        # Sample: Hold a function that returns a new psycopg connection.
        self.connect = connect

    def ensure_schema(self):
        # Sample: Create both tables. Run it twice. Check no error.
        with self.connect() as conn:
            for statement in SCHEMA:
                conn.execute(statement)

    def append(self, table, rows, key):
        # Sample: Append 2 rows with key R-1. Return 2. Append again with key R-1. Return 2 and add nothing. A row without a required column adds nothing and raises SchemaMismatch.
        if table not in TABLES:
            raise SchemaMismatch('Unknown table ' + str(table))
        for row in rows:
            missing = [name for name in TABLES[table] if name not in row]
            if missing:
                raise SchemaMismatch('Missing ' + ', '.join(missing))
        with self.connect() as conn:
            held = conn.execute('SELECT result FROM analytics_key WHERE key = %s', (key,)).fetchone()
            if held:
                return held[0]
            for row in rows:
                conn.execute('INSERT INTO analytics_row (table_name, row) VALUES (%s, %s)', (table, json.dumps(row)))
            conn.execute('INSERT INTO analytics_key (key, result) VALUES (%s, %s)', (key, len(rows)))
            return len(rows)

    def query(self, table, filter=None, limit=100):
        # Sample: Query build_receipt with filter {"label": "L-1"}. Return only rows with label L-1, in append order, at most limit.
        if table not in TABLES:
            raise NotFound(table)
        with self.connect() as conn:
            found = conn.execute('SELECT row FROM analytics_row WHERE table_name = %s AND row @> %s::jsonb ORDER BY seq LIMIT %s',
                                 (table, json.dumps(filter or {}), limit)).fetchall()
            return [item[0] if isinstance(item[0], dict) else json.loads(item[0]) for item in found]
