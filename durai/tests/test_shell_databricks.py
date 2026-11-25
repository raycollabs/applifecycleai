from pathlib import Path
from tempfile import TemporaryDirectory
import os
import unittest

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from databricks.migration_parity import describe
from databricks.prepare import prepare


class DatabricksParityTests(unittest.TestCase):
    def test_stage_contains_both_apps(self):
        # Sample: Stage the shell. Check the API migrations and the Node launch file.
        with TemporaryDirectory() as folder:
            root = prepare(Path(folder) / 'staged')
            self.assertTrue((root / 'api/migrations/001_shell.sql').is_file())
            self.assertTrue((root / 'web/app/actions.ts').is_file())
            self.assertTrue((root / 'web/launch_web.mjs').is_file())
            self.assertIn('databricks-sdk==', (root / 'api/requirements.txt').read_text(encoding='utf-8'))

    @unittest.skipUnless(os.environ.get('DURAI_BUILD_DATABASE_URL'), 'Use the disposable build database')
    def test_parity_shape(self):
        # Sample: Compare two scratch databases. Check equal columns and an empty schema after rollback.
        url = os.environ['DURAI_BUILD_DATABASE_URL']
        info = conninfo_to_dict(url)
        if info.get('host') != 'db' or info.get('dbname') != 'postgres':
            self.fail('Use the disposable build database')
        names = ['durai_a3_parity_a', 'durai_a3_parity_b']
        with psycopg.connect(url, autocommit=True) as connection:
            for name in names:
                connection.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        targets = [make_conninfo(url, dbname=name) for name in names]
        first, second = [describe(target) for target in targets]
        self.assertEqual(first, second)
        self.assertTrue(first['indexes'])
        for target in targets:
            with psycopg.connect(target) as connection:
                self.assertIsNone(connection.execute("SELECT to_regnamespace('shell')").fetchone()[0])
