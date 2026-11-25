import unittest
from program import execute


class PositionTests(unittest.TestCase):
    def test_opening_position(self):
        # Sample: Book T-101 with 2 lots of 100. Check 200 shares.
        result = execute({'commands': [{'action': 'book', 'id': 'T-101', 'lots': 2, 'lot_size': 100}]})
        self.assertEqual(result['netShares'], 200)

    def test_correction_position(self):
        # Sample: Remove 1 lot of 100 from a 200-share position. Check 100 shares.
        result = execute({'commands': [{'action': 'book', 'id': 'T-101', 'lots': 2, 'lot_size': 100},
                                       {'action': 'correct', 'id': 'T-101', 'delta_lots': 1}]})
        self.assertEqual(result['netShares'], 100)

    def test_short_position(self):
        # Sample: Book -2 lots of 100. Check a position of -200 shares.
        result = execute({'commands': [{'action': 'book', 'id': 'T-102', 'lots': -2, 'lot_size': 100}]})
        self.assertEqual(result['netShares'], -200)


if __name__ == '__main__':
    unittest.main()
