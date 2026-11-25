"""Sample: Load trade-pilot E6 and xeditai into durai_test. Check the schema refusals, the matrix, and the blind sample text."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import psycopg
from psycopg.types.json import Jsonb
from durai.bench_store import add_profile, prepare_bench
from durai.common import Conflict, uid
from durai.db import connect, prepare, transaction
from durai.sample_store import prepare_samples

from tools.app_benchmark import baseline_terms, blind_violations, build_manifest, load_benchmark, show_matrix  # noqa: E402

DATABASE = 'durai_test'
FIXTURE = Path(__file__).parent / 'fixtures/app_benchmark_tiny.json'
DIGEST = 'a' * 64
EVIDENCE = [{'path': 'internal/service/booking.go', 'sha256': DIGEST, 'lines': '1-80'}]


def ensure_rubrics():
    # Sample: Load rubric revision 1 of 4 stages from design/review/rubric-v1.json into durai_test. Load again. Keep 4 rows.
    data = json.loads((Path(__file__).parents[1] / 'design/review/rubric-v1.json').read_text(encoding='utf-8'))
    with transaction('durai_evaluator', DATABASE) as db:
        for item in data['rubrics']:
            text = json.dumps({'stage': item['stage'], 'levels': item['levels']}, sort_keys=True, separators=(',', ':'))
            db.execute('INSERT INTO durai_bench.review_rubric(rubric_id,revision,stage,levels,digest) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                       (item['rubric_id'], data['revision'], item['stage'], Jsonb(item['levels']), hashlib.sha256(text.encode('utf-8')).hexdigest()))


class AppBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Prepare durai_test with bench_0006 and samples_0001. Read the 14 categories and 4 rubrics.
        prepare(DATABASE)
        prepare_bench(DATABASE)
        prepare_samples(DATABASE)
        ensure_rubrics()

    def key(self, prefix='trade-pilot'):
        # Sample: Return trade-pilot-1a2b3c4d as a fresh app key.
        return prefix + '-' + uid()[:8]

    def app(self, key, ranking=8, version='E6'):
        # Sample: Insert app trade-pilot-1a2b3c4d version E6 with ranking 8.
        with transaction('durai_evaluator', DATABASE) as db:
            db.execute("INSERT INTO durai_bench.app_benchmark(app_key,app_version,display_name,domain,builder_model,durai_ranking,source_root,source_manifest_digest)"
                       " VALUES(%s,%s,%s,'trading','GPT-6.2-Sol',%s,'D:/src',%s)", (key, version, key, ranking, DIGEST))

    def component(self, key, component='booking', status='BUILT', evidence=EVIDENCE, version='E6', category='transactions'):
        # Sample: Insert component booking BUILT with 1 evidence file under app trade-pilot-1a2b3c4d.
        with transaction('durai_evaluator', DATABASE) as db:
            db.execute("INSERT INTO durai_bench.app_component(app_key,app_version,component_key,category,goal,design,built,status,evidence)"
                       " VALUES(%s,%s,%s,%s,'Book trades once','Lots per desk','Booking service',%s,%s)",
                       (key, version, component, category, status, Jsonb(evidence)))

    def profile(self):
        # Sample: Add model profile p-1a2b3c4d5e6f and return its id.
        profile_id = 'p-' + uid()[:12]
        add_profile(profile_id, 'deepseek-' + uid()[:8], 'deepseek-flash', 'direct', database=DATABASE)
        return profile_id

    def attempt(self, key, profile_id, scope='COMPONENT', component='booking', version='E6'):
        # Sample: Insert a COMPONENT build of booking by profile P-1 and return its attempt id.
        attempt_id = 'A-' + uid()[:10]
        with transaction('durai_evaluator', DATABASE) as db:
            db.execute("INSERT INTO durai_bench.build_attempt(attempt_id,app_key,app_version,scope,component_key,model_profile_id,request_digest)"
                       " VALUES(%s,%s,%s,%s,%s,%s,%s)", (attempt_id, key, version, scope, component if scope == 'COMPONENT' else None, profile_id, DIGEST))
            db.execute("INSERT INTO durai_bench.build_stage(attempt_id,stage,state) VALUES(%s,'IMPLEMENTATION','DONE')", (attempt_id,))
        return attempt_id

    def score(self, attempt_id, key, component, value, version='E6', reviewer='human', rubric='r1'):
        # Sample: Insert IMPLEMENTATION score 3 for attempt A-1 citing component booking and rubric r1.
        with transaction('durai_evaluator', DATABASE) as db:
            db.execute("INSERT INTO durai_bench.build_score(attempt_id,stage,app_key,app_version,component_key,reviewer_profile,score,baseline_score,evidence,rubric_revision,prompt_digest)"
                       " VALUES(%s,'IMPLEMENTATION',%s,%s,%s,%s,%s,4,'Reviewed lots and once-only booking',%s,%s)",
                       (attempt_id, key, version, component, reviewer, value, rubric, DIGEST))


class ToolBase(AppBase):
    def setUp(self):
        # Sample: Open a temp folder for the baseline tree and the load files.
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.tree = Path(self.folder.name, 'tree')

    def write(self, relative, content=b'x\n'):
        # Sample: Write pages/index.tsx under the tree with 2 bytes.
        path = self.tree / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode('utf-8'))
        return path

    def baseline(self):
        # Sample: Write booking.go and migrations/001_init.sql with table trade_lot. Return the manifest.
        self.write('internal/service/booking.go', b'package booking\n')
        self.write('migrations/001_init.sql', 'CREATE TABLE trade_lot(id int);\n')
        self.write('pages/index.tsx')
        return build_manifest(self.tree)

    def files(self, mutate=None):
        # Sample: Copy the tiny fixture with app tiny-trade-1a2b3c and sample ids ending -1a2b3c. Return the data path and manifest path.
        tag = uid()[:6]
        data = json.loads(FIXTURE.read_text(encoding='utf-8'))
        data['app']['app_key'] += '-' + tag
        for sample in data['samples']:
            sample['sample_id'] += '-' + tag
            if 'parent_sample_id' in sample:
                sample['parent_sample_id'] += '-' + tag
        for component in data['components']:
            component['sample_id'] += '-' + tag
        if mutate:
            mutate(data)
        manifest = self.baseline()
        data_path, manifest_path = Path(self.folder.name, 'app.json'), Path(self.folder.name, 'manifest.json')
        data_path.write_text(json.dumps(data), encoding='utf-8')
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        return data_path, manifest_path, data['app']['app_key']

    def count(self, table, key):
        # Sample: Count app_component rows of app tiny-trade-1a2b3c. Return 0 or 1.
        with transaction('durai_evaluator', DATABASE) as db:
            return db.execute('SELECT count(*) AS n FROM durai_bench.' + table + ' WHERE app_key=%s', (key,)).fetchone()['n']


class ManifestTests(ToolBase):
    def test_secret_files_are_excluded(self):
        # Sample: Make a tree with githubkey, app-sa-0123456789ab.json with "private_key", private/runtime.json and pages/index.tsx. Check the manifest has pages/index.tsx only.
        self.write('githubkey', 'ghp_example')
        self.write('app-sa-0123456789ab.json', '{"private_key": "x"}')
        self.write('service-account.json', '{"private_key": "x"}')
        self.write('private/runtime.json', '{"token": "x"}')
        self.write('.env.local', 'A=1')
        self.write('node_modules/lib/index.js')
        self.write('pages/index.tsx')
        self.assertEqual([item['path'] for item in build_manifest(self.tree)['files']], ['pages/index.tsx'])
        self.write('docs/notes.md')
        self.assertEqual([item['path'] for item in build_manifest(self.tree, ['docs/*'])['files']], ['pages/index.tsx'])

    def test_manifest_digest_is_stable(self):
        # Sample: Build the manifest of the same tree twice. Check equal digests.
        self.baseline()
        first, second = build_manifest(self.tree), build_manifest(self.tree)
        self.assertEqual(first['digest'], second['digest'])
        self.assertEqual(len(first['digest']), 64)
        self.write('pages/index.tsx', b'changed\n')
        self.assertNotEqual(build_manifest(self.tree)['digest'], first['digest'])


class LoadTests(ToolBase):
    def test_evidence_digest_must_match(self):
        # Sample: Load trade-pilot with evidence internal/service/booking.go. Change the file. Check the load fails and app_component has 0 rows.
        data_path, manifest_path, key = self.files()
        self.write('internal/service/booking.go', b'package changed\n')
        with self.assertRaises(ValueError):
            load_benchmark(data_path, manifest_path, DATABASE)
        self.assertEqual((self.count('app_benchmark', key), self.count('app_component', key)), (0, 0))

    def test_same_version_load_is_refused(self):
        # Sample: Load trade-pilot E6. Load it again. Check refusal.
        data_path, manifest_path, key = self.files()
        self.assertEqual(load_benchmark(data_path, manifest_path, DATABASE)['components'], 1)
        with self.assertRaises(Conflict):
            load_benchmark(data_path, manifest_path, DATABASE)
        self.assertEqual(self.count('app_component', key), 1)
        self.assertEqual([row['component_key'] for row in show_matrix(key, None, DATABASE)], ['booking'])


class AppBenchmarkSchemaTests(AppBase):
    def test_ranking_and_status_checks(self):
        # Sample: Insert trade-pilot with durai_ranking 11. Check refusal. Insert component booking with status DONE. Check refusal.
        key = self.key()
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.app(key, ranking=11)
        self.app(key, ranking=8)
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.component(key, status='DONE')

    def test_built_needs_evidence(self):
        # Sample: Insert component booking, status BUILT, evidence []. Check refusal.
        key = self.key()
        self.app(key)
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.component(key, status='BUILT', evidence=[])
        self.component(key, status='DESIGNED', evidence=[])


class BuildTests(AppBase):
    def test_component_scope_needs_component(self):
        # Sample: Insert a trade-pilot build with scope COMPONENT and no component_key. Check refusal.
        key = self.key()
        self.app(key)
        profile_id = self.profile()
        with self.assertRaises(psycopg.errors.CheckViolation):
            with transaction('durai_evaluator', DATABASE) as db:
                db.execute("INSERT INTO durai_bench.build_attempt(attempt_id,app_key,app_version,scope,model_profile_id,request_digest)"
                           " VALUES(%s,%s,'E6','COMPONENT',%s,%s)", ('A-' + uid()[:10], key, profile_id, DIGEST))

    def test_score_is_against_same_baseline(self):
        # Sample: Insert a trade-pilot build of booking. Insert a score that cites xeditai component export. Check refusal.
        trade, docs = self.key('trade-pilot'), self.key('xeditai')
        self.app(trade)
        self.component(trade)
        self.app(docs, ranking=9, version='found')
        self.component(docs, 'export', category='export-reporting', version='found')
        attempt_id = self.attempt(trade, self.profile())
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            with transaction('durai_evaluator', DATABASE) as db:
                db.execute("INSERT INTO durai_bench.build_score(attempt_id,stage,app_key,app_version,component_key,reviewer_profile,score,evidence,rubric_revision,prompt_digest)"
                           " VALUES(%s,'IMPLEMENTATION',%s,'found','export','human',2,'Export reviewed','r1',%s)", (attempt_id, docs, DIGEST))
        self.score(attempt_id, trade, 'booking', 3)


class MatrixTests(AppBase):
    def test_matrix_shows_best_score_per_model(self):
        # Sample: Insert two DeepSeek builds of booking with IMPLEMENTATION scores 2 and 3. Check the matrix shows 3.
        key = self.key()
        self.app(key)
        self.component(key)
        profile_id = self.profile()
        self.score(self.attempt(key, profile_id), key, 'booking', 2)
        self.score(self.attempt(key, profile_id), key, 'booking', 3)
        with transaction('durai_evaluator', DATABASE) as db:
            rows = db.execute("SELECT best_build_score, baseline_score, builds FROM durai_bench.baseline_matrix WHERE app_key=%s AND stage='IMPLEMENTATION' AND model_profile_id=%s",
                              (key, profile_id)).fetchall()
        self.assertEqual([(row['best_build_score'], row['baseline_score'], row['builds']) for row in rows], [(3, 4, 2)])


def sample_row(db, sample_id, revision=1, status='REVIEWED', text='book equity, futures and option trades for my desks; never book a trade twice',
               scope='COMPONENT', parent=None, order=0, title='Book trades'):
    # Sample: Insert sample S-TRADE-BOOK-1a2b revision 1 REVIEWED with 12 words of user text.
    db.execute("INSERT INTO durai_samples.sample_request(sample_id,revision,domain,scope,parent_sample_id,sort_order,title,request_text,status,author,reviewer)"
               " VALUES(%s,%s,'trading',%s,%s,%s,%s,%s,%s,'author-1',%s)",
               (sample_id, revision, scope, parent, order, title, text, status, 'reviewer-1' if status != 'DRAFT' else None))


def as_builder(query, params=()):
    # Sample: Switch to durai_build_reader. Run the query. Return the rows.
    with connect('postgres', DATABASE) as db:
        db.execute('SET ROLE durai_build_reader')
        return db.execute(query, params).fetchall()


class SampleTests(AppBase):
    def test_sample_revision_is_immutable(self):
        # Sample: Insert sample S-TRADE-BOOK revision 1 "book equity, futures and option trades for my desks; never book a trade twice", status REVIEWED. Update its text. Check refusal. Insert revision 2 with "...and show me each desk's position". Check accepted.
        sample_id = 'S-TRADE-BOOK-' + uid()[:6]
        with transaction('durai_evaluator', DATABASE) as db:
            sample_row(db, sample_id)
        with self.assertRaises(psycopg.errors.IntegrityConstraintViolation):
            with connect('postgres', DATABASE) as admin:
                admin.execute('UPDATE durai_samples.sample_request SET request_text=%s WHERE sample_id=%s AND revision=1', ('book anything', sample_id))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with transaction('durai_evaluator', DATABASE) as db:
                db.execute('UPDATE durai_samples.sample_request SET title=%s WHERE sample_id=%s', ('Other', sample_id))
        with transaction('durai_evaluator', DATABASE) as db:
            db.execute("UPDATE durai_samples.sample_request SET status='PUBLISHED' WHERE sample_id=%s AND revision=1", (sample_id,))
            sample_row(db, sample_id, 2, text="book equity, futures and option trades for my desks; never book a trade twice; and show me each desk's position")
            rows = db.execute('SELECT revision, status FROM durai_samples.sample_request WHERE sample_id=%s ORDER BY revision', (sample_id,)).fetchall()
        self.assertEqual([(row['revision'], row['status']) for row in rows], [(1, 'PUBLISHED'), (2, 'REVIEWED')])

    def test_app_sample_groups_components(self):
        # Sample: Insert APP sample S-TRADE with 5 COMPONENT children. Read the group. Check 5 titles in order.
        root = 'S-TRADE-' + uid()[:6]
        titles = ['Book trades', 'Value positions', 'Settle trades', 'Desk hierarchy', 'Market prices']
        with transaction('durai_evaluator', DATABASE) as db:
            sample_row(db, root, scope='APP', title='Trading app', text='build me a trading app for my desks')
            for number in (3, 1, 5, 2, 4):
                sample_row(db, root + '-' + str(number), parent=root, order=number, title=titles[number - 1], text='component request number ' + str(number))
        rows = as_builder('SELECT title FROM durai_samples.published_sample WHERE parent_sample_id=%s ORDER BY sort_order', (root,))
        self.assertEqual([row['title'] for row in rows], titles)

    def test_draft_is_hidden_from_builder(self):
        # Sample: Insert S-JOBS-1 DRAFT and S-JOBS-2 REVIEWED. Read as the builder role. Check S-JOBS-2 only.
        tag = uid()[:6]
        with transaction('durai_evaluator', DATABASE) as db:
            sample_row(db, 'S-JOBS-1-' + tag, status='DRAFT', text='schedule jobs')
            sample_row(db, 'S-JOBS-2-' + tag, status='REVIEWED', text='run jobs every night')
        rows = as_builder("SELECT sample_id FROM durai_samples.published_sample WHERE sample_id LIKE %s", ('S-JOBS-%-' + tag,))
        self.assertEqual([row['sample_id'] for row in rows], ['S-JOBS-2-' + tag])
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            as_builder('SELECT sample_id FROM durai_samples.sample_request')


class BlindnessTests(ToolBase):
    def test_builder_role_cannot_read_hidden_goals(self):
        # Sample: Connect as the builder role. Select hidden_goals, the booking design, the goal sample link and the app notes. Check permission denied for each. Select request_text of S-TRADE-BOOK from published_sample. Check one row.
        key, sample_id = self.key(), 'S-TRADE-BOOK-' + uid()[:6]
        self.app(key)
        self.component(key)
        with transaction('durai_evaluator', DATABASE) as db:
            sample_row(db, sample_id)
            digest = db.execute('SELECT text_digest FROM durai_samples.sample_request WHERE sample_id=%s', (sample_id,)).fetchone()['text_digest']
            db.execute("INSERT INTO durai_bench.component_goal(app_key,app_version,component_key,sample_id,sample_revision,sample_digest,hidden_goals)"
                       " VALUES(%s,'E6','booking',%s,1,%s,%s)", (key, sample_id, digest, Jsonb(['Same trade id books once'])))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            as_builder('SELECT hidden_goals FROM durai_bench.component_goal WHERE app_key=%s', (key,))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            as_builder('SELECT * FROM durai_bench.build_score')
        for query in ('SELECT design FROM durai_bench.app_component WHERE app_key=%s',
                      'SELECT sample_id FROM durai_bench.component_goal WHERE app_key=%s',
                      'SELECT notes FROM durai_bench.app_benchmark WHERE app_key=%s'):
            with self.subTest(query=query), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                as_builder(query, (key,))
        rows = as_builder('SELECT request_text FROM durai_samples.published_sample WHERE sample_id=%s', (sample_id,))
        self.assertEqual(len(rows), 1)
        self.assertIn('never book a trade twice', rows[0]['request_text'])

    def test_request_with_baseline_terms_is_refused(self):
        # Sample: Load sample S-TRADE-BOOK with request_text "Extend internal/service/booking.go with FIFO lots". Check refusal that names booking.go.
        def mutate(data):
            data['samples'][1]['request_text'] = 'Extend internal/service/booking.go with FIFO lots'
        data_path, manifest_path, key = self.files(mutate)
        with self.assertRaises(ValueError) as caught:
            load_benchmark(data_path, manifest_path, DATABASE)
        self.assertIn('booking.go', str(caught.exception))
        self.assertEqual(self.count('app_benchmark', key), 0)
        terms = baseline_terms(build_manifest(self.tree), self.tree)
        self.assertEqual(blind_violations('Book trades for my desks and show each booking', terms), [])
        self.assertEqual(blind_violations('Keep every trade_lot per desk', terms), ['table trade_lot'])


if __name__ == '__main__':
    unittest.main()
