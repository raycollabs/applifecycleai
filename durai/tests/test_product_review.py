"""Sample: Test P-REV product reviewer with mock browser runner. Check verdicts, evidence, screenshot digests, and UI/UX scoring."""
import json
import tempfile
import unittest
from pathlib import Path

from tools.product_review import review_product


class ProductReviewTests(unittest.TestCase):
    def setUp(self):
        # Sample: Create temporary folders for evidence and receipts.
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.evidence = self.root / 'evidence'
        self.receipts = self.root / 'receipts'

    def tearDown(self):
        # Sample: Clean up temporary folders.
        self.folder.cleanup()

    def test_product_review_with_mock_browser_pass(self):
        # Sample: Mock browser returns HTML with Trade Booking. Check PASS verdict and UI score 4.
        def mock_browser(url, evidence_dir):
            screenshot = evidence_dir / 'screen.png'
            screenshot.write_bytes(b'PNG_IMAGE_BYTES')
            return {
                'status': 200,
                'html': '<html><body><h1>Trade Booking System</h1><p>Record and book trades once</p></body></html>',
                'screenshots': [{'path': str(screenshot), 'sha256': 'a' * 64}]
            }

        criteria = ['Trade Booking System interface', 'Record and book trades once']
        receipt = review_product('http://127.0.0.1:54360/', criteria, project='durai-app-test',
                                 evidence_folder=self.evidence, receipts_folder=self.receipts,
                                 browser_runner=mock_browser)
        self.assertEqual(200, receipt['httpStatus'])
        self.assertEqual(2, receipt['criteriaChecked'])
        self.assertEqual(['PASS', 'PASS'], [c['verdict'] for c in receipt['checks']])
        self.assertEqual(4, receipt['uiScore'])
        self.assertTrue((self.receipts / 'product-review-durai-app-test.json').is_file())

    def test_product_review_fails_when_terms_missing(self):
        # Sample: Mock browser returns empty page. Check FAIL verdict and UI score 1.
        def mock_browser(url, evidence_dir):
            return {'status': 200, 'html': '<html><body>Empty</body></html>', 'screenshots': []}

        criteria = ['Trade Booking System interface']
        receipt = review_product('http://127.0.0.1:54360/', criteria, project='durai-app-fail',
                                 evidence_folder=self.evidence, receipts_folder=self.receipts,
                                 browser_runner=mock_browser)
        self.assertEqual(1, receipt['uiScore'])
        self.assertEqual('FAIL', receipt['checks'][0]['verdict'])


if __name__ == '__main__':
    unittest.main()
