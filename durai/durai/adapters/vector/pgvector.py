"""Sample: Make PgVectorStore with a connect function. Call ensure_schema. Upsert 4 vectors. Search with k 3. Get the same answer as MemoryVectorStore."""
from ...ports.errors import DimensionMismatch, NotFound

SCHEMA = (
    'CREATE EXTENSION IF NOT EXISTS vector',
    'CREATE TABLE IF NOT EXISTS vector_collection (name text PRIMARY KEY, size integer NOT NULL)',
    'CREATE TABLE IF NOT EXISTS vector_item (collection text NOT NULL, id text NOT NULL, text text NOT NULL, embedding vector NOT NULL, PRIMARY KEY (collection, id))',
    'CREATE TABLE IF NOT EXISTS vector_key (key text PRIMARY KEY, result integer NOT NULL)',
)


def literal(vector):
    # Sample: Vector (1, 0.5) returns the text [1.0,0.5].
    return '[' + ','.join(repr(float(x)) for x in vector) + ']'


class PgVectorStore:
    def __init__(self, connect):
        # Sample: Hold a function that returns a new psycopg connection.
        self.connect = connect

    def ensure_schema(self):
        # Sample: Create the extension and three tables. Run it twice. Check no error.
        with self.connect() as conn:
            for statement in SCHEMA:
                conn.execute(statement)

    def upsert(self, collection, items, key):
        # Sample: Upsert A (1,0) and B (0,1) with key V-1. Return 2. Again with key V-1. Return 2 and change nothing. A vector of size 3 in a size 2 collection raises DimensionMismatch.
        with self.connect() as conn:
            held = conn.execute('SELECT result FROM vector_key WHERE key = %s', (key,)).fetchone()
            if held:
                return held[0]
            row = conn.execute('SELECT size FROM vector_collection WHERE name = %s', (collection,)).fetchone()
            size = row[0] if row else None
            for _, _, vector in items:
                if size is None:
                    size = len(vector)
                if len(vector) != size:
                    raise DimensionMismatch('Expected %d, got %d' % (size, len(vector)))
            conn.execute('INSERT INTO vector_collection (name, size) VALUES (%s, %s) ON CONFLICT DO NOTHING', (collection, size))
            for item_id, text, vector in items:
                conn.execute('INSERT INTO vector_item (collection, id, text, embedding) VALUES (%s, %s, %s, %s::vector) '
                             'ON CONFLICT (collection, id) DO UPDATE SET text = EXCLUDED.text, embedding = EXCLUDED.embedding',
                             (collection, item_id, text, literal(vector)))
            conn.execute('INSERT INTO vector_key (key, result) VALUES (%s, %s)', (key, len(items)))
            return len(items)

    def search(self, collection, query_vector, k, allowed_ids=None):
        # Sample: Search (1,0) with k 3 over A (1,0), D (1,0), C (0.9,0.1) and B (0,1). Return A, D, C. A tie on score sorts by id.
        with self.connect() as conn:
            row = conn.execute('SELECT size FROM vector_collection WHERE name = %s', (collection,)).fetchone()
            if row is None:
                raise NotFound(collection)
            if len(query_vector) != row[0]:
                raise DimensionMismatch('Expected %d, got %d' % (row[0], len(query_vector)))
            found = conn.execute(
                'SELECT id, round((1 - (embedding <=> %(q)s::vector))::numeric, 6) AS score FROM vector_item '
                'WHERE collection = %(c)s AND (%(ids)s::text[] IS NULL OR id = ANY(%(ids)s::text[])) '
                'ORDER BY score DESC, id LIMIT %(k)s',
                {'q': literal(query_vector), 'c': collection, 'ids': list(allowed_ids) if allowed_ids is not None else None, 'k': k}).fetchall()
            return [(item_id, float(score)) for item_id, score in found]

    def drop(self, collection, key):
        # Sample: Drop the collection with key V-9. Search it. Raise NotFound. Drop again with key V-9. Change nothing.
        with self.connect() as conn:
            if conn.execute('SELECT 1 FROM vector_key WHERE key = %s', (key,)).fetchone():
                return
            conn.execute('DELETE FROM vector_item WHERE collection = %s', (collection,))
            conn.execute('DELETE FROM vector_collection WHERE name = %s', (collection,))
            conn.execute('INSERT INTO vector_key (key, result) VALUES (%s, 0)', (key,))
