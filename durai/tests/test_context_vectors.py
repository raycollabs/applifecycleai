"""Sample: Batch two plans with fixture vectors. Keep plan scope and exclude a stale status revision."""
import unittest
from durai.common import Conflict
from durai.context_search import BatchedVectorSearch, passages
from durai.context_store import ContextStore
from durai.db import ROOT, connect, prepare
from test_context_store import plan, status


class FixtureEmbeddings:
    model = 'fixture-concept-v1:1024'

    def embed(self, texts, mode):
        # Sample: Map seat and booking to coordinate 1. Map refund to coordinate 2. Make no external call.
        result = []
        for text in texts:
            lowered = text.lower()
            vector = [0.0] * 1024
            vector[0] = 1.0 if 'seat' in lowered or 'booking' in lowered else 0.01
            vector[1] = 1.0 if 'refund' in lowered else 0.01
            result.append(vector)
        return result


class ContextVectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Install the optional vector schema in the isolated test database. Make no provider call.
        prepare('durai_test')
        with connect('postgres', 'durai_test') as db:
            available = db.execute("SELECT 1 FROM pg_available_extensions WHERE name='vector'").fetchone()
            if not available:
                raise unittest.SkipTest('Optional pgvector extension is unavailable in this database image')
            exists = db.execute("SELECT to_regclass('durai_context.embedding') AS name").fetchone()['name']
            if not exists:
                db.execute((ROOT / 'migrations/context/context_vector_0001.sql').read_text(), prepare=False)

    def setUp(self):
        # Sample: Create one tenant-scoped plan. Select the fixture embedding adapter.
        self.store, self.plan, self.revision = plan()
        self.controller = ContextStore('durai_test', 'durai_evaluator')
        self.adapter = BatchedVectorSearch(FixtureEmbeddings())

    def test_batch_keeps_plan_scope(self):
        # Sample: Index plans A and B. Search A for booking. Check no B record.
        _, other, _ = plan()
        self.adapter.build(self.controller, self.plan)
        self.adapter.build(self.controller, other)
        result = self.store.search(self.plan, 'temporary booking', adapter=self.adapter)
        self.assertTrue(result['results'])
        self.assertTrue(all(r['digest'] == self.revision['digest'] for r in result['results']))
        foreign = ContextStore('durai_test', 'durai_foreign')
        with self.assertRaises(Conflict):
            foreign.search(self.plan, 'booking', adapter=self.adapter)

    def test_batch_excludes_new_status_revision(self):
        # Sample: Index status revision 1. Save revision 2. Check no old status hit.
        one = self.store.set_status(self.plan, status(self.revision), 0, 's1')
        self.adapter.build(self.controller, self.plan)
        self.store.set_status(self.plan, dict(status(self.revision), nextAction='Inspect refund results.'), 1, 's2')
        result = self.store.search(self.plan, 'booking', adapter=self.adapter)
        self.assertFalse(any(r['key'] == 'status' and r['digest'] == one['digest'] for r in result['results']))

    def test_stale_batch_publication_is_refused(self):
        # Sample: Prepare status revision 1 chunks. Save revision 2. Refuse the old batch publication.
        self.store.set_status(self.plan, status(self.revision), 0, 's1')
        records = self.store.read(self.plan)
        work = [(r, n, text) for r in records for n, text in enumerate(passages(r))]
        self.store.set_status(self.plan, dict(status(self.revision), phase='2'), 1, 's2')
        from durai.context_embeddings import vector_text
        vectors = [vector_text(v) for v in FixtureEmbeddings().embed([w[2] for w in work], 'passage')]
        with self.assertRaises(Conflict):
            self.adapter.publish(self.controller, self.plan, records, work, vectors)

    def test_batch_budget_is_checked_before_provider_call(self):
        # Sample: Give a large plan a one-call limit. Refuse before any embedding request.
        from unittest.mock import patch
        long_record = dict(self.revision, value=dict(self.revision['value'], text='seat ' * 10000))
        with patch.object(self.controller, 'read', return_value=[long_record]), patch.object(self.adapter.embeddings, 'embed', side_effect=AssertionError('Provider called')):
            with self.assertRaises(Conflict):
                self.adapter.build(self.controller, self.plan, max_calls=1)

    def test_provider_failure_is_explicit(self):
        # Sample: Fail the fixture provider. Keep the previous published batch unchanged.
        from unittest.mock import patch
        self.adapter.build(self.controller, self.plan)
        with patch.object(self.adapter.embeddings, 'embed', side_effect=Conflict('Provider unavailable')):
            with self.assertRaises(Conflict):
                self.adapter.build(self.controller, self.plan)
        self.assertTrue(self.store.search(self.plan, 'booking', adapter=self.adapter)['results'])

    def test_search_indexes_exist(self):
        # Sample: Inspect the derived vector index and record keyword index. Check HNSW and GIN.
        with connect('postgres', 'durai_test') as db:
            indexes = db.execute("SELECT indexdef FROM pg_indexes WHERE schemaname='durai_context'").fetchall()
        self.assertTrue(any('USING hnsw' in r['indexdef'] for r in indexes))
        self.assertTrue(any('USING gin' in r['indexdef'] for r in indexes))

    def test_missing_index_cannot_spend_provider_budget(self):
        # Sample: Request a batch with no vector schema. Check refusal and zero provider calls.
        from unittest.mock import MagicMock, patch
        cursor = MagicMock()
        cursor.execute.return_value.fetchone.return_value = {'ready': False}
        with patch('durai.context_search.transaction') as connection, patch.object(self.adapter.embeddings, 'embed', side_effect=AssertionError('Provider called')):
            connection.return_value.__enter__.return_value = cursor
            with self.assertRaises(Conflict):
                self.adapter.build(self.controller, self.plan)


if __name__ == '__main__':
    unittest.main()
