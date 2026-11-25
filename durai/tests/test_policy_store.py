"""Sample: Read the adopted 7.1b and 7.1c sources. Check their approved digests. Record and project generic pack G-4 in durai_test."""
import json
import unittest
from durai.common import digest
from durai.db import ROOT, prepare
from durai.packs import PackStore, generic_pack
from durai.policy_catalog import extract_policies, policy_key, worker_view as catalog_view
from durai.policy_graph import graph_policies, project_policies
from durai.policy_store import record_policies, worker_view

DATABASE = 'durai_test'


def review(name):
    # Sample: Read receipts/dev-loop-task-7-1b-review.json. Return its module and migration digests.
    return json.loads((ROOT / 'receipts' / ('dev-loop-task-' + name + '-review.json')).read_text(encoding='utf-8'))


class AdoptedPolicyStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply core_0010 and core_0011 to durai_test. Install G-4. Bind it as adopted-generic-v4.
        prepare(DATABASE)
        cls.store = PackStore(DATABASE)
        cls.reference = cls.store.install(generic_pack('4'))
        cls.store.bind('adopted-generic-v4', [cls.reference], 'SYNTHETIC_LOCAL')

    def test_sources_match_approved_candidate(self):
        # Sample: Hash durai/policy_store.py and core_0010.sql. Check the digests in the 7.1b review receipt.
        receipt = review('7-1b')
        self.assertEqual(digest((ROOT / 'durai/policy_store.py').read_bytes()), receipt['moduleDigest'])
        self.assertEqual(digest((ROOT / 'migrations/core/core_0010.sql').read_bytes()), receipt['migrationDigest'])

    def test_worker_view_from_store(self):
        # Sample: Record G-4. Read the coding-worker view of adopted-generic-v4. Check four rules equal to the catalog view.
        self.assertEqual(len(record_policies(self.reference, DATABASE)), 18)
        view = worker_view('adopted-generic-v4', 'coding-worker', DATABASE)
        expected = catalog_view('adopted-generic-v4', [('*', generic_pack('4'))], 'coding-worker')
        self.assertEqual([item['policyId'] for item in view['policies']], [item['policyId'] for item in expected['policies']])
        self.assertEqual(len(view['policies']), 4)


class AdoptedPolicyGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply core_0011 to durai_test.
        prepare(DATABASE)

    def test_sources_match_approved_candidate(self):
        # Sample: Hash durai/policy_graph.py and core_0011.sql. Check the digests in the 7.1c review receipt.
        receipt = review('7-1c')
        self.assertEqual(digest((ROOT / 'durai/policy_graph.py').read_bytes()), receipt['moduleDigest'])
        self.assertEqual(digest((ROOT / 'migrations/core/core_0011.sql').read_bytes()), receipt['migrationDigest'])

    def test_project_generic_v4(self):
        # Sample: Project G-4 into durai_pack_graph twice. Read pack durai.software-generation version 4. Check 18 keys.
        project_policies('*', generic_pack('4'), DATABASE)
        keys = project_policies('*', generic_pack('4'), DATABASE)
        expected = sorted(policy_key(item) for item in extract_policies('*', generic_pack('4')))
        self.assertEqual(keys, expected)
        self.assertEqual([item['key'] for item in graph_policies('durai.software-generation', '4', DATABASE)], expected)


class AdoptedPolicyDeliveryTests(unittest.TestCase):
    def test_sources_match_approved_candidate(self):
        # Sample: Hash qualification.py and development_loop.py. Check both digests in the latest amendment, else in the adoption.
        receipt = json.loads((ROOT / 'receipts/policy-delivery-adoption.json').read_text(encoding='utf-8'))
        pins = (receipt.get('amendments') or [receipt])[-1]['fileDigests']
        for path, expected in pins.items():
            self.assertEqual(digest((ROOT / path.removeprefix('/workspace/')).read_bytes()), expected)


if __name__ == '__main__':
    unittest.main()
