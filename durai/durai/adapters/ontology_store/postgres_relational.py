"""Sample: Make PostgresRelationalOntologyStore with a connect function. Call ensure_schema. Put 3 records. Ask the ancestors. Get the same answer as MemoryOntologyStore."""
import json

from ...common import digest
from ...ports.errors import Conflict, DigestMismatch, NotFound

SCHEMA = (
    'CREATE TABLE IF NOT EXISTS ontology_pack (pack_id text NOT NULL, version text NOT NULL, retired boolean NOT NULL DEFAULT false, PRIMARY KEY (pack_id, version))',
    'CREATE TABLE IF NOT EXISTS ontology_node (record_id text PRIMARY KEY, pack_id text NOT NULL, version text NOT NULL, body jsonb NOT NULL, digest text NOT NULL)',
    'CREATE TABLE IF NOT EXISTS ontology_edge (src text NOT NULL, kind text NOT NULL, dst text NOT NULL, PRIMARY KEY (src, kind, dst))',
    'CREATE TABLE IF NOT EXISTS ontology_key (key text PRIMARY KEY, result integer NOT NULL)',
)
VISIBLE = ("SELECT n.record_id FROM ontology_node n LEFT JOIN ontology_pack p ON p.pack_id = n.pack_id AND p.version = n.version "
           "WHERE COALESCE(p.retired, false) = false AND (%(view)s::text[] IS NULL OR n.pack_id || '@' || n.version = ANY(%(view)s::text[]))")


