"""Sample: Run SampleStatusTests. Check a PROPOSED sample is refused by default and served when its status is allowed."""
import unittest
from pathlib import Path

from durai.build_samples import JsonSampleSource

FILE = Path(__file__).resolve().parents[1] / 'design/samples/trading-v1-r2-signin.json'


class SampleStatusTests(unittest.TestCase):
    def test_proposed_sample_is_refused_by_default(self):
        # Sample: Open the revision 2 file with the default statuses. Get S-TRADE-SCREENS-C1-5. Check KeyError.
        with self.assertRaises(KeyError):
            JsonSampleSource(FILE).get('S-TRADE-SCREENS-C1-5')

    def test_proposed_sample_is_served_when_allowed(self):
        # Sample: Open the revision 2 file with statuses PROPOSED. Get S-TRADE-SCREENS-C1-5. Check revision 2 and the words Sign out.
        sample = JsonSampleSource(FILE, statuses=('PROPOSED',)).get('S-TRADE-SCREENS-C1-5')
        self.assertEqual(2, sample['revision'])
        self.assertIn('Sign out', sample['requestText'])


if __name__ == '__main__':
    unittest.main()
