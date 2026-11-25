"""Sample: Load rubric revision 3 and the UI_UX skill into durai_test. Refuse a UI_UX score of 3 for a stdlib front end and accept it for Next.js with TypeScript."""
import json
from pathlib import Path
import unittest

from durai.common import uid
from durai.db import transaction

from test_app_benchmark import DATABASE, DIGEST, AppBase  # noqa: E402
from tools.build_review import audit_skill, facts_for, load_rubric, load_skills  # noqa: E402

DESIGN_FOLDER = Path(__file__).parents[1] / 'design/review'


class UiGateTests(AppBase):
    @classmethod
    def setUpClass(cls):
        # Sample: Prepare durai_test, load rubric revision 3 and the UI_UX skill. Load them again and read unchanged 1 and 1.
        super().setUpClass()
        load_rubric(DESIGN_FOLDER / 'rubric-v3.json', DATABASE)
        load_skills(DESIGN_FOLDER / 'skills-v3.json', DATABASE)

    def ui_attempt(self, stack):
        # Sample: Insert app trade-pilot with component trader-screens and one UI_UX build with frontend_stack NONE. Return the key and attempt id.
        key = self.key()
        self.app(key)
        self.component(key, component='trader-screens', category='ui-workflow')
        attempt_id = 'A-' + uid()[:10]
        with transaction('durai_evaluator', DATABASE) as db:
            db.execute("INSERT INTO durai_bench.build_attempt(attempt_id,app_key,app_version,scope,component_key,model_profile_id,request_digest,frontend_stack)"
                       " VALUES(%s,%s,'E6','COMPONENT','trader-screens',%s,%s,%s)", (attempt_id, key, self.profile(), DIGEST, stack))
            db.execute("INSERT INTO durai_bench.build_stage(attempt_id,stage,state) VALUES(%s,'UI_UX','DONE')", (attempt_id,))
        return key, attempt_id

    def ui_score(self, key, attempt_id, score):
        # Sample: Insert UI_UX score 3 for trader-screens from reviewer deepseek-flash-reviewer under rubric revision 3.
        with transaction('durai_evaluator', DATABASE) as db:
            db.execute("INSERT INTO durai_bench.build_score(attempt_id,stage,app_key,app_version,component_key,reviewer_profile,score,baseline_score,evidence,rubric_revision,prompt_digest)"
                       " VALUES(%s,'UI_UX',%s,'E6','trader-screens','deepseek-flash-reviewer',%s,NULL,'Stack and screens checked',%s,%s)",
                       (attempt_id, key, score, '3', DIGEST))

    def test_ui_ux_above_one_refused_without_nextjs(self):
        # Sample: Attempt with frontend_stack NONE. Insert UI_UX score 3. Read a refusal. Insert UI_UX score 1. Read one row.
        key, attempt_id = self.ui_attempt('NONE')
        with self.assertRaisesRegex(Exception, 'not Next.js with TypeScript'):
            self.ui_score(key, attempt_id, 3)
        self.ui_score(key, attempt_id, 1)
        with transaction('durai_evaluator', DATABASE) as db:
            rows = db.execute("SELECT score FROM durai_bench.build_score WHERE attempt_id=%s AND stage='UI_UX'", (attempt_id,)).fetchall()
        self.assertEqual([row['score'] for row in rows], [1])

    def test_ui_ux_accepted_with_nextjs(self):
        # Sample: Attempt with frontend_stack NEXTJS_TS. Insert UI_UX score 3. Read score 3.
        key, attempt_id = self.ui_attempt('NEXTJS_TS')
        self.ui_score(key, attempt_id, 3)
        with transaction('durai_evaluator', DATABASE) as db:
            row = db.execute("SELECT score FROM durai_bench.build_score WHERE attempt_id=%s AND stage='UI_UX'", (attempt_id,)).fetchone()
        self.assertEqual(row['score'], 3)

    def test_rubric_v3_has_ui_stage(self):
        # Sample: Read the review_rubric rows of stage UI_UX. Read R-UI-UX revision 3 with five levels, status ACTIVE.
        with transaction('durai_evaluator', DATABASE) as db:
            rows = db.execute("SELECT rubric_id, revision, status, jsonb_object_keys(levels) AS level FROM durai_bench.review_rubric WHERE stage='UI_UX'").fetchall()
        self.assertEqual({row['rubric_id'] for row in rows}, {'R-UI-UX'})
        self.assertEqual({row['revision'] for row in rows}, {3})
        self.assertEqual({row['status'] for row in rows}, {'ACTIVE'})
        self.assertEqual(sorted(row['level'] for row in rows), ['0', '1', '2', '3', '4'])

    def test_skill_requires_stack_fact(self):
        # Sample: Skill S-REVIEW-UI-UX without "at most 1" fails the audit and names the fact. The unchanged skill passes.
        skill = json.loads((DESIGN_FOLDER / 'skills-v3.json').read_text(encoding='utf-8'))['skills'][0]
        broken = dict(skill, prompt_text=skill['prompt_text'].replace('at most 1', 'at most two'))
        with self.assertRaisesRegex(ValueError, 'at most 1'):
            audit_skill(broken, facts_for('UI_UX'))
        audit_skill(skill, facts_for('UI_UX'))


if __name__ == '__main__':
    unittest.main()