class PostgresRelationalOntologyStore:
    def __init__(self, connect):
        # Sample: Hold a function that returns a new psycopg connection. Open no connection yet.
        self.connect = connect

    def ensure_schema(self):
        # Sample: Create the four tables. Run it twice. Check no error.
        with self.connect() as conn:
            for statement in SCHEMA:
                conn.execute(statement)

    def put_records(self, pack_id, version, records, key):
        # Sample: Put R1 and R2 with key K-1. Return 2. Put them again with key K-1. Return 2 and write nothing. A changed body in the same pack raises Conflict.
        with self.connect() as conn:
            held = conn.execute('SELECT result FROM ontology_key WHERE key = %s', (key,)).fetchone()
            if held:
                return held[0]
            for record in records:
                if record['digest'] != digest(record['body']):
                    raise DigestMismatch(record['id'])
                row = conn.execute('SELECT pack_id, version, digest FROM ontology_node WHERE record_id = %s', (record['id'],)).fetchone()
                if row and (row[0], row[1]) == (pack_id, version) and row[2] != record['digest']:
                    raise Conflict(record['id'])
            conn.execute('INSERT INTO ontology_pack (pack_id, version) VALUES (%s, %s) ON CONFLICT DO NOTHING', (pack_id, version))
            for record in records:
                conn.execute('INSERT INTO ontology_node (record_id, pack_id, version, body, digest) VALUES (%s, %s, %s, %s, %s) '
                             'ON CONFLICT (record_id) DO UPDATE SET pack_id = EXCLUDED.pack_id, version = EXCLUDED.version, '
                             'body = EXCLUDED.body, digest = EXCLUDED.digest',
                             (record['id'], pack_id, version, json.dumps(record['body']), record['digest']))
                conn.execute('DELETE FROM ontology_edge WHERE src = %s', (record['id'],))
                for edge in record.get('edges', []):
                    conn.execute('INSERT INTO ontology_edge (src, kind, dst) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING',
                                 (record['id'], edge['kind'], edge['to']))
            conn.execute('INSERT INTO ontology_key (key, result) VALUES (%s, %s)', (key, len(records)))
            return len(records)

    def require_visible(self, conn, record_id, view):
        # Sample: Record R1 is visible in the view. Return nothing. Record R9 or a retired pack raises NotFound.
        found = conn.execute('WITH vis AS (' + VISIBLE + ') SELECT 1 FROM vis WHERE record_id = %(id)s', {'view': view, 'id': record_id}).fetchone()
        if not found:
            raise NotFound(record_id)

    def get_record(self, record_id, view=None):
        # Sample: Get R1. Return id, body, digest and edges. Get R9. Raise NotFound.
        with self.connect() as conn:
            self.require_visible(conn, record_id, view)
            row = conn.execute('SELECT body, digest FROM ontology_node WHERE record_id = %s', (record_id,)).fetchone()
            edges = conn.execute('SELECT kind, dst FROM ontology_edge WHERE src = %s ORDER BY kind, dst', (record_id,)).fetchall()
            body = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            return {'id': record_id, 'body': body, 'digest': row[1], 'edges': [{'kind': k, 'to': d} for k, d in edges]}

    def walk(self, record_id, view, depth, forward):
        # Sample: Edges R3 to R2 and R2 to R1. Walk forward from R3 to depth 10. Return R2 then R1.
        start, nxt = ('src', 'dst') if forward else ('dst', 'src')
        sql = ('WITH RECURSIVE vis AS (' + VISIBLE + '), walk(node, depth) AS ('
               'SELECT e.' + nxt + ', 1 FROM ontology_edge e WHERE e.' + start + ' = %(id)s AND e.src IN (SELECT record_id FROM vis) AND e.dst IN (SELECT record_id FROM vis) '
               'UNION '
               'SELECT e.' + nxt + ', w.depth + 1 FROM walk w JOIN ontology_edge e ON e.' + start + ' = w.node '
               'WHERE w.depth < %(depth)s AND e.src IN (SELECT record_id FROM vis) AND e.dst IN (SELECT record_id FROM vis)) '
               'SELECT node, MIN(depth) AS d FROM walk WHERE node <> %(id)s GROUP BY node ORDER BY d, node')
        with self.connect() as conn:
            self.require_visible(conn, record_id, view)
            return [row[0] for row in conn.execute(sql, {'view': view, 'id': record_id, 'depth': depth}).fetchall()]

    def ancestors(self, record_id, view=None, depth=10):
        # Sample: Edges R3 to R2 and R2 to R1. Ancestors of R3 return [R2, R1].
        return self.walk(record_id, view, depth, True)

    def descendants(self, record_id, view=None, depth=10):
        # Sample: Edges R3 to R2 and R2 to R1. Descendants of R1 return [R2, R3].
        return self.walk(record_id, view, depth, False)

    def neighbors(self, record_id, view=None, link_kind=None):
        # Sample: Edge R2 to R1 with kind PARENT. Neighbors of R1 with kind PARENT return [R2].
        sql = ('WITH vis AS (' + VISIBLE + ') SELECT DISTINCT other FROM ('
               'SELECT dst AS other, kind FROM ontology_edge WHERE src = %(id)s UNION ALL SELECT src AS other, kind FROM ontology_edge WHERE dst = %(id)s) x '
               'WHERE other IN (SELECT record_id FROM vis) AND (%(kind)s::text IS NULL OR kind = %(kind)s) ORDER BY other')
        with self.connect() as conn:
            self.require_visible(conn, record_id, view)
            return [row[0] for row in conn.execute(sql, {'view': view, 'id': record_id, 'kind': link_kind}).fetchall()]

    def retire_pack(self, pack_id, version, key):
        # Sample: Retire P-1 version 1 with key K-9. Retire it again with key K-9. Check one retired pack and one key row.
        with self.connect() as conn:
            if conn.execute('SELECT 1 FROM ontology_key WHERE key = %s', (key,)).fetchone():
                return
            conn.execute('INSERT INTO ontology_pack (pack_id, version, retired) VALUES (%s, %s, true) '
                         'ON CONFLICT (pack_id, version) DO UPDATE SET retired = true', (pack_id, version))
            conn.execute('INSERT INTO ontology_key (key, result) VALUES (%s, 0)', (key,))
