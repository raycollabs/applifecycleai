"""Sample: Batch plan P-1 after a session. Search its current records with semantic and keyword ranks."""
from .common import Conflict, canonical
from .context_embeddings import vector_text
from .db import transaction


def passages(record):
    # Sample: Split a 1500-character plan into overlapping chunks of at most 1000 characters.
    value = record['value']
    text = value['text'] if record['kind'] == 'plan' else canonical(value).decode()
    return [text[i:i + 1000] for i in range(0, len(text), 840)]


def require_index(store):
    # Sample: Request a batch before the vector schema exists. Refuse before any provider call.
    with transaction(store.role, store.database) as db:
        db.execute('SET TRANSACTION READ ONLY')
        ready = db.execute("SELECT to_regclass('durai_context.embedding') IS NOT NULL AND "
            "to_regclass('durai_context.embedding_batch') IS NOT NULL AS ready").fetchone()['ready']
    if not ready:
        raise Conflict('Install the qualified optional context vector schema before provider calls')


class BatchedVectorSearch:
    def __init__(self, embeddings):
        # Sample: Select model M-1 with 1024 dimensions. Make no provider call yet.
        self.embeddings = embeddings

    def build(self, store, plan_id, max_calls=10):
        # Sample: Batch 20 chunks in one call. Refuse a batch that needs more than its declared call limit.
        if store.role != 'durai_evaluator':
            raise PermissionError('Context indexing requires the controller')
        if type(max_calls) is not int or not 1 <= max_calls <= 100:
            raise ValueError('Batch call limit must be 1 to 100')
        require_index(store)
        records = store.read(plan_id)
        if not records:
            raise KeyError('Plan has no records in this scope')
        work = [(r, n, text) for r in records for n, text in enumerate(passages(r))]
        if (len(work) + 31) // 32 > max_calls:
            raise Conflict('Context embedding batch exceeds the call budget')
        vectors = []
        for i in range(0, len(work), 32):
            result = self.embeddings.embed([item[2] for item in work[i:i + 32]], 'passage')
            if len(result) != len(work[i:i + 32]):
                raise Conflict('Embedding response count differs')
            vectors.extend(vector_text(v) for v in result)
        return self.publish(store, plan_id, records, work, vectors)

    def publish(self, store, plan_id, records, work, vectors):
        # Sample: Index status revision 1 after revision 2 is saved. Refuse the stale batch.
        if store.role != 'durai_evaluator' or len(work) != len(vectors):
            raise Conflict('Context batch authority or vector count differs')
        through = max(r['sequence'] for r in records)
        with transaction(store.role, store.database) as db:
            current = db.execute('SELECT key,revision,digest FROM durai_context.record WHERE plan_id=%s ORDER BY key', (plan_id,)).fetchall()
            expected = {(r['key'], r['revision'], r['digest']) for r in records}
            if not expected <= {(r['key'], r['revision'], r['digest']) for r in current}:
                raise Conflict('Context changed during the embedding batch')
            saved = db.execute('INSERT INTO durai_context.embedding_batch(application_id,plan_id,model,through_sequence,chunks) '
                'VALUES(durai_core.application_id(),%s,%s,%s,%s) ON CONFLICT(application_id,plan_id,model) '
                'DO UPDATE SET through_sequence=EXCLUDED.through_sequence,chunks=EXCLUDED.chunks,created_at=clock_timestamp() '
                'WHERE durai_context.embedding_batch.through_sequence<=EXCLUDED.through_sequence RETURNING through_sequence',
                (plan_id, self.embeddings.model, through, len(work))).fetchone()
            if not saved:
                raise Conflict('A newer embedding batch is already published')
            db.execute('DELETE FROM durai_context.embedding WHERE plan_id=%s AND model=%s', (plan_id, self.embeddings.model))
            for (record, ordinal, text), vector in zip(work, vectors):
                db.execute('INSERT INTO durai_context.embedding VALUES(durai_core.application_id(),%s,%s,%s,%s,%s,%s,%s,%s::public.vector)',
                    (plan_id, record['key'], record['revision'], record['digest'], ordinal, self.embeddings.model, text, vector))
        return {'planId': plan_id, 'model': self.embeddings.model, 'throughSequence': through, 'chunks': len(work), 'modelCalls': (len(work) + 31) // 32}

    def search(self, store, plan_id, query, limit=10):
        # Sample: Search P-1 for temporary bookings. Return only current P-1 chunks and keyword matches.
        if not isinstance(query, str) or not query.strip() or len(query) > 2000 or type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('Search requires a short query and 1 to 50 results')
        require_index(store)
        with transaction(store.role, store.database) as db:
            db.execute('SET TRANSACTION READ ONLY')
            exists = db.execute('SELECT 1 FROM durai_context.embedding_batch WHERE plan_id=%s AND model=%s',
                (plan_id, self.embeddings.model)).fetchone()
        if not exists:
            raise Conflict('No semantic batch exists for this plan and model')
        vector = vector_text(self.embeddings.embed([query], 'query')[0])
        with transaction(store.role, store.database) as db:
            db.execute('SET TRANSACTION READ ONLY')
            version = db.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()['extversion']
            if tuple(int(n) for n in version.split('.')[:2]) >= (0, 8):
                db.execute("SET LOCAL hnsw.iterative_scan='strict_order'")
            rows = db.execute('SELECT e.record_key AS key,e.revision,e.digest,e.ordinal,e.text,e.vector <=> %s::public.vector AS distance '
                'FROM durai_context.embedding e JOIN durai_context.record r '
                'ON r.application_id=e.application_id AND r.plan_id=e.plan_id AND r.key=e.record_key AND r.revision=e.revision AND r.digest=e.digest '
                'WHERE e.plan_id=%s AND e.model=%s ORDER BY e.vector <=> %s::public.vector LIMIT %s',
                (vector, plan_id, self.embeddings.model, vector, max(40, limit * 8))).fetchall()
            batch = db.execute('SELECT through_sequence,chunks FROM durai_context.embedding_batch WHERE plan_id=%s AND model=%s',
                (plan_id, self.embeddings.model)).fetchone()
        if not batch:
            raise Conflict('No semantic batch exists for this plan and model')
        lexical = store.search(plan_id, query, min(50, limit * 4))['results']
        scores, hits = {}, {}
        for ranked in (rows, lexical):
            seen = set()
            for row in ranked:
                identity = (row['key'], row['revision'], row['digest'])
                if identity in seen:
                    continue
                seen.add(identity)
                scores[identity] = scores.get(identity, 0) + 1 / (60 + len(seen))
                hits.setdefault(identity, row)
        order = sorted(scores, key=lambda identity: (-scores[identity], identity))[:limit]
        return {'planId': plan_id, 'method': 'batch-hybrid-rrf/v1', 'dataOnly': True, 'model': self.embeddings.model,
                'coverage': batch, 'results': [dict(hits[k], score=scores[k]) for k in order]}
