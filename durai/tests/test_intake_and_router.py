"""Sample: Post signed ticket T-1 twice via TestClient, verify 401/400/413 rejections, test SQL views in pg_support, and verify select_route."""
import json
from pathlib import Path
import unittest

from fastapi.testclient import TestClient

from durai.intake_api import compute_signature, create_intake_app
from durai.model_router import select_route
from durai.rl_store import InMemoryRLStore
import tests.pg_support as pg_support

ROOT = Path(__file__).resolve().parents[1]
HMAC_SECRET = 'intake-test-hmac-secret-v1'
FAKE_SK = 'sk-' + 'b' * 32
FAKE_GOOGLE = 'AIza' + 'A' * 35


def _valid_ticket(**overrides) -> dict:
    # Sample: Return valid PUBLIC_SHELL ticket payload for fingerprint F-1.
    payload = {
        'fingerprint': 'fp-web-cookie-001',
        'source_mode': 'PUBLIC_SHELL',
        'client_instance_hash': 'inst-abc123',
        'pack_id': 'durai.starter',
        'pack_version': '1.0.0',
        'shell_version': '1.0.0',
        'category': 'WEB_RULE_GAP',
        'severity': 'HIGH',
        'summary': 'GET_CHANGES_COOKIE triggered on prefetch request',
        'sanitized_payload': {'rule_id': 'PREFETCH_CHANGED_COOKIE', 'signal_kind': 'GET_CHANGES_COOKIE'},
    }
    payload.update(overrides)
    return payload


class IntakeApiTests(unittest.TestCase):
    def setUp(self):
        # Sample: Initialize InMemoryRLStore and TestClient with HMAC_SECRET and max_bytes 1024.
        self.store = InMemoryRLStore()
        self.app = create_intake_app(self.store, hmac_secret=HMAC_SECRET, max_bytes=1024)
        self.client = TestClient(self.app)

    def _post_signed(self, payload: dict, secret: str = HMAC_SECRET):
        # Sample: Serialize payload JSON, sign raw bytes, and POST to /v1/tickets.
        raw = json.dumps(payload).encode('utf-8')
        sig = compute_signature(raw, secret)
        return self.client.post(
            '/v1/tickets',
            content=raw,
            headers={'Content-Type': 'application/json', 'X-Durai-Signature': sig},
        )

    def test_health_endpoint(self):
        # Sample: GET /v1/health returns HTTP 200 with status ok.
        reply = self.client.get('/v1/health')
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.json(), {'status': 'ok'})

    def test_valid_signed_ticket_intake_and_duplicate_increment(self):
        # Sample: Post signed ticket fp-web-cookie-001 twice. Check count 1 then 2 and one PUBLIC_SHELL row.
        first = self._post_signed(_valid_ticket())
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.json()['occurrence_count'], 1)
        self.assertEqual(first.json()['source_mode'], 'PUBLIC_SHELL')

        second = self._post_signed(_valid_ticket())
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()['occurrence_count'], 2)

        stored = self.store.list_tickets()
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]['occurrence_count'], 2)
        self.assertEqual(stored[0]['source_mode'], 'PUBLIC_SHELL')

    def test_bad_or_missing_signature_rejected(self):
        # Sample: Post without signature or with wrong signature. Check 401 and zero stored tickets.
        raw = json.dumps(_valid_ticket()).encode('utf-8')
        missing = self.client.post('/v1/tickets', content=raw, headers={'Content-Type': 'application/json'})
        self.assertIn(missing.status_code, (401, 403))

        wrong = self._post_signed(_valid_ticket(), secret='wrong-secret')
        self.assertIn(wrong.status_code, (401, 403))
        self.assertEqual(len(self.store.list_tickets()), 0)

    def test_secret_and_raw_code_leaks_rejected_with_400(self):
        # Sample: Post payloads with API keys, file paths, raw code markers, or source_code field. Check 400 and zero rows.
        bad_cases = [
            _valid_ticket(summary='Crash with key ' + FAKE_SK),
            _valid_ticket(sanitized_payload={'trace': FAKE_GOOGLE}),
            _valid_ticket(summary='Failed at C:\\Users\\alice\\project\\app.py'),
            _valid_ticket(summary='Failed at /Users/alice/workspace/main.py'),
            _valid_ticket(summary='Code snippet:\n```python\ndef run(): pass\n```'),
            _valid_ticket(sanitized_payload={'snippet': 'from fastapi import FastAPI'}),
            _valid_ticket(summary='export async function POST(req) { return null; }'),
            _valid_ticket(source_code='print(1)'),
            _valid_ticket(source_mode='LOCAL_DURAI'),
        ]
        for payload in bad_cases:
            reply = self._post_signed(payload)
            self.assertEqual(reply.status_code, 400, f'Expected 400 for {payload}, got {reply.status_code}: {reply.text}')
        self.assertEqual(len(self.store.list_tickets()), 0)

    def test_oversized_payload_rejected_with_413(self):
        # Sample: Post signed payload larger than max_bytes 1024. Check 413 and zero stored tickets.
        oversized = _valid_ticket(summary='tool friction summary ' + ('x' * 1500))
        reply = self._post_signed(oversized)
        self.assertIn(reply.status_code, (400, 413))
        self.assertEqual(reply.status_code, 413)
        self.assertEqual(len(self.store.list_tickets()), 0)


