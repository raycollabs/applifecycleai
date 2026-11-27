"""Sample: Verify reward calculation, zero-check cap, signal extraction, privacy sanitization, and duplicate ticket fingerprinting."""
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from durai.privacy_sanitizer import PrivacySanitizer, contains_raw_code_or_secret
from durai.rl_store import InMemoryRLStore
from durai.ticket_collector import (
    VALID_SIGNAL_KINDS,
    collect_run_feedback,
    compute_reward,
    extract_signals,
)


class TicketCollectorTests(unittest.TestCase):
    def test_deterministic_reward_formula(self):
        # Sample: Compute reward for a FINISHED run with 8 passed, 2 failed, 2 tool errors, 1 nudge, smells, and 0.10 USD cost.
        receipt = {
            'stopReason': 'FINISHED',
            'lastRunTests': {'passed': 8, 'failed': 2},
            'toolErrors': {'edit_file': 2},
            'stall_nudges': 1,
            'estimatedListCost': 0.10,
        }
        smells = {
            'summary': {'high': 1, 'medium': 2, 'low': 3},
            'smells': [{'severity': 'HIGH', 'kind': 'RAW_SQL_FORMAT'}],
        }
        expected = round(
            1.00
            + 0.50 * (8 / 10)
            - 0.05 * 2
            - 0.15 * 1
            - 0.10 * 1
            - 0.02 * 3
            - 0.25 * math.log1p(0.10 * 10.0),
            4,
        )
        self.assertEqual(expected, compute_reward(receipt, smells=smells))
        self.assertEqual(expected, compute_reward(receipt, smells=smells))

    def test_zero_check_reward_cap(self):
        # Sample: Cap reward at 0.0 when passed_checks + failed_checks == 0 even if stopReason is FINISHED.
        finished_no_checks = {
            'stopReason': 'FINISHED',
            'lastRunTests': {'passed': 0, 'failed': 0},
            'estimatedListCost': 0.0,
        }
        self.assertEqual(0.0, compute_reward(finished_no_checks))

        failed_no_checks = {
            'stopReason': 'STOP_STALL',
            'lastRunTests': None,
            'toolErrors': {'edit_file': 2},
            'estimatedListCost': 0.05,
        }
        score = compute_reward(failed_no_checks)
        self.assertLess(score, 0.0)

    def test_extract_signals_from_trace_smells_and_web_rules(self):
        # Sample: Parse trace entries, smell list, and web rule violations into valid episode_signal dicts.
        secret_tok = 'sk-' + 'x' * 24
        trace_entries = [
            {
                'role': 'ai',
                'content': '',
                'tool_calls': [
                    {'id': 'c1', 'name': 'edit_file', 'args': {}},
                    {'id': 'c2', 'name': 'submit_stage', 'args': {}},
                    {'id': 'c3', 'name': 'check_app', 'args': {}},
                ],
            },
            {
                'role': 'tool',
                'tool_call_id': 'c1',
                'status': 'error',
                'content': f'ERROR: Old text must occur exactly once; token={secret_tok}',
            },
            {
                'role': 'tool',
                'tool_call_id': 'c2',
                'status': 'error',
                'content': 'ERROR: Stage IMPLEMENTATION refused. Next stage is PLAN.',
            },
            {
                'role': 'human',
                'content': 'Notice: 30 calls have run without a stage advance.',
            },
            {
                'role': 'tool',
                'tool_call_id': 'c3',
                'status': 'success',
                'content': 'status: 500\nDEPLOYED is refused until check_app passes.',
            },
        ]
        smells = [
            {'kind': 'HARDCODED_SECRET', 'severity': 'HIGH', 'line': 4},
            {'kind': 'EXCESSIVE_FILE_LENGTH', 'severity': 'LOW', 'line': 1},
        ]
        web_rules = [
            {'rule': 'GET_CHANGES_COOKIE', 'path': 'app/signout/route.ts', 'message': 'GET handler sets cookie'},
            {'rule': 'ABSOLUTE_REDIRECT', 'path': 'app/signout/route.ts', 'message': 'Uses request.url'},
        ]

        signals = extract_signals(trace_entries, smells=smells, web_rules=web_rules)
        kinds = [s['signal_kind'] for s in signals]
        self.assertEqual(
            ['EDIT_MISMATCH', 'PLAN_GATE_REFUSAL', 'STALL_NUDGE', 'CHECK_APP_FAIL', 'SMELL_HIGH', 'GET_CHANGES_COOKIE', 'CHECK_APP_FAIL'],
            kinds,
        )
        for sig in signals:
            self.assertIn(sig['signal_kind'], VALID_SIGNAL_KINDS)
            self.assertEqual(64, len(sig['detail_digest']))
            self.assertNotIn(secret_tok, json.dumps(sig))

    def test_privacy_sanitizer_strips_paths_code_overlays_and_secrets(self):
        # Sample: Verify PrivacySanitizer strips Windows/Unix paths, code snippets, user overlays, and secrets while keeping token counts.
        fake_sk = 'sk-' + 'k' * 28
        fake_google = 'AIza' + 'G' * 35
        fake_gh = 'ghp_' + 'H' * 36
        win_path = 'C:\\Users\\' + 'alice\\workspace\\src\\app.py'
        unix_path = '/home/alice/repos/durai/app.ts'
        dirty = {
            'category': 'TOOL_FRICTION',
            'pack_id': 'durai.starter',
            'input_tokens': 1200,
            'output_tokens': 300,
            'api_key': fake_sk,
            'user_overlay': {'custom_prompt': 'always do X'},
            'source_code': 'def secret_fn():\n    return 42\n',
            'tracePath': win_path,
            'summary': f'Failed in {win_path} and {unix_path} with {fake_google} and Bearer {fake_gh}\n```python\nimport os\n```\ndef leak():\n    pass',
        }
        self.assertTrue(contains_raw_code_or_secret(dirty))

        sanitizer = PrivacySanitizer()
        cleaned = sanitizer.sanitize_payload(dirty)

        serialized = json.dumps(cleaned)
        self.assertFalse(contains_raw_code_or_secret(cleaned))
        self.assertNotIn(fake_sk, serialized)
        self.assertNotIn(fake_google, serialized)
        self.assertNotIn(fake_gh, serialized)
        self.assertNotIn('alice', serialized)
        self.assertNotIn('user_overlay', cleaned)
        self.assertNotIn('source_code', cleaned)
        self.assertNotIn('tracePath', cleaned)
        self.assertNotIn('def leak', serialized)
        self.assertNotIn('import os', serialized)
        self.assertEqual(1200, cleaned['input_tokens'])
        self.assertEqual(300, cleaned['output_tokens'])

        self.assertTrue(contains_raw_code_or_secret('SELECT * FROM trades WHERE id = 1'))
        self.assertTrue(contains_raw_code_or_secret({'token': fake_sk}))
        self.assertFalse(contains_raw_code_or_secret({'category': 'WEB_RULE_GAP', 'signal_kinds': ['GET_CHANGES_COOKIE']}))

    def test_collect_run_feedback_clean_run_files_no_ticket(self):
        # Sample: Run collect_run_feedback on clean FINISHED receipt. Check episode and route stats saved with no ticket.
        store = InMemoryRLStore()
        receipt = {
            'runId': 'R-CLEAN',
            'sampleId': 'S-TRADE-BOOK',
            'model': 'DeepSeek-V4.1-Flash',
            'provider': 'deepinfra',
            'stopReason': 'FINISHED',
            'calls': 8,
            'inputTokens': 2000,
            'outputTokens': 500,
            'estimatedListCost': 0.001,
            'estimatedEnterpriseCost': 0.009,
            'lastRunTests': {'passed': 5, 'failed': 0},
            'toolErrors': {},
        }
        result = collect_run_feedback(store, receipt, component_category='trading', pack_version='1.0.1')
        self.assertIsNone(result['ticket'])
        self.assertEqual([], store.list_tickets())
        self.assertGreater(result['episode']['reward_score'], 1.0)
        stats = store.get_route_stats('trading')
        self.assertEqual(1, len(stats))
        self.assertEqual(1, stats[0]['episodes_count'])
        self.assertEqual(1.0, stats[0]['finish_rate'])

    def test_collect_run_feedback_public_sanitization_and_duplicate_fingerprint(self):
        # Sample: Submit two failing PUBLIC_SHELL runs with same friction category and private fields. Check single ticket with occurrence_count 2 and zero leaks.
        store = InMemoryRLStore()
        fake_sk = 'sk-' + 'z' * 26
        win_path = 'D:\\Users\\' + 'dev\\private\\trace.jsonl'
        with tempfile.TemporaryDirectory() as folder:
            trace_file = Path(folder) / 'stream-1.jsonl'
            trace_entries = [
                {'role': 'ai', 'content': '', 'tool_calls': [{'id': 't1', 'name': 'edit_file', 'args': {}}]},
                {'role': 'tool', 'tool_call_id': 't1', 'status': 'error', 'content': f'ERROR: Old text must occur exactly once in {win_path} key={fake_sk}'},
            ]
            trace_file.write_text('\n'.join(json.dumps(row) for row in trace_entries) + '\n', encoding='utf-8')

            receipt_1 = {
                'runId': 'R-1',
                'sampleId': 'S-CHK-SCREENS',
                'model': 'DeepSeek-V4.1-Flash',
                'provider': 'deepinfra',
                'stopReason': 'STOP_STALL',
                'calls': 30,
                'inputTokens': 5000,
                'outputTokens': 1000,
                'estimatedListCost': 0.02,
                'lastRunTests': {'passed': 1, 'failed': 2},
                'toolErrors': {'edit_file': 2},
                'stall_nudges': 1,
                'user_overlay': {'note': 'private rule'},
                'code': 'export function GET() { return null; }',
                'tracePath': win_path,
                'api_key': fake_sk,
            }
            receipt_2 = dict(receipt_1, runId='R-2', calls=40)

            res1 = collect_run_feedback(
                store,
                receipt_1,
                trace_path=trace_file,
                component_category='ui',
                source_mode='PUBLIC_SHELL',
            )
            res2 = collect_run_feedback(
                store,
                receipt_2,
                trace_path=trace_file,
                component_category='ui',
                source_mode='PUBLIC_SHELL',
            )

        tickets = store.list_tickets()
        self.assertEqual(1, len(tickets))
        self.assertEqual(2, tickets[0]['occurrence_count'])
        self.assertEqual(res1['ticket']['fingerprint'], res2['ticket']['fingerprint'])
        self.assertFalse(contains_raw_code_or_secret(tickets[0]))
        ticket_json = json.dumps(tickets[0])
        self.assertNotIn(fake_sk, ticket_json)
        self.assertNotIn('dev', ticket_json)
        self.assertNotIn('user_overlay', tickets[0]['sanitized_payload'])
        self.assertNotIn('code', tickets[0]['sanitized_payload'])
        self.assertNotIn('tracePath', tickets[0]['sanitized_payload'])

        stats = store.get_route_stats('ui')
        self.assertEqual(1, len(stats))
        self.assertEqual(2, stats[0]['episodes_count'])
        self.assertEqual(0.0, stats[0]['finish_rate'])
        self.assertEqual(35.0, stats[0]['avg_calls'])


if __name__ == '__main__':
    unittest.main()
