"""Sample: Run full Wave 2 RL closed loop on disposable PostgreSQL 17: collect friction, intake public ticket, triage, evaluate, human-approve, build signed pack, sync EffectiveView, and route."""
import hashlib
import hmac
import json
from pathlib import Path
import sys
import tempfile
import unittest

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pg_support  # noqa: E402
from durai.adapters.rl_store.postgres import PostgresRLStore  # noqa: E402
from durai.effective_view import EffectiveView  # noqa: E402
from durai.intake_api import create_intake_app  # noqa: E402
from durai.model_router import select_route  # noqa: E402
from durai.pack_registry import PackRegistry  # noqa: E402
from durai.pack_signing import TrustedKeyList, generate_key_pair  # noqa: E402
from durai.policy_eval import evaluate_candidate, promote_candidate  # noqa: E402
from durai.ticket_collector import collect_run_feedback  # noqa: E402
from tools.build_pack import build_starter_pack  # noqa: E402
from tools.product_review import review_product  # noqa: E402
from tools.triage_tickets import triage_open_tickets  # noqa: E402


class RLEndToEndLoopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Start disposable PostgreSQL 17 container when Docker is ready.
        if not pg_support.docker_ready():
            raise unittest.SkipTest('Docker is not available')
        cls.connect = staticmethod(pg_support.start_postgres())

    @classmethod
    def tearDownClass(cls):
        # Sample: Remove disposable PostgreSQL 17 container cleanly.
        pg_support.stop_postgres()

    def test_closed_loop_collect_triage_approve_pack_and_route(self):
        # Sample: Record failing run + P-REV defect + public intake ticket -> triage -> PASS_READY eval -> human approve -> compile starter pack -> PackFeedClient sync -> EffectiveView + ModelRouter.
        rl_store = PostgresRLStore(self.connect)
        rl_store.ensure_schema()

        with self.connect() as conn:
            conn.execute(
                'TRUNCATE TABLE durai_improvement.episode_signal, durai_improvement.policy_evaluation, '
                'durai_improvement.policy_candidate, durai_improvement.ticket, '
                'durai_improvement.execution_episode, durai_improvement.model_route_stats '
                'RESTART IDENTITY CASCADE'
            )

        priv, pub = generate_key_pair()
        keys = TrustedKeyList()
        keys.add_key('k-e2e', pub)
        registry = PackRegistry(trusted_keys=keys)
        view = EffectiveView(registry)

        starter_v1 = build_starter_pack(priv, key_id='k-e2e', version='1.0.0')
        registry.install(starter_v1)
        self.assertIsNone(view.get_record('lesson.C-DUMMY'))

        baseline_receipt = {
            'runId': 'RUN-BASE-1',
            'sampleId': 'S-CHK-SCREENS',
            'stopReason': 'STOP_STALL',
            'calls': 45,
            'inputTokens': 200000,
            'outputTokens': 15000,
            'estimatedListCost': 0.12,
            'estimatedEnterpriseCost': 0.55,
            'lastRunTests': {'passed': 4, 'failed': 2},
            'toolErrors': {'edit_file': 3},
            'model': 'deepseek-chat',
            'provider': 'deepseek',
        }
        smells = [{'kind': 'RAW_SQL_FORMAT', 'severity': 'HIGH'}]
        web_rules = [{'rule': 'GET_CHANGES_COOKIE'}]
        base_ep = collect_run_feedback(
            rl_store,
            baseline_receipt,
            smells=smells,
            web_rules=web_rules,
            component_category='ui',
            effective_view_digest='ev-v1',
        )['episode']

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            review_product(
                'http://127.0.0.1:1',
                ['Checkout screen answers 200'],
                project='chk-broken',
                evidence_folder=tmp / 'ev',
                receipts_folder=tmp / 'rec',
                browser_runner=lambda url, d: {'screenshots': [], 'html': 'Signed out'},
                rl_store=rl_store,
            )

            app = create_intake_app(rl_store, hmac_secret='sec-e2e')
            client = TestClient(app)
            pub_payload = {
                'ticket_id': 'PUB-1',
                'fingerprint': rl_store.list_tickets('OPEN')[0]['fingerprint'],
                'source_mode': 'PUBLIC_SHELL',
                'pack_id': 'durai.starter',
                'pack_version': '1.0.0',
                'category': 'UI_UX_DEFECT',
                'severity': 'HIGH',
                'summary': 'Same signed-out defect reported from public shell',
                'sanitized_payload': {'rule': 'GET_CHANGES_COOKIE'},
            }
            raw = json.dumps(pub_payload).encode('utf-8')
            sig = hmac.new(b'sec-e2e', raw, hashlib.sha256).hexdigest()
            resp = client.post('/v1/tickets', content=raw, headers={'Content-Type': 'application/json', 'X-Durai-Signature': sig})
            self.assertEqual(resp.status_code, 200)
            self.assertGreaterEqual(resp.json()['occurrence_count'], 2)

            candidates = triage_open_tickets(rl_store, proposed_by='durai-triage-agent')
            self.assertGreaterEqual(len(candidates), 1)
            cid = candidates[0]['candidate_id']

            with self.assertRaises(PermissionError):
                promote_candidate(rl_store, cid, 'durai-triage-agent')

            treatment_receipt = {
                'runId': 'RUN-TREAT-1',
                'sampleId': 'S-CHK-SCREENS',
                'stopReason': 'FINISHED',
                'calls': 18,
                'inputTokens': 80000,
                'outputTokens': 8000,
                'estimatedListCost': 0.04,
                'estimatedEnterpriseCost': 0.24,
                'lastRunTests': {'passed': 12, 'failed': 0},
                'toolErrors': {},
                'model': 'deepseek-chat',
                'provider': 'deepseek',
            }
            treat_ep = collect_run_feedback(rl_store, treatment_receipt, component_category='ui', effective_view_digest='ev-v2')['episode']

            ev = evaluate_candidate(rl_store, cid, base_ep, treat_ep)
            self.assertEqual(ev['decision'], 'PASS_READY')

            promoted = promote_candidate(rl_store, cid, 'operator', require_pass_ready=True)
            self.assertEqual(promoted['status'], 'APPROVED')

            starter_v2 = build_starter_pack(priv, key_id='k-e2e', approved_lessons=[promoted], version='1.0.1')
            installed = registry.install(starter_v2)
            self.assertEqual(installed['state'], 'INSTALLED')
            self.assertIsNotNone(view.get_record(f'lesson.{cid}'))

            rl_store.upsert_route_stats({
                'component_category': 'ui',
                'shell_profile': 'py-fastapi-next',
                'model_id': 'deepseek-chat',
                'provider': 'deepseek',
                'pack_version': '1.0.1',
                'episodes_count': 2,
                'finish_rate': 0.95,
                'avg_calls': 18.0,
                'avg_list_cost_usd': 0.04,
                'mean_reward': 1.25,
            })
            best = select_route(rl_store, 'ui', 'py-fastapi-next', min_finish_rate=0.90)
            self.assertEqual(best['source'], 'model_route_stats')
            self.assertEqual(best['model_id'], 'deepseek-chat')


if __name__ == '__main__':
    unittest.main()