class ModelRouterTests(unittest.TestCase):
    def test_select_route_prefers_highest_mean_reward_then_lowest_cost(self):
        # Sample: Seed 3 routes for ui. Exclude 0.70 finish_rate route, break reward tie by lower avg_list_cost_usd.
        store = InMemoryRLStore()
        store.upsert_route_stats({
            'component_category': 'ui',
            'shell_profile': 'py-fastapi-next',
            'model_id': 'unstable-high-reward',
            'provider': 'custom',
            'pack_version': '1.0.0',
            'episodes_count': 10,
            'finish_rate': 0.70,
            'avg_list_cost_usd': 0.01,
            'mean_reward': 1.49,
        })
        store.upsert_route_stats({
            'component_category': 'ui',
            'shell_profile': 'py-fastapi-next',
            'model_id': 'claude-sonnet',
            'provider': 'anthropic',
            'pack_version': '1.0.0',
            'episodes_count': 10,
            'finish_rate': 0.95,
            'avg_list_cost_usd': 0.45,
            'mean_reward': 1.35,
        })
        store.upsert_route_stats({
            'component_category': 'ui',
            'shell_profile': 'py-fastapi-next',
            'model_id': 'DeepSeek-V4.1-Flash',
            'provider': 'deepinfra',
            'pack_version': '1.0.0',
            'episodes_count': 10,
            'finish_rate': 0.95,
            'avg_list_cost_usd': 0.03,
            'mean_reward': 1.35,
        })

        picked = select_route(store, 'ui', shell_profile='py-fastapi-next', min_finish_rate=0.90)
        self.assertEqual(picked['source'], 'model_route_stats')
        self.assertEqual(picked['model_id'], 'DeepSeek-V4.1-Flash')
        self.assertEqual(picked['provider'], 'deepinfra')

    def test_select_route_falls_back_to_model_pricing(self):
        # Sample: Query empty store and low-finish store. Check fallback returns cheapest non-fake model_pricing route.
        store = InMemoryRLStore()
        fallback = select_route(store, 'payments', shell_profile='py-fastapi-next', min_finish_rate=0.90)
        self.assertEqual(fallback['source'], 'model_pricing_fallback')
        self.assertEqual(fallback['provider'], 'deepseek')
        self.assertNotEqual(fallback['provider'], 'fake')

        store.upsert_route_stats({
            'component_category': 'payments',
            'shell_profile': 'py-fastapi-next',
            'model_id': 'failing-model',
            'provider': 'openai',
            'pack_version': '1.0.0',
            'episodes_count': 5,
            'finish_rate': 0.40,
            'avg_list_cost_usd': 0.25,
            'mean_reward': 0.20,
        })
        fallback2 = select_route(store, 'payments', shell_profile='py-fastapi-next', min_finish_rate=0.90)
        self.assertEqual(fallback2['source'], 'model_pricing_fallback')
        self.assertEqual(fallback2['provider'], 'deepseek')


