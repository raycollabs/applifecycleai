"""Sample: Run OntologyStoreContractTests on each adapter. Check idempotent writes, ancestor order, digest refusal and retired packs."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pg_support  # noqa: E402
from durai.adapters.ontology_store.memory import MemoryOntologyStore  # noqa: E402
from durai.adapters.ontology_store.postgres_age import AgeOntologyStore  # noqa: E402
from durai.adapters.ontology_store.postgres_relational import PostgresRelationalOntologyStore  # noqa: E402
from durai.common import digest  # noqa: E402
from durai.ports.errors import DigestMismatch, NotFound  # noqa: E402


def record(name, parent=None):
    # Sample: Make record R2 with parent R1. Return id R2, body with name R2, its digest and one PARENT edge to R1.
    body = {'name': name}
    return {'id': name, 'body': body, 'digest': digest(body), 'edges': [{'kind': 'PARENT', 'to': parent}] if parent else []}


class OntologyStoreContract:
    """Sample: Mix into a test case. Set self.store to an adapter in make_store. Run the goals."""

    def make_store(self):
        raise NotImplementedError

    def setUp(self):
        # Sample: Make a store. Put 6 nodes N1 to N6 with chain N6 to N5 to N3 to N1 and N4 to N3 in pack P-1 version 1.
        self.store = self.make_store()
        nodes = [record('N1'), record('N2', 'N1'), record('N3', 'N1'), record('N4', 'N3'), record('N5', 'N3'), record('N6', 'N5')]
        self.store.put_records('P-1', '1', nodes, 'K-1')

    def test_put_is_idempotent_on_key(self):
        # Sample: Put N7 with key K-2 twice. Check both calls return 1 and one record N7 exists.
        first = self.store.put_records('P-1', '1', [record('N7')], 'K-2')
        second = self.store.put_records('P-1', '1', [record('N7')], 'K-2')
        self.assertEqual((1, 1), (first, second))
        self.assertEqual('N7', self.store.get_record('N7')['id'])

    def test_ancestors_order_is_depth_then_id(self):
        # Sample: Ask the ancestors of N6. Check N5 then N3 then N1.
        self.assertEqual(['N5', 'N3', 'N1'], self.store.ancestors('N6'))

    def test_descendants_order_is_depth_then_id(self):
        # Sample: Ask the descendants of N1. Check N2 and N3 at depth 1, then N4 and N5, then N6.
        self.assertEqual(['N2', 'N3', 'N4', 'N5', 'N6'], self.store.descendants('N1'))

    def test_depth_limit_is_kept(self):
        # Sample: Ask the ancestors of N6 with depth 1. Check only N5.
        self.assertEqual(['N5'], self.store.ancestors('N6', depth=1))

    def test_neighbors_filter_by_kind(self):
        # Sample: Ask the neighbors of N3 with kind PARENT. Check N1, N4 and N5. Ask with kind OTHER. Check none.
        self.assertEqual(['N1', 'N4', 'N5'], self.store.neighbors('N3', link_kind='PARENT'))
        self.assertEqual([], self.store.neighbors('N3', link_kind='OTHER'))

    def test_digest_mismatch_is_refused(self):
        # Sample: Put a record whose digest differs from its body. Check DigestMismatch.
        bad = record('N8')
        bad['digest'] = '0' * 64
        with self.assertRaises(DigestMismatch):
            self.store.put_records('P-1', '1', [bad], 'K-3')

    def test_unknown_record_is_not_found(self):
        # Sample: Get N99. Check NotFound.
        with self.assertRaises(NotFound):
            self.store.get_record('N99')

    def test_retired_pack_is_hidden(self):
        # Sample: Retire P-1 version 1. Get N1. Check NotFound.
        self.store.retire_pack('P-1', '1', 'K-4')
        with self.assertRaises(NotFound):
            self.store.get_record('N1')

    def test_view_limits_what_is_visible(self):
        # Sample: Put M1 in pack P-2. Get M1 in view ["P-1@1"]. Check NotFound. Get it in view ["P-2@1"]. Check found.
        self.store.put_records('P-2', '1', [record('M1')], 'K-5')
        with self.assertRaises(NotFound):
            self.store.get_record('M1', view=['P-1@1'])
        self.assertEqual('M1', self.store.get_record('M1', view=['P-2@1'])['id'])

    def test_edge_to_a_later_record_becomes_visible(self):
        # Sample: Put Q2 with a PARENT edge to Q1 before Q1 exists. Get Q1. Check NotFound. Put Q1. Check the ancestors of Q2 are [Q1].
        self.store.put_records('P-1', '1', [record('Q2', 'Q1')], 'K-6')
        with self.assertRaises(NotFound):
            self.store.get_record('Q1')
        self.store.put_records('P-1', '1', [record('Q1')], 'K-7')
        self.assertEqual(['Q1'], self.store.ancestors('Q2'))


class MemoryOntologyStoreTests(OntologyStoreContract, unittest.TestCase):
    def make_store(self):
        # Sample: Return a new MemoryOntologyStore.
        return MemoryOntologyStore()


@unittest.skipUnless(pg_support.docker_ready(), 'Docker is not available')
class PostgresRelationalOntologyStoreTests(OntologyStoreContract, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Start a disposable PostgreSQL 17 container once for this class.
        cls.connect = pg_support.start_postgres()

    @classmethod
    def tearDownClass(cls):
        # Sample: Remove the container.
        pg_support.stop_postgres()

    def make_store(self):
        # Sample: Create the tables. Empty them. Return a store on the container.
        store = PostgresRelationalOntologyStore(type(self).connect)
        store.ensure_schema()
        with type(self).connect() as conn:
            conn.execute('TRUNCATE ontology_pack, ontology_node, ontology_edge, ontology_key')
        return store


AGE = pg_support.age_image() if pg_support.docker_ready() else None


@unittest.skipUnless(AGE, 'Docker or an Apache AGE image is not available')
class AgeOntologyStoreTests(OntologyStoreContract, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Start a disposable PostgreSQL 17 container with Apache AGE once for this class.
        cls.connect = pg_support.start_postgres(AGE[0], AGE[1])

    @classmethod
    def tearDownClass(cls):
        # Sample: Remove the container.
        pg_support.stop_postgres()

    def make_store(self):
        # Sample: Create the graph. Drop it and the helper rows. Create it again empty. Return a store on the container.
        store = AgeOntologyStore(type(self).connect, 'contract_graph')
        store.ensure_schema()
        with type(self).connect() as conn:
            conn.execute("LOAD 'age'")
            conn.execute('SET LOCAL search_path = public, ag_catalog, pg_catalog')
            conn.execute("SELECT ag_catalog.drop_graph('contract_graph', true)")
            conn.execute('TRUNCATE age_ontology_pack, age_ontology_key')
        store.ensure_schema()
        return store

    def test_graph_name_is_checked(self):
        # Sample: Make a store with graph name "x; DROP". Check ValueError. Name "ab" also raises ValueError.
        for bad in ('x; DROP', 'ab', '1graph'):
            with self.assertRaises(ValueError):
                AgeOntologyStore(type(self).connect, bad)


if __name__ == '__main__':
    unittest.main()
