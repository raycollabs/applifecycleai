"""Sample: Load the rubric and skills into durai_test. Record goal checks and reviewer scores for trade-booking. Check the refusals, the agreement view, the report and the role grants."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import psycopg
from psycopg.types.json import Jsonb
from durai.common import uid
from durai.db import connect, transaction
from langchain_core.messages import AIMessage

from test_app_benchmark import DATABASE, DIGEST, AppBase, as_builder, ensure_rubrics, sample_row  # noqa: E402
from tools.build_review import load_rubric, load_skills, record_checks, record_scores, report, review_deepseek  # noqa: E402

DESIGN_FOLDER = Path(__file__).parents[1] / 'design/review'

GOALS = ['The same trade id books once', 'A rejected trade leaves no lot', 'Each lot shows its desk']
STAGE_NAMES = ('DESIGN', 'PLAN', 'IMPLEMENTATION', 'TESTS')
REQUEST = 'Book equity, futures and option trades for my desks. Never book a trade twice.'


class ReviewBase(AppBase):
    def build(self, goals=GOALS):
        # Sample: Insert app trade-pilot E6, component booking with 3 hidden goals, and an attempt with DESIGN, PLAN, IMPLEMENTATION and TESTS DONE. Return key and attempt id.
        key = self.key()
        self.app(key)
        self.component(key)
        sample_id = 'S-TRADE-BOOK-' + uid()[:6]
        with transaction('durai_evaluator', DATABASE) as db:
            sample_row(db, sample_id, text=REQUEST)
            db.execute('INSERT INTO durai_bench.component_goal(app_key,app_version,component_key,sample_id,sample_revision,sample_digest,hidden_goals)'
                       ' VALUES(%s,%s,%s,%s,1,%s,%s)', (key, 'E6', 'booking', sample_id, DIGEST, Jsonb(goals)))
        attempt_id = self.attempt(key, self.profile())
        with transaction('durai_evaluator', DATABASE) as db:
            for stage in ('DESIGN', 'PLAN', 'TESTS'):
                db.execute("INSERT INTO durai_bench.build_stage(attempt_id,stage,state,artifacts) VALUES(%s,%s,'DONE',%s)",
                           (attempt_id, stage, Jsonb([{'path': 'docs/' + stage.lower() + '.md', 'sha256': DIGEST}])))
        return key, attempt_id

    def insert_check(self, attempt_id, key, index, verdict='PASS', overlap=False):
        # Sample: Insert an EXECUTED check for goal 0 of booking with verdict PASS and check file digest.
        with transaction('durai_evaluator', DATABASE) as db:
            db.execute("INSERT INTO durai_bench.goal_check(attempt_id,app_key,app_version,component_key,goal_index,verdict,method,check_path,"
                       "check_sha256,output_sha256,guidance_overlap,reviewer) VALUES(%s,%s,'E6','booking',%s,%s,'EXECUTED','checks/c.py',%s,%s,%s,'opus-reviewer')",
                       (attempt_id, key, index, verdict, DIGEST, DIGEST, overlap))


class ReviewStoreTests(ReviewBase):
    def test_rubric_revision_is_immutable(self):
        # Sample: Insert rubric R-DESIGN revision 1. Update its levels. Check refusal. Update its status to ACTIVE. Check accepted.
        ensure_rubrics()
        with self.assertRaises(psycopg.errors.IntegrityConstraintViolation):
            with connect('postgres', DATABASE) as admin:
                admin.execute("UPDATE durai_bench.review_rubric SET levels=jsonb_set(levels,'{4}','\"Anything\"') WHERE rubric_id='R-DESIGN' AND revision=1")
        with transaction('durai_evaluator', DATABASE) as db:
            db.execute("UPDATE durai_bench.review_rubric SET status='ACTIVE' WHERE rubric_id='R-DESIGN' AND revision=1")
            row = db.execute("SELECT status FROM durai_bench.review_rubric WHERE rubric_id='R-DESIGN' AND revision=1").fetchone()
        self.assertEqual(row['status'], 'ACTIVE')

    def test_score_needs_known_rubric(self):
        # Sample: Insert a score for trade-booking DESIGN with rubric revision 9. Check refusal and 0 rows. Insert it with revision r1. Check 1 row.
        key, attempt_id = self.build()
        with self.assertRaisesRegex(psycopg.errors.IntegrityConstraintViolation, 'not loaded'):
            with transaction('durai_evaluator', DATABASE) as db:
                db.execute("INSERT INTO durai_bench.build_score(attempt_id,stage,app_key,app_version,component_key,reviewer_profile,score,evidence,rubric_revision,prompt_digest)"
                           " VALUES(%s,'DESIGN',%s,'E6','booking','opus-reviewer',3,'docs/design.md:4','r9',%s)", (attempt_id, key, DIGEST))
        with transaction('durai_evaluator', DATABASE) as db:
            self.assertEqual(db.execute('SELECT count(*) AS n FROM durai_bench.build_score WHERE attempt_id=%s', (attempt_id,)).fetchone()['n'], 0)
            db.execute("INSERT INTO durai_bench.build_score(attempt_id,stage,app_key,app_version,component_key,reviewer_profile,score,evidence,rubric_revision,prompt_digest)"
                       " VALUES(%s,'DESIGN',%s,'E6','booking','opus-reviewer',3,'docs/design.md:4','r1',%s)", (attempt_id, key, DIGEST))
            self.assertEqual(db.execute('SELECT count(*) AS n FROM durai_bench.build_score WHERE attempt_id=%s', (attempt_id,)).fetchone()['n'], 1)

    def test_agreement_flags_large_difference(self):
        # Sample: Insert reviewer A score 4 and reviewer B score 2 for trade-booking IMPLEMENTATION. Check NEEDS_ANCHOR. Insert anchor mark 3. Check ANCHORED.
        key, attempt_id = self.build()
        self.score(attempt_id, key, 'booking', 4, reviewer='opus-reviewer')
        self.score(attempt_id, key, 'booking', 2, reviewer='deepseek-flash-reviewer')
        query = "SELECT score_a, score_b, difference, flag, anchor_score FROM durai_bench.reviewer_agreement WHERE attempt_id=%s AND stage='IMPLEMENTATION'"
        with transaction('durai_evaluator', DATABASE) as db:
            row = db.execute(query, (attempt_id,)).fetchone()
            self.assertEqual((row['score_a'], row['score_b'], row['difference'], row['flag']), (4, 2, 2, 'NEEDS_ANCHOR'))
            db.execute("INSERT INTO durai_bench.anchor_mark(attempt_id,stage,score,note) VALUES(%s,'IMPLEMENTATION',3,'Reviewed by user')", (attempt_id,))
            row = db.execute(query, (attempt_id,)).fetchone()
        self.assertEqual((row['flag'], row['anchor_score']), ('ANCHORED', 3))

    def test_two_rubric_revisions_coexist(self):
        # Sample: Insert reviewer score 3 under rubric r1 and score 4 under rubric r2 for one attempt and stage. Check both rows stay and reviewer_agreement reads 4.
        key, attempt_id = self.build()
        data = json.loads((Path(__file__).parents[1] / 'design/review/rubric-v2.json').read_text(encoding='utf-8'))
        with transaction('durai_evaluator', DATABASE) as db:
            for item in data['rubrics']:
                text = json.dumps({'stage': item['stage'], 'levels': item['levels']}, sort_keys=True, separators=(',', ':'))
                db.execute('INSERT INTO durai_bench.review_rubric(rubric_id,revision,stage,levels,digest) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                           (item['rubric_id'], data['revision'], item['stage'], Jsonb(item['levels']), hashlib.sha256(text.encode('utf-8')).hexdigest()))
        self.score(attempt_id, key, 'booking', 3, reviewer='opus-reviewer', rubric='r1')
        self.score(attempt_id, key, 'booking', 4, reviewer='opus-reviewer', rubric='r2')
        with transaction('durai_evaluator', DATABASE) as db:
            kept = db.execute('SELECT count(*) AS n FROM durai_bench.build_score WHERE attempt_id=%s', (attempt_id,)).fetchone()['n']
            row = db.execute("SELECT score_a FROM durai_bench.reviewer_agreement WHERE attempt_id=%s AND stage='IMPLEMENTATION'", (attempt_id,)).fetchone()
        self.assertEqual((kept, row['score_a']), (2, 4))


class FakeReviewer:
    def __init__(self, replies):
        # Sample: Hold 2 replies. Reply 1 reads docs/design.md. Reply 2 gives 4 scores. Keep the messages seen.
        self.replies, self.seen = list(replies), []

    def bind_tools(self, specs):
        # Sample: Accept 2 tool specs. Return the same model.
        return self

    def invoke(self, messages):
        # Sample: Return the next reply with 1000 input and 200 output tokens.
        self.seen.append(list(messages))
        return self.replies.pop(0)


class ReviewToolTests(ReviewBase):
    def setUp(self):
        # Sample: Open a temp folder. Load rubric and skills into durai_test. Return 4 and 4 rows.
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        load_rubric(DESIGN_FOLDER / 'rubric-v1.json', DATABASE)
        load_skills(DESIGN_FOLDER / 'skills-v1.json', DATABASE)

    def write_json(self, name, data):
        # Sample: Write checks.json with 3 records into the temp folder. Return its path.
        path = Path(self.folder.name, name)
        path.write_text(json.dumps(data), encoding='utf-8')
        return path

    def digest(self, stage):
        # Sample: Read the digest of the stored DESIGN skill. Return 64 hex characters.
        with transaction('durai_evaluator', DATABASE) as db:
            return db.execute('SELECT digest FROM durai_bench.review_skill WHERE stage=%s AND revision=1', (stage,)).fetchone()['digest']

    def check_file(self):
        # Sample: Write trade_booking_check.py with 1 line. Return its path and SHA-256.
        path = Path(self.folder.name, 'trade_booking_check.py')
        path.write_bytes(b'print("PASS")\n')
        return str(path), hashlib.sha256(path.read_bytes()).hexdigest()

    def checks(self, attempt_id, verdicts):
        # Sample: Write a checks file with PASS, PASS, FAIL for goals 0, 1 and 2 of attempt A-1. Goal 0 has guidance overlap.
        path, sha = self.check_file()
        return self.write_json('checks.json', {'checks': [
            {'attempt_id': attempt_id, 'goal_index': index, 'verdict': verdict, 'method': 'EXECUTED', 'check_path': path, 'check_sha256': sha,
             'output_sha256': sha, 'guidance_overlap': index == 0, 'reviewer': 'opus-reviewer'} for index, verdict in enumerate(verdicts)]})

    def test_skill_without_required_fact_is_refused(self):
        # Sample: Load skill S-REVIEW-DESIGN without the evidence rule. Check the audit names "cite evidence". Check 0 skills of revision 2 stored.
        data = json.loads((DESIGN_FOLDER / 'skills-v1.json').read_text(encoding='utf-8'))
        data['revision'] = 2
        data['skills'][0]['prompt_text'] = data['skills'][0]['prompt_text'].replace('cite evidence', 'name sources')
        with self.assertRaisesRegex(ValueError, 'cite evidence'):
            load_skills(self.write_json('skills-v2.json', data), DATABASE)
        with transaction('durai_evaluator', DATABASE) as db:
            self.assertEqual(db.execute('SELECT count(*) AS n FROM durai_bench.review_skill WHERE revision=2').fetchone()['n'], 0)

    def test_implementation_score_bounded_by_checks(self):
        # Sample: Record checks PASS, PASS, FAIL for trade-booking. Record IMPLEMENTATION 4. Check refusal. Record IMPLEMENTATION 3. Check accepted.
        key, attempt_id = self.build()
        self.assertEqual(record_checks(self.checks(attempt_id, ['PASS', 'PASS', 'FAIL']), DATABASE), {'recorded': 3})
        item = {'attempt_id': attempt_id, 'stage': 'IMPLEMENTATION', 'reviewer_profile': 'opus-reviewer', 'score': 4, 'baseline_score': 4,
                'evidence': 'check 2 FAIL', 'rubric_revision': 1, 'prompt_digest': self.digest('IMPLEMENTATION')}
        with self.assertRaisesRegex(ValueError, 'FAIL goal check'):
            record_scores(self.write_json('scores4.json', [item]), DATABASE)
        self.assertEqual(record_scores(self.write_json('scores3.json', [{**item, 'score': 3}]), DATABASE), {'recorded': 1})

    def test_scores_refused_without_executed_checks(self):
        # Sample: An attempt has 0 executed checks. Record DESIGN score 1. Check refusal naming zero executed goal checks.
        key, attempt_id = self.build()
        item = {'attempt_id': attempt_id, 'stage': 'DESIGN', 'reviewer_profile': 'opus-reviewer', 'score': 1, 'baseline_score': 1,
                'evidence': 'docs/design.md', 'rubric_revision': 1, 'prompt_digest': self.digest('DESIGN')}
        with self.assertRaisesRegex(ValueError, 'zero executed goal checks'):
            record_scores(self.write_json('scores_no_checks.json', [item]), DATABASE)

    def test_score_three_or_four_refused_without_executed_pass(self):
        # Sample: Record only FAIL executed check. Record DESIGN score 3. Check refusal naming no executed PASS goal checks.
        key, attempt_id = self.build()
        self.insert_check(attempt_id, key, 0, 'FAIL')
        item = {'attempt_id': attempt_id, 'stage': 'DESIGN', 'reviewer_profile': 'opus-reviewer', 'score': 3, 'baseline_score': 3,
                'evidence': 'docs/design.md', 'rubric_revision': 1, 'prompt_digest': self.digest('DESIGN')}
        with self.assertRaisesRegex(ValueError, 'no executed PASS goal checks'):
            record_scores(self.write_json('scores_no_pass.json', [item]), DATABASE)

    def test_check_digest_must_match_file(self):
        # Sample: Record a check with a SHA-256 that differs from the file. Check refusal and 0 checks stored.
        key, attempt_id = self.build()
        data = json.loads(self.checks(attempt_id, ['PASS']).read_text(encoding='utf-8'))
        data['checks'][0]['check_sha256'] = 'f' * 64
        with self.assertRaisesRegex(ValueError, 'differs from the file'):
            record_checks(self.write_json('bad.json', data), DATABASE)
        with transaction('durai_evaluator', DATABASE) as db:
            self.assertEqual(db.execute('SELECT count(*) AS n FROM durai_bench.goal_check WHERE attempt_id=%s', (attempt_id,)).fetchone()['n'], 0)

    def test_report_splits_guidance_overlap(self):
        # Sample: Record 3 PASS checks, one with guidance_overlap. Record reviewer scores 3 and 4. Check 3/3 all and 2/2 without overlap.
        key, attempt_id = self.build()
        for index in range(3):
            self.insert_check(attempt_id, key, index, 'PASS', overlap=index == 0)
        self.score(attempt_id, key, 'booking', 3, reviewer='opus-reviewer')
        self.score(attempt_id, key, 'booking', 4, reviewer='deepseek-flash-reviewer')
        rows = report(attempt_id, DATABASE)
        self.assertEqual([(row['stage'], row['score_a'], row['score_b'], row['pass_all'], row['pass_without_overlap'], row['flag']) for row in rows],
                         [('IMPLEMENTATION', 3, 4, '3/3', '2/2', 'AGREE')])

    def test_builder_role_reads_no_review_rows(self):
        # Sample: Connect as durai_build_reader. Select goal_check. Check permission denied. Select review_skill. Check permission denied.
        for table in ('goal_check', 'review_skill', 'review_rubric', 'anchor_mark', 'reviewer_agreement'):
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                as_builder('SELECT * FROM durai_bench.' + table)

    def test_review_deepseek_with_fake_model(self):
        # Sample: Fake reviewer reads workspace/docs/design.md, then answers 4 scores. Check 4 deepseek-flash-reviewer rows, 2 calls, cost 0.008 USD and a receipt.
        key, attempt_id = self.build()
        self.insert_check(attempt_id, key, 0, 'PASS')
        workspace, archive, receipts = (Path(self.folder.name, name) for name in ('ws', 'archive', 'receipts'))
        (workspace / 'docs').mkdir(parents=True)
        archive.mkdir()
        (workspace / 'docs/design.md').write_text('One lot row per trade id\n', encoding='utf-8')
        usage = {'input_tokens': 1000, 'output_tokens': 200, 'total_tokens': 1200}
        answer = json.dumps({'scores': [{'stage': stage, 'score': 3, 'evidence': 'workspace/docs/design.md:1'} for stage in STAGE_NAMES]})
        model = FakeReviewer([AIMessage(content='', tool_calls=[{'name': 'read_file', 'args': {'path': 'workspace/docs/design.md'}, 'id': 'c1'}], usage_metadata=usage),
                              AIMessage(content=answer, usage_metadata=usage)])
        receipt = review_deepseek(attempt_id, workspace, archive, model=model, database=DATABASE, receipts=receipts)
        self.assertEqual((receipt['calls'], receipt['stopReason'], receipt['scoresInserted'], receipt['estimatedEnterpriseCost']), (2, 'FINISHED', 4, 0.008))
        first = str(model.seen[0][1].content)
        self.assertIn(REQUEST, first)
        self.assertIn(GOALS[1], first)
        self.assertIn('One lot row per trade id', str(model.seen[1][-1].content))
        self.assertTrue((receipts / ('build-review-' + attempt_id + '-deepseek.json')).is_file())
        with transaction('durai_evaluator', DATABASE) as db:
            rows = db.execute('SELECT stage, reviewer_profile FROM durai_bench.build_score WHERE attempt_id=%s', (attempt_id,)).fetchall()
        self.assertEqual(sorted(row['stage'] for row in rows), sorted(STAGE_NAMES))
        self.assertEqual({row['reviewer_profile'] for row in rows}, {'deepseek-flash-reviewer'})
