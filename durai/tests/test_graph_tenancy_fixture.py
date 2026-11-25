"""Sample: Fail the graph shell by assertion. Pass the reference, restore its database, and reach scripted review."""
from pathlib import Path
import json
import subprocess
import tempfile
import unittest
from durai.common import uid
from durai.db import prepare
from durai.db_sandbox import EphemeralDatabase
from durai.harness import prepare_checkpoints
from durai.qualification import BINDING, TEMPLATES, contract_digest, fixture_files, fixture_spec, load, run_fixture
from durai.packs import PackStore
from durai.policy_store import record_policies
from durai.task_contract import build_task
from durai.task_evaluator import SandboxEvaluator
from durai.workspace_manifest import materialize
from test_workspace_worker import SequenceModel

DATABASE = 'durai_test'
NAME = 'task-graph-tenancy'
FOLDER = TEMPLATES / NAME
MODULE = '/workspace/durai/application_policy_graph.py'
MIGRATION = '/workspace/migrations/core/core_0012.sql'
BASE = fixture_files(FOLDER)
SUITE = load(FOLDER / 'protected')
REFERENCE = dict(BASE, **load(FOLDER / 'reference'))


class GraphTenancyFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Record the bound test policies. Pin task-graph-tenancy to the locked evaluator and ephemeral AGE database images.
        prepare(DATABASE)
        prepare_checkpoints(DATABASE)
        contract_digest(DATABASE)
        for reference in PackStore(DATABASE).resolve(BINDING)['document']['packs']:
            record_policies(reference, DATABASE)
        cls.temporary = tempfile.TemporaryDirectory(prefix='durai-graph-tenancy-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.evaluator = SandboxEvaluator(Path(cls.temporary.name))
        cls.task = build_task(fixture_spec(NAME, DATABASE, cls.evaluator.image, cls.evaluator.database_image))['task']

    def test_shell_fails_business_goal(self):
        # Sample: Run the shell. Check database setup and compilation PASS, then an own-policy assertion failure.
        verdict = self.evaluator.evaluate(self.task, BASE, SUITE, baseline=BASE)
        states = {item['command']: item for item in verdict['results']}
        self.assertEqual(states['database-setup']['state'], 'PASS', states)
        self.assertEqual(states['python-compile']['state'], 'PASS', states)
        self.assertIn('GraphTenancyTests.test_application_reads_own_and_generic_policies', states['python-unittest']['output'])
        self.assertIn('self.assertEqual(len(actual), 2)', states['python-unittest']['output'])
        self.assertIn('AssertionError: 0 != 2', states['python-unittest']['output'])
        self.assertNotIn('ImportError', states['python-unittest']['output'])

    def test_reference_passes_protected_suite(self):
        # Sample: Run the reference migration and module. Check all eleven protected graph isolation goals pass.
        verdict = self.evaluator.evaluate(self.task, REFERENCE, SUITE, baseline=BASE)
        self.assertEqual(verdict['verdict'], 'PASS', verdict['results'])
        output = next(item['output'] for item in verdict['results'] if item['command'] == 'python-unittest')
        self.assertIn('Ran 11 tests', output)

    def test_scripted_loop_passes_with_reference(self):
        # Sample: Apply the reference module and migration through file tools. Check WAITING_FOR_REVIEW with no paid call.
        steps = [('edit_file', {'file_path': MODULE, 'old_string': BASE[MODULE].decode(), 'new_string': REFERENCE[MODULE].decode()}),
                 ('write_file', {'file_path': MIGRATION, 'content': REFERENCE[MIGRATION].decode()})]
        result = run_fixture(NAME, lambda attempt: SequenceModel(steps=steps, replies=[]),
                             Path(self.temporary.name) / 'loop', DATABASE,
                             candidate_root=Path(self.temporary.name) / 'candidates')
        self.assertEqual(result['outcome'], 'PASSED', result.get('verdict'))
        self.assertEqual([(item['verdict'], item['state']) for item in result['attempts']], [('PASS', 'WAITING_FOR_REVIEW')])

    def test_restore_preserves_grants_and_digests(self):
        # Sample: Dump an isolated reference database. Restore it as a new database. Check exact views and foreign-table denial.
        folder = Path(self.temporary.name) / ('restore-check-' + uid())
        materialize(folder, dict(REFERENCE, **SUITE))
        with EphemeralDatabase(self.temporary.name, owned=['core_0012.sql']) as database:
            setup = database.apply(REFERENCE, SUITE, self.evaluator.database_image)
            self.assertEqual(setup['exitCode'], 0, setup['output'])
            snapshot = ("import json\nfrom durai.application_policy_graph import project_binding,application_policy_view\n"
                        "views={}\nfor role,binding in [('durai_runtime','graph-trading'),('durai_foreign','graph-foreign')]:\n"
                        " project_binding(binding, 'DATABASE', role)\n views[role]=application_policy_view(binding,'DATABASE',role)\n"
                        "print(json.dumps(views,sort_keys=True))\n")
            before = self.evaluator.run(folder / 'workspace', ['python', '-c', snapshot.replace('DATABASE', DATABASE)],
                                        self.evaluator.database_image, database.candidate_args())
            self.assertEqual(before['exitCode'], 0, before['output'])
            restored = 'durai_restore_' + uid().replace('-', '')[:12]
            created = subprocess.run(['docker', 'exec', database.name, 'createdb', '-U', 'supabase_admin',
                                      '-T', 'template0', restored], capture_output=True, timeout=30)
            self.assertEqual(created.returncode, 0, created.stderr.decode(errors='replace')[-2000:])
            dump = subprocess.run(['docker', 'exec', database.name, 'pg_dump', '-U', 'supabase_admin', '-Fc', DATABASE],
                                  capture_output=True, check=True, timeout=60).stdout
            result = subprocess.run(['docker', 'exec', '-i', database.name, 'pg_restore', '--exit-on-error',
                                     '-U', 'supabase_admin', '-d', restored], input=dump, capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace')[-2500:])
            grants = subprocess.run(['docker', 'exec', database.name, 'psql', '-U', 'supabase_admin', '-d', 'postgres',
                                     '-v', 'ON_ERROR_STOP=1', '-c', 'REVOKE ALL ON DATABASE ' + restored +
                                     ' FROM PUBLIC; GRANT CONNECT ON DATABASE ' + restored + ' TO durai_runtime,durai_foreign;'],
                                    capture_output=True, timeout=30)
            self.assertEqual(grants.returncode, 0, grants.stderr.decode(errors='replace')[-2000:])
            read_snapshot = snapshot.replace(" project_binding(binding, 'DATABASE', role)\n", '')
            raw = self.evaluator.run(folder / 'workspace', ['python', '-c', read_snapshot.replace('DATABASE', restored)],
                                     self.evaluator.database_image, database.candidate_args())
            self.assertNotEqual(raw['exitCode'], 0)
            self.assertIn('graph with oid', raw['output'])
            repair = subprocess.run(['docker', 'exec', '-i', database.name, 'psql', '-U', 'supabase_admin',
                                     '-v', 'ON_ERROR_STOP=1', '-d', restored],
                                    input=SUITE['/workspace/tests/operator/restore_age_graph_oids.sql'], capture_output=True, timeout=30)
            self.assertEqual(repair.returncode, 0, repair.stderr.decode(errors='replace')[-2000:])
            after = self.evaluator.run(folder / 'workspace', ['python', '-c', read_snapshot.replace('DATABASE', restored)],
                                       self.evaluator.database_image, database.candidate_args())
            self.assertEqual(after['exitCode'], 0, after['output'])
            self.assertEqual(json.loads(after['output']), json.loads(before['output']))
            checks = ("import unittest\nfrom tests import test_graph_tenancy as subject\n"
                      "subject.DATABASE='" + restored + "'\n"
                      "result=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(subject.GraphTenancyTests))\n"
                      "raise SystemExit(0 if result.wasSuccessful() else 1)\n")
            verified = self.evaluator.run(folder / 'workspace', ['python', '-c', checks],
                                          self.evaluator.database_image, database.candidate_args())
            self.assertEqual(verified['exitCode'], 0, verified['output'])


if __name__ == '__main__':
    unittest.main()
