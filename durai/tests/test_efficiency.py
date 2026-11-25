"""Sample: Test efficiency_metrics with build traces. Check stages accepted, call distribution, rework, and dual-basis pricing."""
import unittest
from durai.efficiency_metrics import pack_cost, trace_metrics


def sample_trace(calls):
    # Sample: Make a trace dict from a list of call tuples (name, status, args).
    messages = [{'role': 'human', 'content': 'Build component'}]
    for index, (name, status, args) in enumerate(calls):
        messages.append({
            'role': 'ai',
            'content': '',
            'tool_calls': [{'name': name, 'id': 'call-' + str(index), 'args': args}],
            'completion': {'inputCount': 1000, 'outputCount': 100}
        })
        messages.append({
            'role': 'tool',
            'name': name,
            'status': status,
            'tool_call_id': 'call-' + str(index),
            'content': 'Result for ' + name
        })
    return {'format': 'durai-worker-trace/v1', 'attemptId': 'blind-test-1', 'messages': messages}


class EfficiencyTests(unittest.TestCase):
    def test_trace_metrics_computes_stages_and_costs(self):
        # Sample: Trace with DESIGN and PLAN stages and 1 edit. Check 2 stages accepted and calculated dual-basis costs.
        calls = [
            ('write_file', 'success', {'path': 'docs/design.md'}),
            ('submit_stage', 'success', {'stage': 'DESIGN', 'paths': ['docs/design.md']}),
            ('write_file', 'success', {'path': 'docs/plan.md'}),
            ('submit_stage', 'success', {'stage': 'PLAN', 'paths': ['docs/plan.md']}),
        ]
        metrics = trace_metrics(sample_trace(calls))
        self.assertEqual(4, metrics['modelCalls'])
        self.assertEqual(4, metrics['toolCalls'])
        self.assertEqual(2, metrics['stageCount'])
        self.assertEqual(['DESIGN', 'PLAN'], metrics['stagesAccepted'])
        self.assertEqual(4000, metrics['inputCount'])
        self.assertEqual(400, metrics['outputCount'])
        # Enterprise: 4000 * 2.0/1e6 + 400 * 10.0/1e6 = 0.008 + 0.004 = 0.012
        self.assertAlmostEqual(0.012, metrics['costEnterprise'], places=5)
        # List: 4000 * 0.14/1e6 + 400 * 0.28/1e6 = 0.00056 + 0.000112 = 0.000672
        self.assertAlmostEqual(0.000672, metrics['costList'], places=6)

    def test_trace_metrics_detects_resubmission_and_rework(self):
        # Sample: Trace with a refused write and duplicate DESIGN submission. Check refusedWrites 1 and resubmissions 1.
        calls = [
            ('submit_stage', 'success', {'stage': 'DESIGN', 'paths': ['docs/design.md']}),
            ('write_file', 'error', {'path': 'src/bad.py'}),
            ('submit_stage', 'success', {'stage': 'DESIGN', 'paths': ['docs/design.md']}),
        ]
        metrics = trace_metrics(sample_trace(calls))
        self.assertEqual(1, metrics['refusedWrites'])
        self.assertEqual(1, metrics['resubmissions'])
        self.assertEqual(2, metrics['reworkCalls'])
        self.assertEqual(1, metrics['stageCount'])


if __name__ == '__main__':
    unittest.main()
