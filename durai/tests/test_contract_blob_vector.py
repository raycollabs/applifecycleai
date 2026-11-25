"""Sample: Run BlobContractTests and VectorContractTests. Check digest, path refusal, dimension refusal and ranking."""
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from durai.adapters.blob.local_folder import LocalFolderBlobStore  # noqa: E402
import pg_support  # noqa: E402
from durai.adapters.vector.memory import MemoryVectorStore  # noqa: E402
from durai.adapters.vector.pgvector import PgVectorStore  # noqa: E402
from durai.ports.errors import Conflict, DimensionMismatch, InvalidPath, NotFound  # noqa: E402


class BlobContract:
    def make_store(self):
        raise NotImplementedError

    def setUp(self):
        # Sample: Make an empty blob store.
        self.store = self.make_store()

    def test_digest_matches_bytes(self):
        # Sample: Put b"abc" at a/b.bin. Check the digest equals SHA-256 of b"abc" and get returns b"abc".
        digest = self.store.put('a/b.bin', b'abc', 'B-1')
        self.assertEqual(hashlib.sha256(b'abc').hexdigest(), digest)
        self.assertEqual(b'abc', self.store.get('a/b.bin'))

    def test_put_is_idempotent_on_key(self):
        # Sample: Put b"abc" with key B-1 twice. Check the same digest both times.
        self.assertEqual(self.store.put('a/b.bin', b'abc', 'B-1'), self.store.put('a/b.bin', b'abc', 'B-1'))

    def test_other_bytes_at_the_same_path_conflict(self):
        # Sample: Put b"abc" at a/b.bin with key B-1. Put b"xyz" at a/b.bin with key B-2. Check Conflict.
        self.store.put('a/b.bin', b'abc', 'B-1')
        with self.assertRaises(Conflict):
            self.store.put('a/b.bin', b'xyz', 'B-2')

    def test_path_outside_the_store_is_refused(self):
        # Sample: Put at ../x.bin and at /x.bin. Check InvalidPath each time.
        for bad in ('../x.bin', '/x.bin', 'C:/x.bin'):
            with self.assertRaises(InvalidPath):
                self.store.put(bad, b'x', 'B-9')

    def test_missing_blob_is_not_found(self):
        # Sample: Get x/none.bin and presign it. Check NotFound each time.
        with self.assertRaises(NotFound):
            self.store.get('x/none.bin')
        with self.assertRaises(NotFound):
            self.store.presign('x/none.bin', 60)

    def test_presign_names_the_expiry(self):
        # Sample: Put b"abc" at a/b.bin. Presign for 60 seconds. Check the link ends with expires=60.
        self.store.put('a/b.bin', b'abc', 'B-1')
        self.assertTrue(self.store.presign('a/b.bin', 60).endswith('expires=60'))


class LocalFolderBlobStoreTests(BlobContract, unittest.TestCase):
    def make_store(self):
        # Sample: Return a store on a new temporary folder.
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        return LocalFolderBlobStore(folder.name)


class VectorContract:
    def make_store(self):
        raise NotImplementedError

    def setUp(self):
        # Sample: Make a store. Upsert A (1,0), B (0,1), C (0.9,0.1) and D (1,0) in collection C-1.
        self.store = self.make_store()
        self.store.upsert('C-1', [('A', 'a', [1, 0]), ('B', 'b', [0, 1]), ('C', 'c', [0.9, 0.1]), ('D', 'd', [1, 0])], 'V-1')

    def test_same_top_k_for_same_query(self):
        # Sample: Search (1,0) with k 3. Check A and D tie on score and sort by id, then C.
        self.assertEqual(['A', 'D', 'C'], [item[0] for item in self.store.search('C-1', [1, 0], 3)])

    def test_dimension_mismatch_is_refused(self):
        # Sample: Upsert a vector of size 3 in a size 2 collection. Check DimensionMismatch. Search with size 3. Check DimensionMismatch.
        with self.assertRaises(DimensionMismatch):
            self.store.upsert('C-1', [('E', 'e', [1, 0, 0])], 'V-2')
        with self.assertRaises(DimensionMismatch):
            self.store.search('C-1', [1, 0, 0], 3)

    def test_upsert_is_idempotent_on_key(self):
        # Sample: Upsert E with key V-3 twice. Check both calls return 1 and E appears once in a search with k 10.
        item = [('E', 'e', [0.5, 0.5])]
        self.assertEqual((1, 1), (self.store.upsert('C-1', item, 'V-3'), self.store.upsert('C-1', item, 'V-3')))
        self.assertEqual(1, [i[0] for i in self.store.search('C-1', [1, 0], 10)].count('E'))

    def test_allowed_ids_limit_the_result(self):
        # Sample: Search (1,0) with allowed ids B and C. Check C then B.
        self.assertEqual(['C', 'B'], [item[0] for item in self.store.search('C-1', [1, 0], 3, ['B', 'C'])])

    def test_missing_collection_is_not_found(self):
        # Sample: Search collection X. Check NotFound.
        with self.assertRaises(NotFound):
            self.store.search('X', [1, 0], 3)

    def test_drop_removes_the_collection(self):
        # Sample: Drop C-1 with key V-9. Search it. Check NotFound.
        self.store.drop('C-1', 'V-9')
        with self.assertRaises(NotFound):
            self.store.search('C-1', [1, 0], 3)


class MemoryVectorStoreTests(VectorContract, unittest.TestCase):
    def make_store(self):
        # Sample: Return a new MemoryVectorStore.
        return MemoryVectorStore()


@unittest.skipUnless(pg_support.docker_ready() and pg_support.image_ready(pg_support.PGVECTOR_IMAGE), 'Docker or the pgvector image is not available')
class PgVectorStoreTests(VectorContract, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Start a disposable PostgreSQL 17 container with the vector extension once for this class.
        cls.connect = pg_support.start_postgres(pg_support.PGVECTOR_IMAGE)

    @classmethod
    def tearDownClass(cls):
        # Sample: Remove the container.
        pg_support.stop_postgres()

    def make_store(self):
        # Sample: Create the extension and tables. Empty them. Return a store on the container.
        store = PgVectorStore(type(self).connect)
        store.ensure_schema()
        with type(self).connect() as conn:
            conn.execute('TRUNCATE vector_item, vector_collection, vector_key')
        return store


if __name__ == '__main__':
    unittest.main()
