"""Sample: Load app tiny-append with booking into durai_test. Append refund to the same version. Append it again and read a refusal."""
import json
from pathlib import Path

from durai.common import Conflict, uid
from durai.db import transaction

from test_app_benchmark import DATABASE, ToolBase  # noqa: E402
from tools.app_benchmark import append_benchmark, load_benchmark  # noqa: E402


class AppendTests(ToolBase):
    def setUp(self):
        # Sample: Open the temp tree, write a baseline with booking.go, and store its manifest. Read 3 files in the manifest.
        super().setUp()
        self.tag = uid()[:6]
        self.key = 'tiny-append-' + self.tag
        manifest = self.baseline()
        self.manifest_path = Path(self.folder.name, 'manifest.json')
        self.manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        self.digests = {item['path']: item['sha256'] for item in manifest['files']}

    def app_block(self, version='E6'):
        # Sample: Return app tiny-append-1a2b3c at version E6 with ranking 8.
        return {'app_key': self.key, 'app_version': version, 'display_name': 'Tiny append', 'domain': 'trading',
                'builder_model': 'test', 'durai_ranking': 8}

    def sample(self, name):
        # Sample: Return sample S-APPEND-1a2b3c-booking with text "Record each payment once." as a COMPONENT draft.
        return {'sample_id': 'S-APPEND-{}-{}'.format(self.tag, name), 'revision': 1, 'domain': 'trading', 'scope': 'COMPONENT',
                'sort_order': 0, 'title': name, 'status': 'DRAFT', 'author': 'test', 'request_text': 'Record each payment once.', 'tags': []}

    def component(self, name, path, evidence=None):
        # Sample: Return component booking with evidence booking.go and the digest from the manifest.
        return {'component_key': name, 'category': 'transactions', 'goal': 'Record each payment once.', 'design': 'Keys in a table.',
                'built': 'BUILT', 'status': 'BUILT', 'test_goal_count': 1, 'limits': [],
                'evidence': evidence if evidence is not None else [{'path': path, 'sha256': self.digests[path]}],
                'sample_id': 'S-APPEND-{}-{}'.format(self.tag, name), 'sample_revision': 1, 'hidden_goals': ['A payment counts once']}

    def write_file(self, name, data):
        # Sample: Write the JSON file load.json under the temp folder. Return its path.
        path = Path(self.folder.name, name)
        path.write_text(json.dumps(data), encoding='utf-8')
        return path

    def load_booking(self):
        # Sample: Load app tiny-append with component booking. Return 1 component.
        data = {'app': self.app_block(), 'samples': [self.sample('booking')],
                'components': [self.component('booking', 'internal/service/booking.go')]}
        return load_benchmark(self.write_file('load.json', data), self.manifest_path, DATABASE, root=self.tree)

    def count(self):
        # Sample: Count app_component rows of app tiny-append. Return 1 or 2.
        with transaction('durai_evaluator', DATABASE) as db:
            return db.execute('SELECT count(*) AS n FROM durai_bench.app_component WHERE app_key=%s', (self.key,)).fetchone()['n']

    def test_append_adds_components_once(self):
        # Sample: Append refund to the loaded app. Read 2 components. Append refund again and read a refusal naming refund. Read 2 components.
        self.load_booking()
        data = {'app': self.app_block(), 'samples': [self.sample('refund')],
                'components': [self.component('refund', 'migrations/001_init.sql')]}
        appended = self.write_file('append.json', data)
        result = append_benchmark(appended, self.manifest_path, DATABASE, root=self.tree)
        self.assertEqual(result['components_added'], 1)
        self.assertEqual(self.count(), 2)
        with self.assertRaisesRegex(Conflict, 'refund'):
            append_benchmark(appended, self.manifest_path, DATABASE, root=self.tree)
        self.assertEqual(self.count(), 2)

    def test_append_refuses_missing_digest(self):
        # Sample: Append refund with evidence that has no sha256. Read a refusal naming the path. Read 1 component.
        self.load_booking()
        broken = self.component('refund', 'migrations/001_init.sql', evidence=[{'path': 'migrations/001_init.sql'}])
        data = {'app': self.app_block(), 'samples': [self.sample('refund')], 'components': [broken]}
        with self.assertRaisesRegex(ValueError, 'migrations/001_init.sql'):
            append_benchmark(self.write_file('bad.json', data), self.manifest_path, DATABASE, root=self.tree)
        self.assertEqual(self.count(), 1)

    def test_append_refuses_unknown_version(self):
        # Sample: Append to version E9, which is not loaded. Read a refusal that names E9. Read 1 component.
        self.load_booking()
        data = {'app': self.app_block('E9'), 'samples': [self.sample('refund')],
                'components': [self.component('refund', 'migrations/001_init.sql')]}
        with self.assertRaisesRegex(ValueError, 'E9'):
            append_benchmark(self.write_file('e9.json', data), self.manifest_path, DATABASE, root=self.tree)
        self.assertEqual(self.count(), 1)


if __name__ == '__main__':
    import unittest
    unittest.main()
