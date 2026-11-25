"""Sample: Read the adopted policy catalog. Check its approved digest. Build the coding-worker view of generic pack G-4."""
import json
import unittest
from durai.common import digest
from durai.db import ROOT
from durai.packs import generic_pack
from durai.policy_catalog import extract_policies, supersession_edges, worker_view


class AdoptedPolicyCatalogTests(unittest.TestCase):
    def test_source_matches_approved_candidate(self):
        # Sample: Hash durai/policy_catalog.py. Check the module digest in the 7.1a review receipt.
        review = json.loads((ROOT / 'receipts/dev-loop-task-7-1a-review.json').read_text(encoding='utf-8'))
        self.assertEqual(digest((ROOT / 'durai/policy_catalog.py').read_bytes()), review['moduleDigest'])

    def test_generic_pack_v4_worker_view(self):
        # Sample: Build the coding-worker view of G-4. Check four rules, including do-not-write-explanatory-comments.
        view = worker_view('foundation-generic-v4', [('*', generic_pack('4'))], 'coding-worker')
        self.assertEqual([item['policyId'] for item in view['policies']],
                         ['clarification-before-invention', 'do-not-edit-unrequested', 'do-not-over-constrain',
                          'do-not-write-explanatory-comments'])
        self.assertEqual(len(extract_policies('*', generic_pack('4'))), 18)

    def test_generic_pack_v3_to_v4_has_no_changed_rule(self):
        # Sample: Compare G-3 and G-4. G-4 only adds one rule. Check no SUPERSEDES edge.
        self.assertEqual(supersession_edges('*', generic_pack('3'), generic_pack('4')), [])


if __name__ == '__main__':
    unittest.main()
