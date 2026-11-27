"""Sample: Run RLStoreContract on InMemoryRLStore and PostgresRLStore. Check zero-check reward refusal, ticket fingerprinting, human vs agent approval, and restart persistence."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pg_support
from durai.adapters.rl_store.postgres import PostgresRLStore
from durai.rl_store import InMemoryRLStore


def make_episode(episode_id='EP-1', passed=18, failed=0, reward=0.9):
    # Sample: Return an episode dictionary for S-CHK-SCREENS with the given check counts and reward.
    return {
        'episode_id': episode_id,
        'run_id': 'RUN-1',
        'sample_id': 'S-CHK-SCREENS',
        'component_category': 'ui',
        'shell_profile': 'py-fastapi-next',
        'model_id': 'DeepSeek-V4.1-Flash',
        'provider': 'deepinfra',
        'effective_view_digest': 'a' * 64,
        'stop_reason': 'FINISH',
        'calls_used': 5,
        'input_tokens': 1200,
        'output_tokens': 450,
        'list_cost_usd': 0.0012,
        'enterprise_cost_usd': 0.001,
        'passed_checks': passed,
        'failed_checks': failed,
        'tool_error_count': 0,
        'stall_nudges': 0,
        'smell_high': 0,
        'smell_low': 0,
        'reward_score': reward,
    }


def make_ticket(ticket_id='T-1', fingerprint='F-1', category='TOOL_FRICTION', status='OPEN'):
    # Sample: Return a sanitized ticket dictionary for fingerprint F-1.
    return {
        'ticket_id': ticket_id,
        'fingerprint': fingerprint,
        'source_mode': 'LOCAL_DURAI',
        'client_instance_hash': 'client-1',
        'pack_id': 'durai-core',
        'pack_version': '1.0.0',
        'shell_version': '1.0.0',
        'category': category,
        'severity': 'HIGH',
        'summary': 'edit mismatch on repeated block',
        'sanitized_payload': {'tool': 'edit_file', 'count': 1},
        'status': status,
    }


def make_candidate(candidate_id='C-1', proposed_by='S3', status='PROPOSED'):
    # Sample: Return a policy candidate dictionary proposed by S3.
    return {
        'candidate_id': candidate_id,
        'cluster_fingerprint': 'F-1',
        'target_kind': 'LESSON_RULE',
        'target_ref': 'lessons/edit_file.md',
        'proposed_patch': 'Always read target block before editing.',
        'status': status,
        'proposed_by': proposed_by,
    }


class RLStoreContract:
    """Sample: Mix into a test case. Return an RLStore from make_store. Verify all RLStore contract behaviors."""

    def make_store(self):
        # Sample: Return a clean RLStore instance.
        raise NotImplementedError

    def setUp(self):
        # Sample: Initialize self.store from make_store before each contract test.
        self.store = self.make_store()

    def test_zero_check_positive_reward_is_refused(self):
        # Sample: Record an episode with 0 passed and 0 failed checks and reward 0.5. Check ValueError and no stored row.
        with self.assertRaises(ValueError):
            self.store.record_episode(make_episode('EP-BAD', passed=0, failed=0, reward=0.5))
        self.assertIsNone(self.store.get_episode('EP-BAD'))
        stored_zero = self.store.record_episode(make_episode('EP-ZERO', passed=0, failed=0, reward=0.0))
        self.assertEqual(0.0, stored_zero['reward_score'])
        stored_ok = self.store.record_episode(make_episode('EP-1', passed=18, failed=0, reward=0.9))
        self.assertEqual(18, stored_ok['passed_checks'])
        self.assertEqual(0.9, self.store.get_episode('EP-1')['reward_score'])

    def test_signals_preserve_append_order_and_require_episode(self):
        # Sample: Append two signals for EP-1. Check order. Append for missing EP-99. Check KeyError.
        with self.assertRaises(KeyError):
            self.store.append_signal({
                'episode_id': 'EP-99',
                'step_index': 0,
                'signal_kind': 'EDIT_MISMATCH',
                'detail_digest': 'd0',
            })
        self.store.record_episode(make_episode('EP-1'))
        s1 = self.store.append_signal({
            'episode_id': 'EP-1',
            'step_index': 1,
            'signal_kind': 'EDIT_MISMATCH',
            'tool_name': 'edit_file',
            'detail_digest': 'd1',
        })
        s2 = self.store.append_signal({
            'episode_id': 'EP-1',
            'step_index': 2,
            'signal_kind': 'SMELL_HIGH',
            'rule_id': 'WEB-01',
            'detail_digest': 'd2',
        })
        listed = self.store.list_signals('EP-1')
        self.assertEqual([s1['signal_id'], s2['signal_id']], [item['signal_id'] for item in listed])
        self.assertEqual(['EDIT_MISMATCH', 'SMELL_HIGH'], [item['signal_kind'] for item in listed])

    def test_ticket_upsert_is_idempotent_on_fingerprint_and_increments_count(self):
        # Sample: Upsert fingerprint F-1 twice. Check one ticket row with occurrence_count 2 and descending list order.
        first = self.store.upsert_ticket(make_ticket('T-1', 'F-1'))
        self.assertEqual(1, first['occurrence_count'])
        second = self.store.upsert_ticket(make_ticket('T-2', 'F-1'))
        self.assertEqual('T-1', second['ticket_id'])
        self.assertEqual(2, second['occurrence_count'])
        self.store.upsert_ticket(make_ticket('T-3', 'F-2', status='TRIAGED'))
        open_tickets = self.store.list_tickets(status='OPEN')
        self.assertEqual(1, len(open_tickets))
        self.assertEqual(2, open_tickets[0]['occurrence_count'])
        all_tickets = self.store.list_tickets()
        self.assertEqual(['F-1', 'F-2'], [t['fingerprint'] for t in all_tickets])

    def test_human_vs_agent_approval_check(self):
        # Sample: Block agent approver S3 and human self-approval. Allow distinct human reviewer operator.
        with self.assertRaises(KeyError):
            self.store.approve_candidate('C-MISSING', 'operator')
        self.store.create_candidate(make_candidate('C-1', proposed_by='S3'))
        with self.assertRaises(PermissionError):
            self.store.approve_candidate('C-1', 'S3')
        with self.assertRaises(PermissionError):
            self.store.approve_candidate('C-1', 'agent-reviewer')
        approved = self.store.approve_candidate('C-1', 'operator')
        self.assertEqual('APPROVED', approved['status'])
        self.assertEqual('operator', approved['approved_by'])
        self.assertIsNotNone(approved['approved_at'])
        self.store.create_candidate(make_candidate('C-2', proposed_by='operator'))
        with self.assertRaises(PermissionError):
            self.store.approve_candidate('C-2', 'operator')
        approved_list = self.store.list_candidates(status='APPROVED')
        self.assertEqual(['C-1'], [c['candidate_id'] for c in approved_list])

    def test_evaluation_and_route_stats(self):
        # Sample: Record evaluation EV-1 for C-1 and upsert two route stats. Check descending mean_reward order.
        with self.assertRaises(KeyError):
            self.store.record_evaluation({
                'eval_id': 'EV-BAD',
                'candidate_id': 'C-MISSING',
                'delta_pass_rate': 0.1,
                'delta_calls': -1,
                'delta_tool_errors': 0,
                'delta_list_cost_usd': -0.001,
                'decision': 'PASS_READY',
            })
        self.store.record_episode(make_episode('EP-BASE', passed=15, failed=3, reward=0.6))
        self.store.record_episode(make_episode('EP-TREAT', passed=18, failed=0, reward=0.95))
        self.store.create_candidate(make_candidate('C-1'))
        ev = self.store.record_evaluation({
            'eval_id': 'EV-1',
            'candidate_id': 'C-1',
            'baseline_episode_id': 'EP-BASE',
            'treatment_episode_id': 'EP-TREAT',
            'delta_pass_rate': 0.1667,
            'delta_calls': -2,
            'delta_tool_errors': -1,
            'delta_list_cost_usd': -0.0005,
            'decision': 'PASS_READY',
        })
        self.assertEqual('PASS_READY', ev['decision'])
        self.store.upsert_route_stats({
            'component_category': 'ui',
            'shell_profile': 'py-fastapi-next',
            'model_id': 'Model-Low',
            'provider': 'deepinfra',
            'pack_version': '1.0.0',
            'episodes_count': 4,
            'finish_rate': 0.75,
            'avg_calls': 8.5,
            'avg_list_cost_usd': 0.004,
            'mean_reward': 0.55,
        })
        self.store.upsert_route_stats({
            'component_category': 'ui',
            'shell_profile': 'py-fastapi-next',
            'model_id': 'Model-High',
            'provider': 'deepinfra',
            'pack_version': '1.0.0',
            'episodes_count': 6,
            'finish_rate': 0.95,
            'avg_calls': 5.0,
            'avg_list_cost_usd': 0.002,
            'mean_reward': 0.88,
        })
        self.store.upsert_route_stats({
            'component_category': 'ui',
            'shell_profile': 'py-fastapi-next',
            'model_id': 'Model-High',
            'provider': 'deepinfra',
            'pack_version': '1.0.0',
            'episodes_count': 7,
            'finish_rate': 0.96,
            'avg_calls': 4.8,
            'avg_list_cost_usd': 0.0019,
            'mean_reward': 0.91,
        })
        rows = self.store.get_route_stats('ui', 'py-fastapi-next')
        self.assertEqual(['Model-High', 'Model-Low'], [r['model_id'] for r in rows])
        self.assertEqual(7, rows[0]['episodes_count'])


class InMemoryRLStoreTests(RLStoreContract, unittest.TestCase):
    def make_store(self):
        # Sample: Return a fresh InMemoryRLStore instance.
        return InMemoryRLStore()


@unittest.skipUnless(pg_support.docker_ready(), 'Docker is not available')
class PostgresRLStoreTests(RLStoreContract, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Start a disposable PostgreSQL 17 container once for PostgresRLStoreTests.
        cls.connect = pg_support.start_postgres()

    @classmethod
    def tearDownClass(cls):
        # Sample: Stop and remove the disposable PostgreSQL 17 container.
        pg_support.stop_postgres()

    def make_store(self):
        # Sample: Apply rl_0001.sql.proposed and truncate all improvement tables before returning a store.
        store = PostgresRLStore(type(self).connect)
        store.ensure_schema()
        with type(self).connect() as conn:
            conn.execute(
                'TRUNCATE durai_improvement.policy_evaluation, '
                'durai_improvement.episode_signal, '
                'durai_improvement.execution_episode, '
                'durai_improvement.ticket, '
                'durai_improvement.policy_candidate, '
                'durai_improvement.model_route_stats RESTART IDENTITY CASCADE'
            )
        return store

    def test_ensure_schema_is_idempotent(self):
        # Sample: Call ensure_schema a second time on an initialized database. Check no error.
        self.store.ensure_schema()
        self.store.ensure_schema()

    def test_restart_persistence_across_new_store_instance(self):
        # Sample: Write episode, signal, ticket, candidate, evaluation, and route stats, then read from a fresh PostgresRLStore instance.
        self.store.record_episode(make_episode('EP-PERSIST', passed=10, failed=1, reward=0.82))
        self.store.append_signal({
            'episode_id': 'EP-PERSIST',
            'step_index': 0,
            'signal_kind': 'CHECK_APP_FAIL',
            'detail_digest': 'persist-digest',
        })
        self.store.upsert_ticket(make_ticket('T-PERSIST', 'F-PERSIST'))
        self.store.create_candidate(make_candidate('C-PERSIST', proposed_by='S3'))
        self.store.approve_candidate('C-PERSIST', 'durai-human-lead')
        self.store.record_evaluation({
            'eval_id': 'EV-PERSIST',
            'candidate_id': 'C-PERSIST',
            'baseline_episode_id': 'EP-PERSIST',
            'delta_pass_rate': 0.1,
            'delta_calls': -1,
            'delta_tool_errors': 0,
            'delta_list_cost_usd': -0.0002,
            'decision': 'PASS_READY',
        })
        self.store.upsert_route_stats({
            'component_category': 'api',
            'shell_profile': 'py-fastapi-next',
            'model_id': 'DeepSeek-V4.1-Flash',
            'provider': 'deepinfra',
            'pack_version': '1.0.0',
            'episodes_count': 3,
            'finish_rate': 1.0,
            'avg_calls': 4.0,
            'avg_list_cost_usd': 0.001,
            'mean_reward': 0.9,
        })

        restarted = PostgresRLStore(type(self).connect)
        self.assertEqual(0.82, restarted.get_episode('EP-PERSIST')['reward_score'])
        self.assertEqual(1, len(restarted.list_signals('EP-PERSIST')))
        self.assertEqual(['F-PERSIST'], [t['fingerprint'] for t in restarted.list_tickets('OPEN')])
        approved = restarted.list_candidates('APPROVED')
        self.assertEqual(['C-PERSIST'], [c['candidate_id'] for c in approved])
        self.assertEqual('durai-human-lead', approved[0]['approved_by'])
        stats = restarted.get_route_stats('api', 'py-fastapi-next')
        self.assertEqual(1, len(stats))
        self.assertEqual(0.9, stats[0]['mean_reward'])


if __name__ == '__main__':
    unittest.main()
