"""Sample: Test open ticket clustering, PROPOSED candidate creation, PASS_READY vs REGRESSION evaluation math, replay_trace integration, and human approval gate."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from durai.policy_eval import evaluate_candidate, promote_candidate
from durai.rl_store import InMemoryRLStore
from tools.triage_tickets import main as triage_cli_main, triage_open_tickets


class TriageTicketsTests(unittest.TestCase):
    def setUp(self):
        # Sample: Initialize an empty InMemoryRLStore and temporary folder for trace and CLI JSON files.
        self.store = InMemoryRLStore()
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)

    def tearDown(self):
        # Sample: Remove temporary directory after each test case.
        self.folder.cleanup()

    def test_open_ticket_clustering_and_candidate_creation(self):
        # Sample: Upsert 3 OPEN tickets and 1 RELEASED ticket. Triage OPEN tickets into 2 PROPOSED candidates and mark them CANDIDATE_LINKED.
        self.store.upsert_ticket({
            'ticket_id': 'T-1',
            'fingerprint': 'fp-stall-1',
            'source_mode': 'LOCAL_DURAI',
            'pack_id': 'durai.starter',
            'pack_version': '1.0.0',
            'category': 'TOOL_FRICTION',
            'severity': 'HIGH',
            'summary': 'STOP_STALL on S-TRADE-BOOK',
            'sanitized_payload': {'sampleId': 'S-TRADE-BOOK', 'stopReason': 'STOP_STALL'},
            'occurrence_count': 2,
            'status': 'OPEN',
        })
        self.store.upsert_ticket({
            'ticket_id': 'T-2',
            'fingerprint': 'fp-stall-2',
            'source_mode': 'LOCAL_DURAI',
            'pack_id': 'durai.starter',
            'pack_version': '1.0.0',
            'category': 'TOOL_FRICTION',
            'severity': 'MEDIUM',
            'summary': 'STOP_STALL on S-CHK-SCREENS',
            'sanitized_payload': {'sampleId': 'S-CHK-SCREENS', 'stopReason': 'STOP_STALL'},
            'occurrence_count': 1,
            'status': 'OPEN',
        })
        self.store.upsert_ticket({
            'ticket_id': 'T-3',
            'fingerprint': 'fp-cookie-1',
            'source_mode': 'PUBLIC_SHELL',
            'pack_id': 'durai.starter',
            'pack_version': '1.0.0',
            'category': 'WEB_RULE_GAP',
            'severity': 'CRITICAL',
            'summary': 'GET_CHANGES_COOKIE in signout route',
            'sanitized_payload': {'sampleId': 'S-CHK-AUTH', 'rule_id': 'GET_CHANGES_COOKIE'},
            'occurrence_count': 4,
            'status': 'OPEN',
        })
        self.store.upsert_ticket({
            'ticket_id': 'T-4',
            'fingerprint': 'fp-released-1',
            'source_mode': 'LOCAL_DURAI',
            'pack_id': 'durai.starter',
            'pack_version': '1.0.0',
            'category': 'UI_UX_DEFECT',
            'severity': 'LOW',
            'summary': 'Already released ticket',
            'sanitized_payload': {'sampleId': 'S-OLD'},
            'status': 'RELEASED',
        })

        candidates = triage_open_tickets(self.store, proposed_by='durai-triage-agent')
        self.assertEqual(2, len(candidates))
        self.assertEqual([], list(self.store.list_tickets(status='OPEN')))

        linked = list(self.store.list_tickets(status='CANDIDATE_LINKED'))
        self.assertEqual(3, len(linked))
        counts_by_id = {t['ticket_id']: t['occurrence_count'] for t in linked}
        self.assertEqual(2, counts_by_id['T-1'])
        self.assertEqual(4, counts_by_id['T-3'])

        by_kind = {c['target_kind']: c for c in candidates}
        self.assertIn('LESSON_RULE', by_kind)
        self.assertIn('WEB_STATIC_RULE', by_kind)
        self.assertEqual('PROPOSED', by_kind['LESSON_RULE']['status'])
        self.assertEqual('durai-triage-agent', by_kind['LESSON_RULE']['proposed_by'])
        self.assertIsNone(by_kind['LESSON_RULE']['approved_by'])
        self.assertEqual(3, by_kind['LESSON_RULE']['occurrence_total'])
        self.assertEqual(['S-CHK-SCREENS', 'S-TRADE-BOOK'], by_kind['LESSON_RULE']['proposal']['affectedComponents'])

        self.assertEqual([], triage_open_tickets(self.store))

    def test_evaluate_candidate_pass_ready_vs_regression_and_inconclusive(self):
        # Sample: Evaluate candidate C-1 on improving, regressing, and unverified episode pairs. Check exact deltas and decisions.
        self.store.create_candidate({
            'candidate_id': 'C-EVAL-1',
            'cluster_fingerprint': 'fp-1',
            'target_kind': 'LESSON_RULE',
            'target_ref': 'lessons/tool_friction/stop_stall',
            'proposed_patch': 'RULE: Read target lines before edit_file.',
            'proposed_by': 'durai-triage-agent',
        })

        base_ep = self.store.record_episode({
            'episode_id': 'EP-BASE-1', 'run_id': 'R-1', 'sample_id': 'S-TRADE-BOOK',
            'component_category': 'trading', 'model_id': 'DeepSeek-V4.1-Flash',
            'provider': 'deepinfra', 'effective_view_digest': 'ev-1', 'stop_reason': 'STOP_STALL',
            'calls_used': 14, 'list_cost_usd': 0.05, 'passed_checks': 2, 'failed_checks': 3,
            'tool_error_count': 4, 'smell_high': 0, 'reward_score': 0.1,
        })
        good_ep = self.store.record_episode({
            'episode_id': 'EP-GOOD-1', 'run_id': 'R-2', 'sample_id': 'S-TRADE-BOOK',
            'component_category': 'trading', 'model_id': 'DeepSeek-V4.1-Flash',
            'provider': 'deepinfra', 'effective_view_digest': 'ev-2', 'stop_reason': 'FINISHED',
            'calls_used': 8, 'list_cost_usd': 0.02, 'passed_checks': 5, 'failed_checks': 0,
            'tool_error_count': 1, 'smell_high': 0, 'reward_score': 1.4,
        })
        bad_ep = self.store.record_episode({
            'episode_id': 'EP-BAD-1', 'run_id': 'R-3', 'sample_id': 'S-TRADE-BOOK',
            'component_category': 'trading', 'model_id': 'DeepSeek-V4.1-Flash',
            'provider': 'deepinfra', 'effective_view_digest': 'ev-2', 'stop_reason': 'STOP_CALLS',
            'calls_used': 18, 'list_cost_usd': 0.08, 'passed_checks': 1, 'failed_checks': 4,
            'tool_error_count': 6, 'smell_high': 1, 'reward_score': -0.2,
        })
        unverified_ep = {
            'episode_id': 'EP-ZERO-1', 'calls_used': 4, 'list_cost_usd': 0.01,
            'passed_checks': 0, 'failed_checks': 0, 'tool_error_count': 0,
        }

        ev_pass = evaluate_candidate(self.store, 'C-EVAL-1', 'EP-BASE-1', 'EP-GOOD-1')
        self.assertEqual('PASS_READY', ev_pass['decision'])
        self.assertEqual(0.6, ev_pass['delta_pass_rate'])
        self.assertEqual(-6, ev_pass['delta_calls'])
        self.assertEqual(-3, ev_pass['delta_tool_errors'])
        self.assertEqual(-0.03, ev_pass['delta_list_cost_usd'])

        ev_reg = evaluate_candidate(self.store, 'C-EVAL-1', base_ep, bad_ep)
        self.assertEqual('REGRESSION', ev_reg['decision'])
        self.assertEqual(-0.2, ev_reg['delta_pass_rate'])
        self.assertEqual(4, ev_reg['delta_calls'])
        self.assertEqual(2, ev_reg['delta_tool_errors'])
        self.assertEqual(0.03, ev_reg['delta_list_cost_usd'])

        ev_inc = evaluate_candidate(self.store, 'C-EVAL-1', base_ep, unverified_ep)
        self.assertEqual('INCONCLUSIVE', ev_inc['decision'])

    def test_evaluate_candidate_detects_smell_regression(self):
        # Sample: Evaluate candidate with RAW_SQL_FORMAT smell in python_files. Verify decision is REGRESSION.
        self.store.create_candidate({
            'candidate_id': 'C-SMELL-1',
            'cluster_fingerprint': 'fp-smell',
            'target_kind': 'SHELL_TEMPLATE',
            'target_ref': 'templates/sql-blueprint/raw_sql',
            'proposed_patch': 'RULE: Use parameterized queries.',
            'proposed_by': 'durai-triage-agent',
        })
        base_ep = {'episode_id': 'EP-SB', 'passed_checks': 2, 'failed_checks': 3, 'calls_used': 10, 'tool_error_count': 2, 'list_cost_usd': 0.04}
        good_ep = {'episode_id': 'EP-ST', 'passed_checks': 5, 'failed_checks': 0, 'calls_used': 6, 'tool_error_count': 0, 'list_cost_usd': 0.02}
        bad_sql = (
            "def load(oid):\n"
            "    return 'SELECT * FROM orders WHERE id = {}'"
            ".format(oid)\n"
        )
        ev_smell_reg = evaluate_candidate(
            self.store,
            'C-SMELL-1',
            base_ep,
            good_ep,
            python_files={'app.py': bad_sql},
        )
        self.assertEqual('REGRESSION', ev_smell_reg['decision'])

    def test_evaluate_candidate_with_replay_trace(self):
        # Sample: Pass a worker trace file to evaluate_candidate. Verify dry-run replay_trace metadata and PASS_READY decision.
        trace_path = self.root / 'trace.json'
        trace_path.write_text(
            json.dumps({
                'format': 'durai-worker-trace/v1',
                'attemptId': 'attempt-s3-1',
                'messages': [
                    {'role': 'human', 'content': 'start'},
                    {'role': 'ai', 'content': '', 'tool_calls': [{'name': 'read_file', 'args': {'path': 'a.py'}, 'id': 'c1'}]},
                    {'role': 'tool', 'name': 'read_file', 'content': 'ok', 'tool_call_id': 'c1'},
                    {'role': 'ai', 'content': '', 'tool_calls': [{'name': 'edit_file', 'args': {'path': 'a.py'}, 'id': 'c2'}]},
                    {'role': 'tool', 'name': 'edit_file', 'content': 'ok', 'tool_call_id': 'c2'},
                ],
            }),
            encoding='utf-8',
        )
        self.store.create_candidate({
            'candidate_id': 'C-REPLAY-1',
            'cluster_fingerprint': 'fp-replay',
            'target_kind': 'LESSON_RULE',
            'target_ref': 'lessons/edit',
            'proposed_patch': 'RULE: Read before edit.',
            'proposed_by': 'durai-triage-agent',
        })
        ev = evaluate_candidate(
            self.store,
            'C-REPLAY-1',
            {'episode_id': 'B1', 'passed_checks': 5, 'failed_checks': 0, 'calls_used': 10, 'tool_error_count': 2, 'list_cost_usd': 0.04},
            {'episode_id': 'T1', 'passed_checks': 5, 'failed_checks': 0, 'calls_used': 6, 'tool_error_count': 0, 'list_cost_usd': 0.02},
            replay_trace_path=str(trace_path),
        )
        self.assertEqual('PASS_READY', ev['decision'])
        self.assertTrue(ev['replay']['matched'])
        self.assertEqual(2, ev['replay']['totalSteps'])
        self.assertEqual(['edit_file', 'read_file'], ev['replay']['uniqueTools'])

    def test_promote_candidate_enforces_human_allowlist_and_distinct_proposer(self):
        # Sample: Reject non-human agent and proposer self-approval with PermissionError. Accept authorized human operator.
        store = InMemoryRLStore(human_allowlist=['operator', 'durai-triage-agent'])
        store.create_candidate({
            'candidate_id': 'C-GATE-1',
            'cluster_fingerprint': 'fp-gate',
            'target_kind': 'GUIDANCE_TOPIC',
            'target_ref': 'guidance/ui-ux',
            'proposed_patch': 'RULE: Handle 409 inline.',
            'proposed_by': 'durai-triage-agent',
        })

        with self.assertRaises(PermissionError):
            promote_candidate(store, 'C-GATE-1', 'gemini-agent-auto')

        with self.assertRaises(PermissionError):
            promote_candidate(store, 'C-GATE-1', 'durai-triage-agent')

        with self.assertRaises(PermissionError):
            promote_candidate(store, 'C-GATE-1', '')

        approved = promote_candidate(store, 'C-GATE-1', 'operator')
        self.assertEqual('APPROVED', approved['status'])
        self.assertEqual('operator', approved['approved_by'])

    def test_cli_entrypoint_triages_evaluates_and_blocks_agent_approval(self):
        # Sample: Run CLI main with tickets, episodes, and trace files. Check JSON output and non-zero exit for agent approval.
        tickets_path = self.root / 'tickets.json'
        base_path = self.root / 'base.json'
        treat_path = self.root / 'treat.json'
        trace_path = self.root / 'cli_trace.json'

        tickets_path.write_text(
            json.dumps([{
                'ticket_id': 'T-CLI-1',
                'fingerprint': 'fp-cli-1',
                'source_mode': 'LOCAL_DURAI',
                'pack_id': 'durai.starter',
                'pack_version': '1.0.0',
                'category': 'UI_UX_DEFECT',
                'severity': 'HIGH',
                'summary': '409 Conflict crashes ErrorBoundary with React #441',
                'sanitized_payload': {'sampleId': 'S-CHK-CART', 'rule_id': 'REACT_441'},
                'occurrence_count': 3,
                'status': 'OPEN',
            }]),
            encoding='utf-8',
        )
        base_path.write_text(
            json.dumps({'episode_id': 'EP-CLI-B', 'passed_checks': 1, 'failed_checks': 2, 'calls_used': 12, 'tool_error_count': 2, 'list_cost_usd': 0.04}),
            encoding='utf-8',
        )
        treat_path.write_text(
            json.dumps({'episode_id': 'EP-CLI-T', 'passed_checks': 3, 'failed_checks': 0, 'calls_used': 7, 'tool_error_count': 0, 'list_cost_usd': 0.02}),
            encoding='utf-8',
        )
        trace_path.write_text(
            json.dumps({'format': 'durai-worker-trace/v1', 'attemptId': 'cli-1', 'messages': []}),
            encoding='utf-8',
        )

        stdout_buf = io.StringIO()
        with redirect_stdout(stdout_buf):
            code = triage_cli_main([
                '--tickets-json', str(tickets_path),
                '--baseline-json', str(base_path),
                '--treatment-json', str(treat_path),
                '--replay-trace', str(trace_path),
                '--approve-as', 'operator',
            ])
        self.assertEqual(0, code)
        payload = json.loads(stdout_buf.getvalue())
        self.assertEqual(1, payload['candidatesCreated'])
        self.assertEqual('PASS_READY', payload['evaluations'][0]['decision'])
        self.assertEqual('APPROVED', payload['promoted'][0]['status'])

        stderr_buf = io.StringIO()
        with redirect_stderr(stderr_buf):
            bad_code = triage_cli_main([
                '--tickets-json', str(tickets_path),
                '--approve-as', 'auto-bot',
            ])
        self.assertEqual(1, bad_code)


if __name__ == '__main__':
    unittest.main()
