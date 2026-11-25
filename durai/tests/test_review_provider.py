"""Sample: Map provider deepinfra-deepseek to reviewer deepinfra-deepseek-reviewer. Map provider deepseek to deepseek-flash-reviewer. Pass the provider to live_model."""
import unittest
from unittest import mock

from tools.build_review import REVIEWER_B, review_deepseek, reviewer_profile


class ReviewerProfileTests(unittest.TestCase):
    def test_direct_provider_keeps_existing_reviewer(self):
        # Sample: Provider deepseek. Read deepseek-flash-reviewer.
        self.assertEqual(reviewer_profile('deepseek'), REVIEWER_B)

    def test_deepinfra_provider_gets_own_reviewer(self):
        # Sample: Provider deepinfra-deepseek. Read deepinfra-deepseek-reviewer.
        self.assertEqual(reviewer_profile('deepinfra-deepseek'), 'deepinfra-deepseek-reviewer')

    def test_review_asks_live_model_for_the_given_provider(self):
        # Sample: Call review_deepseek with provider deepinfra-deepseek and no model. Patch live_model. Read one call with the provider and role verification.
        with mock.patch('durai.build_driver.live_model', side_effect=ValueError('stop here')) as live:
            with self.assertRaisesRegex(ValueError, 'stop here'):
                review_deepseek('A-1', '.', '.', provider='deepinfra-deepseek')
        live.assert_called_once_with('deepinfra-deepseek', 'verification')


if __name__ == '__main__':
    unittest.main()
