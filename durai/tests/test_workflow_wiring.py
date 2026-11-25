"""Sample: Build the default registry. Run a TEAM step with an implementer and a test writer under leases. Accept in the sandbox. Run the workflow CLI."""
import importlib.util
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from durai.common import uid
from durai.db import prepare, transaction
from durai.execution_events import replay
from durai.file_leases import FileLeases
from durai.harness import prepare_checkpoints
from durai.strategy_subagents import FolderWorkspace
from durai.task_evaluator import SandboxEvaluator
from durai.workflow_gates import approve
from durai.workflow_registry import default_providers, sandbox_acceptance
from durai.workflow_runner import CAPABILITY, build_workflow, start_workflow
from durai.workspace_manifest import manifest
from test_dev_loop import SUITE as SHARES_SUITE, loop_spec
from test_workspace_worker import SequenceModel

DATABASE = 'durai_test'
TOOL = Path(__file__).resolve().parents[1] / 'tools' / 'workflow.py'
JOURNAL, TESTFILE = '/workspace/journal.py', '/workspace/tests/test_journal.py'
OLD = b'def balanced(lines):\n    # Sample: Read lines 100 and -100. Return True.\n    return sum(lines) == 1\n'
FIXED = OLD.replace(b'== 1', b'== 0')
STUB = b'import unittest\n'
BASE = {JOURNAL: OLD, TESTFILE: STUB}
INIT = b''
GOAL_JOURNAL = (b'"""Sample: Post lines 100 and -100. Check balanced."""\nimport unittest\nfrom journal import balanced\n\n\n'
                b'class GoalJournalTests(unittest.TestCase):\n    def test_balanced(self):\n'
                b'        # Sample: Read lines 100 and -100. Check True.\n        self.assertTrue(balanced([100, -100]))\n')
GOAL_TESTS = (b'"""Sample: Read tests/test_journal.py. Check it holds test_unbalanced_rejected."""\nimport unittest\nfrom pathlib import Path\n\n\n'
              b'class GoalTestFileTests(unittest.TestCase):\n    def test_tester_wrote_goal(self):\n'
              b'        # Sample: Read test_journal.py. Check the text names test_unbalanced_rejected.\n'
              b'        text = Path(__file__).with_name("test_journal.py").read_text()\n        self.assertIn("def test_unbalanced_rejected", text)\n')
SUITE_IMPL = {'/workspace/tests/__init__.py': INIT, '/workspace/tests/test_goal_journal.py': GOAL_JOURNAL}
SUITE_TESTS = {'/workspace/tests/__init__.py': INIT, '/workspace/tests/test_goal_tests.py': GOAL_TESTS}
SUITE_ALL = dict(SUITE_IMPL, **SUITE_TESTS)
NEW_TEST = ('"""Sample: Post lines 100 and -50. Check the journal is rejected."""\nimport unittest\nfrom journal import balanced\n\n\n'
            'class JournalRejectTests(unittest.TestCase):\n    def test_unbalanced_rejected(self):\n'
            '        # Sample: Read lines 100 and -50. Check False.\n        self.assertFalse(balanced([100, -50]))\n')
SCRIPTS = {'"taskId": "T-impl-': [('edit_file', {'file_path': JOURNAL, 'old_string': '== 1', 'new_string': '== 0'})],
           '"taskId": "T-tests-': [('edit_file', {'file_path': TESTFILE, 'old_string': 'import unittest\n', 'new_string': NEW_TEST})]}


def text(files):
    # Sample: Read files {"/workspace/a.py": b"x = 1"}. Return {"/workspace/a.py": "x = 1"}.
    return {path: value.decode() for path, value in files.items()}


