"""Sample: Test Arm 1 vs Arm 2 comparison metrics calculation and markdown table rendering."""
import pytest
from pathlib import Path
from tools.compare_arms import calculate_arm_metrics, render_markdown_table


class TestCompareArms:
    def test_calculate_arm_metrics(self):
        # Sample: Aggregate two sample receipts. Check calls, costs, and passed test sums.
        receipts = [
            {
                'sampleId': 'S-TRADE-BOOK',
                'calls': 20,
                'inputTokens': 10000,
                'outputTokens': 2000,
                'estimatedEnterpriseCost': 0.04,
                'estimatedListCost': 0.003,
                'lastRunTests': {'passed': 25, 'failed': 0},
                'toolErrors': {'edit_file': 1},
                'stopReason': 'FINISHED',
            },
            {
                'sampleId': 'S-TRADE-ACCESS',
                'calls': 15,
                'inputTokens': 8000,
                'outputTokens': 1500,
                'estimatedEnterpriseCost': 0.03,
                'estimatedListCost': 0.002,
                'lastRunTests': {'passed': 30, 'failed': 2},
                'toolErrors': {'edit_file': 2},
                'stopReason': 'FINISHED',
            },
        ]
        m = calculate_arm_metrics(receipts)
        assert m['componentCount'] == 2
        assert m['finishedCount'] == 2
        assert m['totalCalls'] == 35
        assert m['testsPassed'] == 55
        assert m['testsFailed'] == 2
        assert m['toolErrors'] == 3

    def test_render_markdown_table(self):
        # Sample: Format comparison dict into markdown table. Verify column headers and lift numbers.
        comp = {
            'arm1_durai': {
                'finishedCount': 5,
                'testsPassed': 134,
                'toolErrors': 2,
                'totalCalls': 140,
                'enterpriseCost': 30.5,
                'listCost': 2.1,
            },
            'arm2_bare': {
                'finishedCount': 1,
                'testsPassed': 20,
                'toolErrors': 18,
                'totalCalls': 180,
                'enterpriseCost': 35.0,
                'listCost': 2.8,
            },
            'lift': {
                'passRateLiftPercent': 75.2,
                'arm1PassRate': 98.5,
                'arm2PassRate': 23.3,
                'toolErrorReductionPercent': 88.89,
                'arm1Finished': 5,
                'arm2Finished': 1,
            },
        }
        table = render_markdown_table(comp)
        assert '| Metric | Arm 1 (DeepSeek Flash + Durai) | Arm 2 (DeepSeek Flash Bare) |' in table
        assert '+75.2%' in table
        assert '88.89% reduction' in table
