"""Sample: Make AgeOntologyStore with a connect function and graph name durai_ontology_port. Call ensure_schema. Put 3 records. Ask the ancestors. Get the same answer as MemoryOntologyStore."""
import json
import re

from psycopg import sql

from ...common import digest
from ...ports.errors import Conflict, DigestMismatch, NotFound

GRAPH_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{2,62}$')
SCHEMA = (
    'CREATE TABLE IF NOT EXISTS age_ontology_pack (pack_id text NOT NULL, version text NOT NULL, retired boolean NOT NULL DEFAULT false, PRIMARY KEY (pack_id, version))',
    'CREATE TABLE IF NOT EXISTS age_ontology_key (key text PRIMARY KEY, result integer NOT NULL)',
)
VISIBLE_PACKS = "SELECT pack_id || '@' || version FROM age_ontology_pack WHERE NOT retired"


class AgeOntologyStore:
    def __init__(self, connect, graph='durai_ontology_port', load_library=True):
        # Sample: Hold a connect function and the graph name. Graph name ab raises ValueError. Open no connection yet.
        if not GRAPH_NAME.match(graph):
            raise ValueError('Graph name needs 3 to 63 letters, digits or underscores: ' + graph)
        self.connect = connect
        self.graph = graph
        self.load_library = load_library

    def prepare(self, conn):
        # Sample: Open a transaction. Load the age library and set the search path to public, ag_catalog, pg_catalog for this transaction only. Helper tables then land in public.
        if self.load_library:
            conn.execute("LOAD 'age'")
        conn.execute('SET LOCAL search_path = public, ag_catalog, pg_catalog')

    def cypher(self, conn, query, params, columns):
        # Sample: Run MATCH (n:R {id: $id}) RETURN n.id with {"id": "N1"} and 1 column. Return [["N1"]]. A missing node returns [].
        names = ['c%d' % i for i in range(columns)]
        statement = (sql.SQL('SELECT ' + ', '.join(names) + ' FROM ag_catalog.cypher(')
                     + sql.Literal(self.graph)
                     + sql.SQL(', $q$' + query + '$q$, %s::ag_catalog.agtype) AS (' + ', '.join(n + ' ag_catalog.agtype' for n in names) + ')'))
        rows = conn.execute(statement, (json.dumps(params),)).fetchall()
        return [[None if cell is None else json.loads(cell) for cell in row] for row in rows]

    def ensure_schema(self):
        # Sample: Create the age extension, the graph with labels R and E, a property index per label and two helper tables. Run it twice. Check no error.
        with self.connect() as conn:
            conn.execute('CREATE EXTENSION IF NOT EXISTS age')
            self.prepare(conn)
            for statement in SCHEMA:
                conn.execute(statement)
            if not conn.execute('SELECT 1 FROM ag_catalog.ag_graph WHERE name = %s', (self.graph,)).fetchone():
                conn.execute('SELECT ag_catalog.create_graph(%s)', (self.graph,))
            for label, creator in (('R', 'create_vlabel'), ('E', 'create_elabel')):
                known = conn.execute('SELECT 1 FROM ag_catalog.ag_label l JOIN ag_catalog.ag_graph g ON g.graphid = l.graph WHERE g.name = %s AND l.name = %s',
                                     (self.graph, label)).fetchone()
                if not known:
                    conn.execute('SELECT ag_catalog.' + creator + '(%s, %s)', (self.graph, label))
                conn.execute(sql.SQL('CREATE INDEX IF NOT EXISTS {} ON {} USING gin (properties)').format(
                    sql.Identifier(self.graph + '_' + label.lower() + '_props'), sql.Identifier(self.graph, label)))

    def visible_packs(self, conn, view):
        # Sample: Packs P-1@1 and P-2@1 are live and P-3@1 is retired. View None returns the first two. View ["P-2@1"] returns only P-2@1.
        live = [row[0] for row in conn.execute(VISIBLE_PACKS).fetchall()]
        return live if view is None else [pack for pack in live if pack in view]

    def find_node(self, conn, record_id):
        # Sample: Find N1. Return its pack key, pack, version, digest and body text. Find N99. Return None.
        rows = self.cypher(conn, 'MATCH (n:R {id: $id}) RETURN n.pk, n.pack, n.version, n.digest, n.body', {'id': record_id}, 5)
        return rows[0] if rows else None

    def require_visible(self, conn, record_id, view):
        # Sample: Record N1 is in a live pack inside the view. Return its row. Record N99, a stub, a retired pack or a pack outside the view raises NotFound.
        row = self.find_node(conn, record_id)
        if not row or row[0] is None or row[0] not in self.visible_packs(conn, view):
            raise NotFound(record_id)
        return row

    def put_records(self, pack_id, version, records, key):
        # Sample: Put N1 and N2 with key K-1. Return 2. Put them again with key K-1. Return 2 and write nothing. A changed body in the same pack raises Conflict.
        pack_key = pack_id + '@' + version
        with self.connect() as conn:
            self.prepare(conn)
            held = conn.execute('SELECT result FROM age_ontology_key WHERE key = %s', (key,)).fetchone()
            if held:
                return held[0]
            for record in records:
                if record['digest'] != digest(record['body']):
                    raise DigestMismatch(record['id'])
                existing = self.find_node(conn, record['id'])
                if existing and (existing[1], existing[2]) == (pack_id, version) and existing[3] != record['digest']:
                    raise Conflict(record['id'])
            conn.execute('INSERT INTO age_ontology_pack (pack_id, version) VALUES (%s, %s) ON CONFLICT DO NOTHING', (pack_id, version))
            for record in records:
                self.cypher(conn, 'MERGE (n:R {id: $id}) SET n.pk = $pk, n.pack = $pack, n.version = $version, n.digest = $digest, n.body = $body RETURN n.id',
                            {'id': record['id'], 'pk': pack_key, 'pack': pack_id, 'version': version, 'digest': record['digest'],
                             'body': json.dumps(record['body'], sort_keys=True)}, 1)
            for record in records:
                self.cypher(conn, 'MATCH (a:R {id: $id})-[e:E]->() DELETE e RETURN 1', {'id': record['id']}, 1)
                for edge in record.get('edges', []):
                    self.cypher(conn, 'MERGE (b:R {id: $to}) RETURN b.id', {'to': edge['to']}, 1)
                    self.cypher(conn, 'MATCH (a:R {id: $id}), (b:R {id: $to}) MERGE (a)-[e:E {kind: $kind}]->(b) RETURN e.kind',
                                {'id': record['id'], 'to': edge['to'], 'kind': edge['kind']}, 1)
            conn.execute('INSERT INTO age_ontology_key (key, result) VALUES (%s, %s)', (key, len(records)))
            return len(records)

    def get_record(self, record_id, view=None):
        # Sample: Get N1. Return id, body, digest and edges. Get N99. Raise NotFound.
        with self.connect() as conn:
            self.prepare(conn)
            row = self.require_visible(conn, record_id, view)
            edges = self.cypher(conn, 'MATCH (a:R {id: $id})-[e:E]->(b:R) RETURN e.kind, b.id', {'id': record_id}, 2)
            return {'id': record_id, 'body': json.loads(row[4]), 'digest': row[3],
                    'edges': [{'kind': kind, 'to': to} for kind, to in sorted(edges)]}

    def walk(self, record_id, view, depth, forward):
        # Sample: Edges N3 to N2 and N2 to N1. Walk forward from N3 to depth 10. Return N2 then N1. A node met twice keeps its smallest depth.
        query = ('MATCH (a:R)-[:E]->(b:R) WHERE a.id IN $frontier AND a.pk IN $live AND b.pk IN $live RETURN b.id' if forward
                 else 'MATCH (b:R)-[:E]->(a:R) WHERE a.id IN $frontier AND a.pk IN $live AND b.pk IN $live RETURN b.id')
        with self.connect() as conn:
            self.prepare(conn)
            self.require_visible(conn, record_id, view)
            live = self.visible_packs(conn, view)
            seen, frontier, ordered = {record_id}, [record_id], []
            for _ in range(depth):
                level = sorted({row[0] for row in self.cypher(conn, query, {'frontier': frontier, 'live': live}, 1)} - seen)
                if not level:
                    break
                seen.update(level)
                ordered.extend(level)
                frontier = level
            return ordered

    def ancestors(self, record_id, view=None, depth=10):
        # Sample: Edges N3 to N2 and N2 to N1. Ancestors of N3 return [N2, N1].
        return self.walk(record_id, view, depth, True)

    def descendants(self, record_id, view=None, depth=10):
        # Sample: Edges N3 to N2 and N2 to N1. Descendants of N1 return [N2, N3].
        return self.walk(record_id, view, depth, False)

    def neighbors(self, record_id, view=None, link_kind=None):
        # Sample: Edge N2 to N1 with kind PARENT. Neighbors of N1 with kind PARENT return [N2]. Kind OTHER returns [].
        with self.connect() as conn:
            self.prepare(conn)
            self.require_visible(conn, record_id, view)
            live = self.visible_packs(conn, view)
            found = self.cypher(conn, 'MATCH (a:R {id: $id})-[e:E]->(b:R) WHERE b.pk IN $live RETURN e.kind, b.id', {'id': record_id, 'live': live}, 2)
            found += self.cypher(conn, 'MATCH (b:R)-[e:E]->(a:R {id: $id}) WHERE b.pk IN $live RETURN e.kind, b.id', {'id': record_id, 'live': live}, 2)
            return sorted({other for kind, other in found if link_kind is None or kind == link_kind})

    def retire_pack(self, pack_id, version, key):
        # Sample: Retire P-1 version 1 with key K-9. Retire it again with key K-9. Check one retired pack and one key row.
        with self.connect() as conn:
            self.prepare(conn)
            if conn.execute('SELECT 1 FROM age_ontology_key WHERE key = %s', (key,)).fetchone():
                return
            conn.execute('INSERT INTO age_ontology_pack (pack_id, version, retired) VALUES (%s, %s, true) '
                         'ON CONFLICT (pack_id, version) DO UPDATE SET retired = true', (pack_id, version))
            conn.execute('INSERT INTO age_ontology_key (key, result) VALUES (%s, 0)', (key,))