@unittest.skipUnless(
    pg_support.docker_ready() and pg_support.image_ready(pg_support.IMAGE),
    'Docker or postgres:17-alpine image is unavailable',
)
class SupersetViewsPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Start disposable postgres:17-alpine container and apply rl_0001 and rl_superset_views SQL files.
        cls.connect = staticmethod(pg_support.start_postgres())
        base_sql = (ROOT / 'migrations' / 'improvement' / 'rl_0001.sql.proposed').read_text(encoding='utf-8')
        views_sql = (ROOT / 'migrations' / 'improvement' / 'rl_superset_views.sql.proposed').read_text(encoding='utf-8')
        with cls.connect() as conn:
            conn.execute(base_sql)
            conn.execute(views_sql)
            conn.commit()

    @classmethod
    def tearDownClass(cls):
        # Sample: Stop and remove disposable postgres container.
        pg_support.stop_postgres()

    def test_superset_views_query_results(self):
        # Sample: Insert 1 episode, 1 ticket, and 2 route stats rows. Query all 3 Superset views and check aggregates.
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO durai_improvement.execution_episode (
                    episode_id, run_id, sample_id, component_category, shell_profile,
                    model_id, provider, effective_view_digest, stop_reason, calls_used,
                    input_tokens, output_tokens, list_cost_usd, enterprise_cost_usd,
                    passed_checks, failed_checks, tool_error_count, stall_nudges,
                    smell_high, smell_low, reward_score
                ) VALUES (
                    'EP-1', 'RUN-1', 'S-CHK', 'ui', 'py-fastapi-next',
                    'DeepSeek-V4.1-Flash', 'deepinfra', 'digest-1', 'FINISHED', 12,
                    10000, 2000, 0.003200, 0.040000,
                    18, 0, 0, 0, 0, 1, 1.4800
                )
                """
            )
            conn.execute(
                """
                INSERT INTO durai_improvement.ticket (
                    ticket_id, fingerprint, source_mode, client_instance_hash,
                    pack_id, pack_version, shell_version, category, severity,
                    summary, sanitized_payload, occurrence_count, status
                ) VALUES (
                    'T-1', 'fp-1', 'PUBLIC_SHELL', 'inst-1',
                    'durai.starter', '1.0.0', '1.0.0', 'WEB_RULE_GAP', 'HIGH',
                    'Prefetch cookie rule gap', '{}'::jsonb, 3, 'OPEN'
                )
                """
            )
            conn.execute(
                """
                INSERT INTO durai_improvement.model_route_stats (
                    component_category, shell_profile, model_id, provider, pack_version,
                    episodes_count, finish_rate, avg_calls, avg_list_cost_usd, mean_reward
                ) VALUES
                    ('ui', 'py-fastapi-next', 'DeepSeek-V4.1-Flash', 'deepinfra', '1.0.0', 10, 0.9500, 11.50, 0.020000, 1.4000),
                    ('ui', 'py-fastapi-next', 'claude-sonnet', 'anthropic', '1.0.0', 10, 0.9600, 9.00, 0.400000, 1.3800)
                """
            )
            conn.commit()

            backlog = conn.execute('SELECT ticket_id, occurrence_count, is_open FROM durai_improvement.rl_ticket_backlog').fetchall()
            self.assertEqual(len(backlog), 1)
            self.assertEqual(backlog[0], ('T-1', 3, True))

            trend = conn.execute('SELECT component_category, episodes_count, finished_count, total_list_cost_usd FROM durai_improvement.rl_episode_cost_trend').fetchall()
            self.assertEqual(len(trend), 1)
            self.assertEqual(trend[0][0:3], ('ui', 1, 1))

            board = conn.execute(
                'SELECT model_id, route_rank FROM durai_improvement.rl_route_leaderboard ORDER BY route_rank ASC'
            ).fetchall()
            self.assertEqual(board, [('DeepSeek-V4.1-Flash', 1), ('claude-sonnet', 2)])


if __name__ == '__main__':
    unittest.main()
