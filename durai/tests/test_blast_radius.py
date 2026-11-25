"""Sample: Test blast-radius analysis across Trade-Pilot components. Check downstream impact and risk tiers."""
import unittest
from durai.blast_radius import analyze_blast_radius


class BlastRadiusTests(unittest.TestCase):
    def test_booking_component_has_high_blast_radius(self):
        # Sample: Analyze booking component. Trade is a core table and pnl depends on it. Check HIGH risk tier.
        impact = analyze_blast_radius('booking')
        self.assertEqual('booking', impact['component'])
        self.assertIn('trade', impact['modifiedTables'])
        self.assertIn('pnl', impact['downstreamComponents'])
        self.assertEqual('HIGH', impact['riskTier'])
        self.assertTrue(impact['requiresHumanSignoff'])

    def test_access_component_risk_tier(self):
        # Sample: Analyze access component. Cash depends on trader_account. Check downstream has cash.
        impact = analyze_blast_radius('access')
        self.assertIn('cash', impact['downstreamComponents'])

    def test_unknown_component_defaults_to_low(self):
        # Sample: Analyze an unknown component. Check LOW risk tier and 0 impact count.
        impact = analyze_blast_radius('unknown_component')
        self.assertEqual('LOW', impact['riskTier'])
        self.assertEqual(0, impact['impactCount'])


if __name__ == '__main__':
    unittest.main()
