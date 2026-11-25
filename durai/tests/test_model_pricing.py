"""Sample: Test model pricing resolver. Check rates and cost estimation across DeepSeek direct, DeepInfra, Claude, and Enterprise."""
import unittest
from durai.model_pricing import estimate_costs, resolve_list_rate


class ModelPricingTests(unittest.TestCase):
    def test_deepseek_rates(self):
        # Sample: Check deepseek direct rate: $0.14 input, $0.28 output per 1M.
        rate = resolve_list_rate('deepseek', 'deepseek-chat')
        self.assertAlmostEqual(0.14 / 1e6, rate['input'], places=10)
        self.assertAlmostEqual(0.28 / 1e6, rate['output'], places=10)

    def test_deepinfra_rates(self):
        # Sample: Check deepinfra rate: $0.20 input, $0.60 output per 1M.
        rate = resolve_list_rate('deepinfra-deepseek')
        self.assertAlmostEqual(0.20 / 1e6, rate['input'], places=10)
        self.assertAlmostEqual(0.60 / 1e6, rate['output'], places=10)

    def test_cost_calculation(self):
        # Sample: 1M input and 100k output tokens on deepseek direct. Enterprise = 3.0 USD, List = 0.168 USD.
        costs = estimate_costs('deepseek', 'deepseek-flash', 1_000_000, 100_000)
        self.assertAlmostEqual(3.0, costs['enterprise'], places=4)
        self.assertAlmostEqual(0.168, costs['list'], places=4)


if __name__ == '__main__':
    unittest.main()
