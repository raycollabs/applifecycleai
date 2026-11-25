"""Sample: Test S-REV spec reviewer. Check proposal formatting, code leakage rejection, and human approval transitions."""
import json
import tempfile
import unittest
from pathlib import Path

from tools.spec_review import approve_spec, propose_spec, validate_criteria


class SpecReviewTests(unittest.TestCase):
    def setUp(self):
        # Sample: Create temporary folder for spec proposals.
        self.folder = tempfile.TemporaryDirectory()
        self.specs_dir = Path(self.folder.name)

    def tearDown(self):
        # Sample: Clean up temporary folder.
        self.folder.cleanup()

    def test_propose_spec_writes_valid_proposal(self):
        # Sample: Propose a spec with 2 criteria. Check format durai-spec/v1 and PROPOSED status.
        criteria = ['Trades book with unique ID', 'No trade is booked twice']
        spec, path = propose_spec('S-TRADE-BOOK', 2, 'Book trades once.', criteria, specs_folder=self.specs_dir)
        self.assertEqual('durai-spec/v1', spec['format'])
        self.assertEqual('PROPOSED', spec['status'])
        self.assertIsNone(spec['reviewer'])
        self.assertEqual(2, len(spec['criteria']))
        self.assertTrue(Path(path).is_file())

    def test_spec_rejects_code_leakage(self):
        # Sample: Propose a criterion with raw SQL select. Check ValueError.
        criteria = ['SELECT * FROM trades WHERE id = 1']
        with self.assertRaisesRegex(ValueError, 'implementation code leakage'):
            validate_criteria(criteria)

    def test_approve_spec_requires_human_reviewer(self):
        # Sample: Try approving spec with reviewer agent. Check refusal. Approve with user. Check APPROVED.
        criteria = ['Trades book once']
        propose_spec('S-TRADE-BOOK', 2, 'Book trades.', criteria, specs_folder=self.specs_dir)
        with self.assertRaisesRegex(ValueError, 'authorized human reviewer'):
            approve_spec('S-TRADE-BOOK', 2, reviewer='agent', specs_folder=self.specs_dir)
        approved = approve_spec('S-TRADE-BOOK', 2, reviewer='user', specs_folder=self.specs_dir)
        self.assertEqual('APPROVED', approved['status'])
        self.assertEqual('user', approved['reviewer'])


if __name__ == '__main__':
    unittest.main()
