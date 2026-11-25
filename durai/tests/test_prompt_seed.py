"""Sample: Check the seed of design/prompt against both baseline manifests. Load it into durai_test and classify its 27 sample prompts."""
import json
import tempfile
import unittest
from pathlib import Path

from durai.build_tools import BuildTools
from durai.db import connect, prepare
from durai.prompt_enhancer import DEFAULT_MANIFESTS, SEED_DIRECTORY, Enhancer, blind_findings, load_manifest_terms
from durai.prompt_store import prepare_prompt

from tools.prompt_store import check_seed, load_seed, read_seed, seed_texts

DATABASE = 'durai_test'


class SeedTests(unittest.TestCase):
    def test_seed_passes_blind_check_for_both_baselines(self):
        # Sample: Check every seed text against both manifests. Check zero findings.
        missing = [str(path) for path in DEFAULT_MANIFESTS if not Path(path).is_file()]
        if missing:
            self.skipTest('Manifest is not available: ' + ', '.join(missing))
        terms = load_manifest_terms()
        self.assertEqual(2, len(terms))
        texts = seed_texts()
        self.assertGreater(len(texts), 200)
        findings = {label: blind_findings(text, terms) for label, text in texts}
        self.assertEqual({}, {label: found for label, found in findings.items() if found})
        self.assertEqual([], check_seed())

    def test_blind_check_names_a_baseline_term_in_a_seed_text(self):
        # Sample: Copy the seed. Add the file name booking.go to one concept. Check the finding names booking.go.
        with tempfile.TemporaryDirectory() as folder:
            files = [{'path': 'internal/service/booking.go', 'size': 10, 'sha256': 'a' * 64}]
            lines = ''.join('{}\t{}\t{}\n'.format(item['path'], item['size'], item['sha256']) for item in files)
            import hashlib
            manifest = {'root': folder, 'files': files, 'digest': hashlib.sha256(lines.encode('utf-8')).hexdigest()}
            (Path(folder) / 'm.json').write_text(json.dumps(manifest), encoding='utf-8')
            copy = Path(folder) / 'seed'
            copy.mkdir()
            for path in SEED_DIRECTORY.glob('*.json'):
                (copy / path.name).write_bytes(path.read_bytes())
            data = json.loads((copy / 'concepts.json').read_text(encoding='utf-8'))
            data['concepts'][0]['definition'] += ' See booking.go.'
            (copy / 'concepts.json').write_text(json.dumps(data), encoding='utf-8')
            problems = check_seed(copy, [Path(folder) / 'm.json'])
        self.assertTrue(any('booking.go' in problem for problem in problems), problems)

    def test_seed_counts_per_kind(self):
        # Sample: Count per kind. Check 6 to 12 concepts, 4 templates, 3 samples (4 for web-screens), 6 to 12 items and one OFFER item and 4 skills for each of 9 kinds.
        seed = read_seed()
        self.assertEqual(9, len(seed['kinds']))
        for kind in (row['kind_key'] for row in seed['kinds']):
            self.assertTrue(6 <= sum(kind in row['kind_keys'] for row in seed['concepts']) <= 12, kind)
            self.assertEqual(4, sum(row['kind_key'] == kind for row in seed['templates']))
            self.assertEqual(4 if kind == 'web-screens' else 3, sum(row['kind_key'] == kind for row in seed['samples']))
            self.assertTrue(6 <= sum(row['kind_key'] == kind for row in seed['items']) <= 12, kind)
            self.assertTrue(any(row['kind_key'] == kind and row['item_kind'] == 'OFFER' for row in seed['items']), kind)
            self.assertEqual(4, sum(row['kind_key'] == kind for row in seed['skills']))


class ScreenDesignSeedTests(unittest.TestCase):
    def test_web_screens_carry_ux_requirements(self):
        # Sample: Read the web-screens templates. Check Screen design in DESIGN and PLAN, Screen checks and Screen tests, and a trade screens sample that shows them.
        seed = read_seed()
        sections = {row['stage']: row['required_sections'] for row in seed['templates'] if row['kind_key'] == 'web-screens'}
        self.assertIn('Screen design', sections['DESIGN'])
        self.assertIn('Screen design', sections['PLAN'])
        self.assertIn('Screen checks', sections['IMPLEMENTATION'])
        self.assertIn('Screen tests', sections['TESTS'])
        sample = next(row for row in seed['samples'] if row['kind_key'] == 'web-screens' and 'look good' in row['request_text'])
        for name in ('Screen design', 'Screen checks', 'Screen tests', 'label', 'phone width'):
            self.assertIn(name, sample['enhanced_example'])
        kinds = [row['item_kind'] for row in seed['items'] if row['kind_key'] == 'web-screens']
        self.assertEqual({'ASK', 'OFFER'}, set(kinds))


class SeedLoadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Prepare durai_test with prompt_0001.
        prepare(DATABASE)
        prepare_prompt(DATABASE)

    def test_loader_refuses_reviewed_without_reviewer(self):
        # Sample: Ask for REVIEWED with no reviewer name. Check the refusal. Ask with a blank name. Check the refusal.
        for reviewer in (None, '  '):
            with self.assertRaises(ValueError):
                load_seed(SEED_DIRECTORY, DATABASE, 'REVIEWED', reviewer)

    def test_loader_loads_draft_then_review_and_classifies_samples(self):
        # Sample: Load the seed as DRAFT. Check the builder view is empty for its kinds. Load as REVIEWED. Check 27 samples classify to their kind.
        first = load_seed(SEED_DIRECTORY, DATABASE, 'DRAFT')
        self.assertEqual(9, first.get('kind:loaded', 0) + first.get('kind:unchanged', 0) + first.get('kind:updated', 0))
        with connect('postgres', DATABASE) as db:
            statuses = {row['status'] for row in db.execute("SELECT status FROM durai_prompt.component_kind WHERE kind_key='idempotent-command'").fetchall()}
        if statuses == {'DRAFT'}:
            with connect('postgres', DATABASE) as db:
                db.execute('SET ROLE durai_build_reader')
                self.assertEqual([], db.execute("SELECT 1 FROM durai_prompt.published_kind WHERE kind_key='idempotent-command'").fetchall())
        load_seed(SEED_DIRECTORY, DATABASE, 'REVIEWED', 'test-reviewer')
        again = load_seed(SEED_DIRECTORY, DATABASE, 'REVIEWED', 'test-reviewer')
        self.assertEqual({}, {key: value for key, value in again.items() if not key.endswith(':unchanged')})
        enhancer = Enhancer.for_database(DATABASE, manifests=())
        wrong = [(row['sample_key'], enhancer.classify(row['request_text'])['kind']) for row in read_seed()['samples']
                 if enhancer.classify(row['request_text'])['kind'] != row['kind_key']]
        self.assertEqual([], wrong)

    def test_offline_mode_returns_text_and_records_nothing(self):
        # Sample: Open the tools with enhance on and no database. Call the tool for S-TRADE-BOOK. Check the text starts with the request and says it did not record.
        with tempfile.TemporaryDirectory() as folder:
            tools = BuildTools(Path(folder) / 'ws', enhance=True, build_label='L-OFFLINE')
            tools.call('get_sample', {'sampleId': 'S-TRADE-BOOK'})
            reply = tools.call('get_enhanced_request', {})
        self.assertFalse(reply['isError'], reply['text'])
        self.assertIn('kind: idempotent-command', reply['text'])
        self.assertTrue(reply['text'].rstrip().endswith('no database]'))


if __name__ == '__main__':
    unittest.main()
