"""Sample: Start database E-1. Check Durai schemas, throwaway passwords, candidate reach, and teardown."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from durai.db import ROOT
from durai.db_sandbox import EphemeralDatabase
from durai.task_contract import build_task
from durai.task_evaluator import SandboxEvaluator
from durai.workspace_manifest import manifest
from test_task_contract import feature_spec

SUITE = {'/workspace/tests/__init__.py': b'',
         '/workspace/tests/test_reach.py': b'''"""Sample: Query database E-1. Fail to reach 1.1.1.1 and the local Durai database."""
import socket
import unittest
from durai.db import connect


class ReachTests(unittest.TestCase):
    def test_ephemeral_database_answers(self):
        # Sample: Log in as durai_runtime to durai_test in E-1. Select 1. Check 1.
        with connect('durai_runtime', 'durai_test') as connection:
            self.assertEqual(connection.execute('SELECT 1 AS n').fetchone()['n'], 1)

    def test_durai_schema_and_age_exist(self):
        # Sample: Read E-1 as durai_runtime. Check schemas durai_tasks and durai_ontology and extension age.
        with connect('durai_runtime', 'durai_test') as connection:
            schemas = {row['nspname'] for row in connection.execute('SELECT nspname FROM pg_namespace').fetchall()}
            extensions = {row['extname'] for row in connection.execute('SELECT extname FROM pg_extension').fetchall()}
        self.assertTrue({'durai_tasks', 'durai_ontology', 'durai_checkpoints'} <= schemas)
        self.assertIn('age', extensions)

    def test_internet_is_blocked(self):
        # Sample: Open a socket to 1.1.1.1 port 53. Check OSError.
        with self.assertRaises(OSError):
            socket.create_connection(('1.1.1.1', 53), timeout=3)

    def test_local_database_is_blocked(self):
        # Sample: Open a socket to host.docker.internal port 54410. Check OSError.
        with self.assertRaises(OSError):
            socket.create_connection(('host.docker.internal', 54410), timeout=3)
'''}
PROBE_MIGRATION = (b'-- Sample: Create table probe with one integer column. Let durai_runtime read it.\n'
                   b'CREATE TABLE durai_ontology.sandbox_probe(id integer PRIMARY KEY);\n'
                   b'GRANT SELECT ON durai_ontology.sandbox_probe TO durai_runtime;\n')
PROBE_SUITE = {'/workspace/tests/__init__.py': b'',
               '/workspace/tests/seed/001_probe.sql': b'INSERT INTO durai_ontology.sandbox_probe VALUES(7);\n',
               '/workspace/tests/test_probe.py': b'''"""Sample: Read table probe in E-1. Check row 7."""
import unittest
from durai.db import connect


class ProbeTests(unittest.TestCase):
    def test_seed_row_is_visible(self):
        # Sample: Select id from probe as durai_runtime. Check 7.
        with connect('durai_runtime', 'durai_test') as connection:
            self.assertEqual(connection.execute('SELECT id FROM durai_ontology.sandbox_probe').fetchone()['id'], 7)
'''}
FILES = {'/workspace/durai/__init__.py': b'', '/workspace/durai/db.py': (ROOT / 'durai/db.py').read_bytes()}


def names(kind, name):
    # Sample: List containers named E-1. Return an empty list after teardown.
    command = ['docker', 'ps', '-a', '--filter', 'name=' + name, '--format', '{{.Names}}'] if kind == 'container' else \
        ['docker', 'network', 'ls', '--filter', 'name=' + name, '--format', '{{.Name}}']
    return [line for line in subprocess.run(command, capture_output=True, text=True, check=True).stdout.split() if line]


class DbSandboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Start database E-1 for the schema and password checks.
        cls.database = EphemeralDatabase().__enter__()
        cls.database.seal()

    @classmethod
    def tearDownClass(cls):
        # Sample: Remove database E-1.
        cls.database.__exit__(None, None, None)

    def test_database_leaves_bridge_after_setup(self):
        # Sample: Inspect E-1 after setup. Check that its only network is N-1.
        self.assertEqual(self.database.networks(), [self.database.network])

    def test_passwords_are_throwaway(self):
        # Sample: Read the candidate passwords for E-1. Check no superuser entry and no match with the local passwords.
        local = json.loads((ROOT / 'private/local.json').read_text(encoding='utf-8'))['passwords']
        candidate = json.loads(self.database.candidate_path.read_text(encoding='utf-8'))['passwords']
        self.assertNotIn('postgres', candidate)
        self.assertTrue(all(candidate[role] != local[role] for role in candidate))
        self.assertFalse(self.database.settings_path.exists())

    def test_teardown_removes_container_and_network(self):
        # Sample: Start and stop database E-2. Check zero containers and networks with its names.
        with EphemeralDatabase() as database:
            self.assertEqual(names('container', database.name), [database.name])
        self.assertEqual((names('container', database.name), names('network', database.network)), ([], []))
        self.assertFalse(database.candidate_path.exists())

    def test_candidate_reaches_only_ephemeral_database(self):
        # Sample: Run three candidate tests in a new E-3. Check SELECT 1, a blocked 1.1.1.1, and a blocked local database.
        evaluator = SandboxEvaluator(Path(tempfile.mkdtemp(prefix='durai-dbeval-')))
        task = build_task(feature_spec(writePaths=['/workspace/durai/db.py'], deliverables=['/workspace/durai/db.py'],
                                       validationCommands=['python-unittest'],
                                       environment={'imageId': evaluator.database_image, 'database': 'ephemeral-postgres-age'},
                                       protectedSuite={'id': 'H-DB', 'digest': manifest(SUITE)['digest']}))['task']
        verdict = evaluator.evaluate(task, FILES, SUITE)
        self.assertEqual(verdict['verdict'], 'PASS', verdict['results'])
        self.assertEqual([item['command'] for item in verdict['results']], ['database-setup', 'python-unittest'])
        self.assertIn('Ran 4 tests', verdict['results'][1]['output'])
        self.assertEqual(verdict['database'], 'ephemeral-postgres-age')

    def evaluate_probe(self, files, suite, migration='core_0099.sql'):
        # Sample: Pin migrations 1 to 11. Replace owned migration 10 with table probe. Exclude later migration 12.
        baseline = dict(FILES)
        baseline.update({'/workspace/migrations/core/'+path.name:path.read_bytes()
                         for path in (ROOT/'migrations/core').glob('*.sql') if path.name<='core_0011.sql'})
        files = dict(baseline,**files)
        evaluator = SandboxEvaluator(Path(tempfile.mkdtemp(prefix='durai-dbeval-')))
        task = build_task(feature_spec(writePaths=['/workspace/durai/db.py', '/workspace/migrations/core/' + migration],
                                       deliverables=['/workspace/durai/db.py'], validationCommands=['python-unittest'],
                                       environment={'imageId': evaluator.database_image, 'database': 'ephemeral-postgres-age'},
                                       protectedSuite={'id': 'H-PROBE', 'digest': manifest(suite)['digest']}))['task']
        return evaluator.evaluate(task, files, suite, baseline=baseline)

    def test_candidate_migration_and_seed_are_applied(self):
        # Sample: Add migration core_0099 with table probe and seed row 7. Check that the candidate reads 7.
        files = dict(FILES)
        files['/workspace/migrations/core/core_0099.sql'] = PROBE_MIGRATION
        verdict = self.evaluate_probe(files, PROBE_SUITE)
        self.assertEqual(verdict['verdict'], 'PASS', verdict['results'])
        self.assertIn('applied', verdict['results'][0]['output'])

    def test_changed_applied_migration_fails_setup(self):
        # Sample: Change one byte of core_0001.sql in the candidate files. Check database-setup FAIL.
        files = dict(FILES)
        files['/workspace/migrations/core/core_0001.sql'] = (ROOT / 'migrations/core/core_0001.sql').read_bytes() + b'\n'
        verdict = self.evaluate_probe(files, PROBE_SUITE)
        self.assertEqual(verdict['results'][0]['command'], 'database-setup')
        self.assertEqual(verdict['results'][0]['state'], 'FAIL')
        self.assertIn('Applied migration changed', verdict['results'][0]['output'])
        self.assertEqual(verdict['verdict'], 'FAIL')

    def test_owned_migration_replaces_trusted_file(self):
        # Sample: Own core_0010.sql. Give table probe in place of the trusted policy table. Check PASS and row 7.
        files = dict(FILES)
        files['/workspace/migrations/core/core_0010.sql'] = PROBE_MIGRATION
        verdict = self.evaluate_probe(files, PROBE_SUITE, 'core_0010.sql')
        self.assertEqual(verdict['verdict'], 'PASS', verdict['results'])

    def test_task_without_database_keeps_no_network(self):
        # Sample: Build the docker command for a task without the database option. Check --network none.
        evaluator = SandboxEvaluator(Path(tempfile.mkdtemp(prefix='durai-dbeval-')))
        command = evaluator.command('C-1', '/tmp/w', ['python', '-V'], evaluator.image)
        self.assertEqual(command[command.index('--network') + 1], 'none')
        task = build_task(feature_spec())['task']
        self.assertEqual(evaluator.image_for(task), evaluator.image)


if __name__ == '__main__':
    unittest.main()