def load_tool():
    # Sample: Load tools/workflow.py as a module. Return it with its main function.
    spec = importlib.util.spec_from_file_location('durai_workflow_tool', TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def docker_ready():
    # Sample: Ask docker for the locked sandbox image. Return True when it exists. Return False when docker or the image is absent.
    if not shutil.which('docker'):
        return False
    try:
        evaluator = SandboxEvaluator(tempfile.gettempdir())
        return subprocess.run(['docker', 'image', 'inspect', evaluator.image], capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


class TaskModel(SequenceModel):
    scripts: dict = {}

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        # Sample: Read the system prompt of task T-impl. Send the journal fix. Read T-tests. Send the test file edit.
        prompt = ' '.join(str(message.content) for message in messages if message.type == 'system')
        self.steps = next(steps for key, steps in self.scripts.items() if key in prompt)
        return super()._generate(messages, stop, run_manager, **kwargs)


def scripted(attempt):
    # Sample: Return the scripted model for attempt 1. It picks the edit by the task id in its prompt.
    return TaskModel(steps=[], replies=[], scripts=SCRIPTS)


def acceptance_spec(suite, **changes):
    # Sample: Build the acceptance task for journal.py and the new test with the suite digest and the locked image.
    return loop_spec(suite, writePaths=[JOURNAL, TESTFILE], deliverables=[JOURNAL], deniedPaths=[],
                     sourceRevision=manifest(BASE)['digest'], **changes)


def team_goal():
    # Sample: Build the TEAM goal: journal.py for the implementer, tests/test_journal.py for the test writer, a reviewer, and the sandbox acceptance suite.
    tag = uid()[:8]
    parent = acceptance_spec(SUITE_ALL, roles=['implementer', 'test-writer', 'reviewer'], taskId='T-team-' + tag)
    members = {'implementer': {'taskId': 'T-impl-' + tag, 'deliverables': [JOURNAL], 'sourceRevision': manifest({JOURNAL: OLD})['digest'],
                               'protectedSuite': {'id': 'H-impl', 'digest': manifest(SUITE_IMPL)['digest']}},
               'test-writer': {'taskId': 'T-tests-' + tag, 'deliverables': [TESTFILE], 'deniedPaths': [],
                               'sourceRevision': manifest(BASE)['digest'],
                               'protectedSuite': {'id': 'H-tests', 'digest': manifest(SUITE_TESTS)['digest']}}}
    return {'spec': parent, 'files': text(BASE), 'suite': text(SUITE_ALL), 'budget': {'modelCalls': 30}, 'members': members,
            'suites': {'implementer': text(SUITE_IMPL), 'test-writer': text(SUITE_TESTS)}}


def events(workflow_id):
    # Sample: Replay run W-1. Return its event kinds with payloads in order.
    return [(row['kind'], row['payload']) for row in replay(workflow_id, database=DATABASE)]


class RegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply the core migrations and the checkpoint tables to durai_test.
        prepare(DATABASE)
        prepare_checkpoints(DATABASE)

    def test_default_providers_register_three_strategies(self):
        # Sample: Build the default registry for durai_test. Check workflow-step SINGLE, SUBAGENTS, and TEAM are available and a fourth name is not.
        providers = default_providers(DATABASE, {'scratch': tempfile.mkdtemp(prefix='durai-reg-')}, model_factory=scripted)
        self.assertEqual([providers.available(CAPABILITY, name) for name in ('SINGLE', 'SUBAGENTS', 'TEAM', 'SWARM')],
                         [True, True, True, False])
        self.assertIsInstance(providers.parts.leases, FileLeases)

    def test_acceptance_uses_sandbox(self):
        # Sample: Accept the shared workspace with journal.py fixed. A fake runner records the docker image command python -m unittest. Check passed True, the host Python is not started, and a broken journal.py gives passed False.
        calls = []

        class Recorder(SandboxEvaluator):
            def run(self, folder, argv, image=None, network=None):
                calls.append((list(argv), image, sorted(path.name for path in Path(folder).rglob('*.py'))))
                broken = b'== 1' in (Path(folder) / 'journal.py').read_bytes()
                return {'exitCode': 1 if broken and 'unittest' in argv else 0, 'output': 'scripted', 'started': True}

        evaluator = Recorder(tempfile.mkdtemp(prefix='durai-accept-'))
        accept = sandbox_acceptance(evaluator)
        goal = {'spec': acceptance_spec(SUITE_ALL), 'suite': text(SUITE_ALL), 'files': text(BASE)}
        context = {'goal': goal}
        workspace = FolderWorkspace('S-1.team', {JOURNAL: FIXED, TESTFILE: NEW_TEST.encode()})
        with mock.patch('durai.task_evaluator.subprocess.run', side_effect=AssertionError('host process')):
            verdict = accept(workspace, {'id': 'S-1'}, context)
            workspace.write(JOURNAL, OLD)
            broken = accept(workspace, {'id': 'S-1'}, context)
        self.assertTrue(verdict['passed'], verdict)
        self.assertRegex(verdict['evidence'], '^[0-9a-f]{64}$')
        self.assertEqual([call[0][:3] for call in calls[:2]], [['python', '-m', 'compileall'], ['python', '-m', 'unittest']])
        self.assertEqual(calls[0][1], evaluator.image)
        workspace.close()
        self.assertFalse(broken['passed'])
        self.assertIn('python-unittest', broken['detail'])

    @unittest.skipUnless(docker_ready(), 'Docker or the locked sandbox image is not available')
    def test_acceptance_real_sandbox(self):
        # Sample: Run the real Docker sandbox on the shared workspace. Fixed journal.py and the new test give passed True. Broken journal.py gives passed False.
        accept = sandbox_acceptance(SandboxEvaluator(tempfile.mkdtemp(prefix='durai-accept-')))
        goal = {'spec': acceptance_spec(SUITE_ALL), 'suite': text(SUITE_ALL), 'files': text(BASE)}
        workspace = FolderWorkspace('S-1.team', {JOURNAL: FIXED, TESTFILE: NEW_TEST.encode()})
        verdict = accept(workspace, {'id': 'S-1'}, {'goal': goal})
        workspace.write(JOURNAL, OLD)
        broken = accept(workspace, {'id': 'S-1'}, {'goal': goal})
        workspace.close()
        self.assertTrue(verdict['passed'], verdict)
        self.assertFalse(broken['passed'], broken)


class TeamIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply the core migrations and the checkpoint tables to durai_test.
        prepare(DATABASE)
        prepare_checkpoints(DATABASE)

    def test_two_member_dev_loop_team(self):
        # Sample: Explicit TEAM step. The implementer fixes journal.py and the test writer writes tests/test_journal.py. Each runs the real dev loop with a scripted model under its own lease on one shared workspace. The reviewer reads. Sandbox acceptance passes once.
        providers = default_providers(DATABASE, {'scratch': tempfile.mkdtemp(prefix='durai-team-')}, model_factory=scripted)
        spec = {'format': 'durai-workflow/v1', 'id': 'wf-' + uid()[:8],
                'steps': [{'id': 'S-1', 'goal': team_goal(), 'strategy': 'TEAM'}]}
        result = start_workflow(build_workflow(spec, providers, DATABASE), spec['id'], DATABASE)
        seen = events(spec['id'])
        names = [kind for kind, payload in seen]
        finished = {payload['memberId']: payload for kind, payload in seen if kind == 'TEAM_MEMBER_FINISHED'}
        self.assertEqual(result['status'], 'SUCCEEDED', [payload for kind, payload in seen if kind in ('STEP_FINISHED', 'TEAM_MEMBER_FINISHED')])
        self.assertEqual({name: item['state'] for name, item in finished.items()},
                         {'implementer': 'SUCCEEDED', 'test-writer': 'SUCCEEDED', 'reviewer': 'SUCCEEDED'})
        self.assertEqual((names.count('LEASE_CLAIMED'), names.count('LEASE_RELEASED'), names.count('ACCEPTANCE_FINISHED')), (2, 2, 1))
        accepted = next(payload for kind, payload in seen if kind == 'ACCEPTANCE_FINISHED')
        self.assertTrue(accepted['passed'], accepted)
        self.assertIn('Sandbox PASS', accepted['detail'])
        with transaction('durai_runtime', DATABASE) as db:
            rows = db.execute('SELECT path,owner,state FROM durai_coordination.file_lease WHERE worktree=%s ORDER BY path',
                              (spec['id'] + '/S-1',)).fetchall()
        self.assertEqual([(row['path'], row['owner'], row['state']) for row in rows],
                         [(JOURNAL, 'implementer', 'RELEASED'), (TESTFILE, 'test-writer', 'RELEASED')])


class CliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply the core migrations and the checkpoint tables to durai_test.
        prepare(DATABASE)
        prepare_checkpoints(DATABASE)

    def definition(self):
        # Sample: Write W-1 with one SINGLE shares step and an after gate to a temp folder. Return the folder and the definition.
        spec = loop_spec()
        goal = {'spec': spec, 'files': text({'/workspace/shares.py': __import__('test_dev_loop').BASE['/workspace/shares.py']}),
                'suite': text(SHARES_SUITE)}
        definition = {'format': 'durai-workflow/v1', 'id': 'wf-' + uid()[:8],
                      'steps': [{'id': 'S-1', 'goal': goal, 'strategy': 'SINGLE',
                                 'gate': {'when': 'after', 'question': 'Accept S-1?', 'deadlineSeconds': 3600}}]}
        folder = Path(tempfile.mkdtemp(prefix='durai-cli-'))
        (folder / (definition['id'] + '.json')).write_text(json.dumps(definition), encoding='utf-8')
        return folder, definition

    def run_tool(self, *arguments):
        # Sample: Run tools/workflow.py main with scripted model M-1 on durai_test. Return the exit code and the JSON line it printed.
        tool = load_tool()
        out = io.StringIO()
        shares = [('edit_file', {'file_path': '/workspace/shares.py', 'old_string': 'return lots * lot_size - 1',
                                 'new_string': 'return lots * lot_size'})]
        code = tool.main(['--database', DATABASE, '--scratch', tempfile.mkdtemp(prefix='durai-cli-run-')] + list(arguments),
                         model_factory=lambda attempt: SequenceModel(steps=shares, replies=[]), out=out)
        return code, json.loads(out.getvalue())

    def test_workflow_cli_start_and_resume(self):
        # Sample: Run start for W-1 with a scripted model. The dev loop passes and the after gate opens. Approve as the reviewer. Run resume. Check W-1 SUCCEEDED.
        folder, definition = self.definition()
        path = str(folder / (definition['id'] + '.json'))
        code, started = self.run_tool('start', '--definition', path)
        self.assertEqual((code, started['waitingAtGate'], started['workflowId']), (0, True, definition['id']), started)
        subject = next(payload['subjectDigest'] for kind, payload in events(definition['id']) if kind == 'GATE_OPENED')
        approve(definition['id'], subject, 'Synthetic test only.', database=DATABASE)
        code, resumed = self.run_tool('resume', '--definition', path)
        self.assertEqual((code, resumed['status'], resumed['steps']), (0, 'SUCCEEDED', {'S-1': 'SUCCEEDED'}), resumed)
        self.assertEqual([payload['state'] for kind, payload in events(definition['id']) if kind == 'WORKFLOW_FINISHED'], ['SUCCEEDED'])

    def test_workflow_cli_sweep(self):
        # Sample: Run start for W-1 and approve the gate. Run sweep over the folder. Check W-1 is resumed once and a second sweep resumes none.
        folder, definition = self.definition()
        code, started = self.run_tool('start', '--definition', str(folder / (definition['id'] + '.json')))
        subject = next(payload['subjectDigest'] for kind, payload in events(definition['id']) if kind == 'GATE_OPENED')
        approve(definition['id'], subject, 'Synthetic test only.', database=DATABASE)
        first = self.run_tool('sweep', '--definitions', str(folder))[1]['resumed']
        second = self.run_tool('sweep', '--definitions', str(folder))[1]['resumed']
        self.assertIn(definition['id'], first)
        self.assertNotIn(definition['id'], second)

    def test_live_model_needs_accept_spend(self):
        # Sample: Run start without --accept-spend and without an injected model. Check the tool stops before any model call.
        tool = load_tool()
        with self.assertRaises(SystemExit) as stopped:
            tool.main(['--database', DATABASE, 'start', '--definition', 'W-1.json'])
        self.assertIn('--accept-spend', str(stopped.exception))


class WorkflowRecoverCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply the core migrations and the checkpoint tables to durai_test.
        prepare(DATABASE)
        prepare_checkpoints(DATABASE)

    def run_tool(self, *arguments):
        # Sample: Run tools/workflow.py main with scripted model M-1 on durai_test. Return the exit code and the JSON line it printed.
        tool, out = load_tool(), io.StringIO()
        shares = [('edit_file', {'file_path': '/workspace/shares.py', 'old_string': 'return lots * lot_size - 1',
                                 'new_string': 'return lots * lot_size'})]
        code = tool.main(['--database', DATABASE, '--scratch', tempfile.mkdtemp(prefix='durai-recover-run-')] + list(arguments),
                         model_factory=lambda attempt: SequenceModel(steps=shares, replies=[]), out=out)
        return code, json.loads(out.getvalue())

    def test_recover_command_resumes_workflow(self):
        # Sample: Start workflow W-1 with 3 steps and an after gate on S-1. The run stops at the gate after step 1. Approve as the reviewer. Run "workflow.py recover W-1". Check exit 0, status SUCCEEDED, and 3 steps SUCCEEDED.
        files = text({'/workspace/shares.py': __import__('test_dev_loop').BASE['/workspace/shares.py']})
        gate = {'when': 'after', 'question': 'Accept S-1?', 'deadlineSeconds': 3600}
        steps = [dict({'id': name, 'goal': {'spec': loop_spec(), 'files': files, 'suite': text(SHARES_SUITE)}, 'strategy': 'SINGLE'},
                      **({'gate': gate} if name == 'S-1' else {})) for name in ('S-1', 'S-2', 'S-3')]
        definition = {'format': 'durai-workflow/v1', 'id': 'wf-' + uid()[:8], 'steps': steps}
        folder = Path(tempfile.mkdtemp(prefix='durai-recover-'))
        self.addCleanup(shutil.rmtree, folder, True)
        (folder / (definition['id'] + '.json')).write_text(json.dumps(definition), encoding='utf-8')
        code, started = self.run_tool('start', '--definition', str(folder / (definition['id'] + '.json')))
        self.assertEqual((code, started['waitingAtGate'], started['steps']), (0, True, {'S-1': 'SUCCEEDED'}), started)
        subject = next(payload['subjectDigest'] for kind, payload in events(definition['id']) if kind == 'GATE_OPENED')
        approve(definition['id'], subject, 'Synthetic test only.', database=DATABASE)
        code, recovered = self.run_tool('recover', definition['id'], '--definitions', str(folder))
        self.assertEqual((code, recovered['status']), (0, 'SUCCEEDED'), recovered)
        self.assertEqual(recovered['steps'], {'S-1': 'SUCCEEDED', 'S-2': 'SUCCEEDED', 'S-3': 'SUCCEEDED'})
        self.assertEqual([payload['stepId'] for kind, payload in events(definition['id']) if kind == 'STEP_FINISHED'], ['S-1', 'S-2', 'S-3'])

    def test_recover_command_unknown_id(self):
        # Sample: Run "workflow.py recover W-404" for a workflow with no events. Check exit 2 and an error naming W-404.
        code, printed = self.run_tool('recover', 'W-404-' + uid()[:8])
        self.assertEqual(code, 2)
        self.assertIn('Unknown workflow', printed['error'])


if __name__ == '__main__':
    unittest.main()
