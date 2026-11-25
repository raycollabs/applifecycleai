"""Sample: Record a synthetic FINISHED receipt with DESIGN and PLAN for trade-pilot component booking in durai_test. Read 1 attempt and 2 stages. Refuse a second record and a STOP_BUDGET receipt."""
from durai.common import Conflict, uid
from durai.db import transaction
from durai.bench_store import add_profile

from test_app_benchmark import DATABASE, DIGEST, AppBase, sample_row  # noqa: E402
from tools.record_blind_build import MODEL_PROFILE, record_receipt  # noqa: E402


def receipt(sample_id, stop='FINISHED'):
    # Sample: Return a receipt with DESIGN at digest a, PLAN at digest b, cost 2.5 and stop FINISHED.
    return {'sampleId': sample_id, 'revision': 1, 'digest': DIGEST, 'startedAt': '2026-11-23T02:07:00+00:00',
            'finishedAt': '2026-11-23T02:11:00+00:00', 'estimatedEnterpriseCost': 2.5, 'stopReason': stop,
            'stages': [{'stage': 'DESIGN', 'paths': ['docs/design.md'], 'sha256s': [DIGEST]},
                       {'stage': 'PLAN', 'paths': ['docs/plan.md'], 'sha256s': ['b' * 64]}]}


class RecordBlindBuildTests(AppBase):
    def setUp(self):
        # Sample: Insert trade-pilot version E6 with component booking and sample S-REC-1a2b3c. Add the model profile.
        super().setUp()
        self.app_key = self.key()
        self.app(self.app_key)
        self.component(self.app_key, component='booking')
        self.sample_id = 'S-REC-' + uid()[:6]
        with transaction('durai_evaluator', DATABASE) as db:
            sample_row(db, self.sample_id, text='Book a trade once.')
        add_profile(MODEL_PROFILE, 'deepseek', 'deepseek-flash', 'direct', database=DATABASE)

    def test_record_writes_attempt_and_stages(self):
        # Sample: Record attempt A-REC with frontend NONE. Read one attempt row and two DONE stages.
        attempt = 'A-REC-' + uid()[:6]
        result = record_receipt(receipt(self.sample_id), attempt, self.app_key, 'E6', 'booking', 'NONE', 'abc1234', database=DATABASE)
        self.assertEqual(result['stages'], 2)
        with transaction('durai_evaluator', DATABASE) as db:
            row = db.execute('SELECT frontend_stack, cost FROM durai_bench.build_attempt WHERE attempt_id=%s', (attempt,)).fetchone()
            stages = db.execute("SELECT stage, state FROM durai_bench.build_stage WHERE attempt_id=%s ORDER BY stage", (attempt,)).fetchall()
        self.assertEqual(row['frontend_stack'], 'NONE')
        self.assertEqual([(item['stage'], item['state']) for item in stages], [('DESIGN', 'DONE'), ('PLAN', 'DONE')])

    def test_second_record_is_refused(self):
        # Sample: Record attempt A-DUP once. Record it again and read a refusal naming A-DUP.
        attempt = 'A-DUP-' + uid()[:6]
        record_receipt(receipt(self.sample_id), attempt, self.app_key, 'E6', 'booking', 'NONE', 'abc1234', database=DATABASE)
        with self.assertRaisesRegex(Conflict, attempt):
            record_receipt(receipt(self.sample_id), attempt, self.app_key, 'E6', 'booking', 'NONE', 'abc1234', database=DATABASE)

    def test_budget_stop_is_refused(self):
        # Sample: Receipt with stopReason STOP_BUDGET. Read a refusal that names STOP_BUDGET and no attempt row.
        attempt = 'A-STOP-' + uid()[:6]
        with self.assertRaisesRegex(ValueError, 'STOP_BUDGET'):
            record_receipt(receipt(self.sample_id, stop='STOP_BUDGET'), attempt, self.app_key, 'E6', 'booking', 'NONE', 'abc1234', database=DATABASE)
        with transaction('durai_evaluator', DATABASE) as db:
            self.assertIsNone(db.execute('SELECT 1 FROM durai_bench.build_attempt WHERE attempt_id=%s', (attempt,)).fetchone())


if __name__ == '__main__':
    import unittest
    unittest.main()
