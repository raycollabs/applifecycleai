"""Sample: Keep a lease live by heartbeat. Resume a killed team. Add a git worktree. Run two subagents in parallel. Review with a scripted model."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from durai.common import Conflict, uid
from durai.db import prepare
from durai.execution_events import replay
from durai.file_leases import FileLeases
from durai.harness import prepare_checkpoints
from durai.strategy_subagents import FolderWorkspace, SubagentsExecutor, package_digest
from durai.strategy_team import LeasedWorkspace, ReviewerMember, TeamRun, parse_verdict, workspace_diff
from durai.workflow_gates import emit as gate_emit
from durai.workflow_registry import default_providers
from durai.workflow_runner import CAPABILITY, build_workflow, recover_workflow, start_workflow
from durai.workspace_disk import DiskWorkspace
from durai.workspace_git import GitWorktreeWorkspace
from durai.workspace_manifest import manifest
from test_dev_loop import loop_spec
from test_strategy_subagents_integration import BASE as MODULE_BASE, SUITES as MODULE_SUITES
from test_strategy_team import Acceptance, BASE, Clock, JOURNAL, Members, TESTS, goal, kinds, recorded, rows, run, worktree
from test_workflow_wiring import TaskModel, docker_ready, events, team_goal, text
from test_workspace_worker import SequenceModel

DATABASE = 'durai_test'
GIT = ['git', '-c', 'safe.directory=*', '-c', 'user.name=durai-test', '-c', 'user.email=durai-test@example.invalid']
TESTS_FOLDER = Path(__file__).resolve().parent
CHILD = TESTS_FOLDER / 'team_crash_child.py'
KILL_CHILD = TESTS_FOLDER / 'workflow_kill_child.py'
ROOT = TESTS_FOLDER.parent


def git(folder, *arguments):
    # Sample: Run git -c safe.directory=* in folder F-1 with "worktree list". Return its text. A failure raises.
    done = subprocess.run(GIT + ['-C', str(folder)] + list(arguments), capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        raise AssertionError('git ' + ' '.join(arguments) + ' failed: ' + done.stderr[-300:])
    return done.stdout


def worktrees(repo):
    # Sample: Read "git worktree list" of repo R-1. Return one entry per worktree. A new repo gives 1 entry.
    return [line for line in git(repo, 'worktree', 'list', '--porcelain').splitlines() if line.startswith('worktree ')]


class LeaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply the core migrations and the checkpoint tables to durai_test.
        prepare(DATABASE)
        prepare_checkpoints(DATABASE)

    def test_heartbeat_keeps_lease_live(self):
        # Sample: M-1 claims journal.py for 1 second and runs a heartbeat every 0.3 seconds. After 2.2 seconds the lease is LIVE with generation 1. M-2 claims test_journal.py for 1 second with no heartbeat. After 2.2 seconds the lease is LOST.
        tree, leases, stop = worktree(), FileLeases(DATABASE), threading.Event()
        beating = leases.claim(tree, JOURNAL, 'M-1', 'implementer', 1)
        leases.claim(tree, TESTS, 'M-2', 'test-writer', 1)
        thread = leases.heartbeat(beating, 0.3, stop, 1)
        time.sleep(2.2)
        live, silent = leases.check(tree, JOURNAL, 'M-1'), leases.check(tree, TESTS, 'M-2')
        stop.set()
        thread.join(5)
        self.assertEqual((live['status'], live['lease'].generation), ('LIVE', 1))
        self.assertEqual(silent['status'], 'LOST')
        self.assertFalse(thread.is_alive())

    def test_heartbeat_stops_on_release(self):
        # Sample: Team run R-1 claims journal.py for member M-1 and starts one heartbeat thread. Release M-1. Check the thread ends within 2 seconds and the lease row is RELEASED.
        tree, member = worktree(), 'M-' + uid()[:6]
        run_ = TeamRun(FileLeases(DATABASE), tree, 'W-' + uid()[:8], 'S-1', [{'memberId': member, 'role': 'implementer'}], 30)
        run_.every = 0.2
        run_.claim({'memberId': member, 'role': 'implementer', 'writePaths': [JOURNAL]})
        name = 'lease-heartbeat-' + member
        self.assertIn(name, [thread.name for thread in threading.enumerate()])
        run_.release(member)
        deadline = time.time() + 2
        while name in [thread.name for thread in threading.enumerate()] and time.time() < deadline:
            time.sleep(0.05)
        self.assertNotIn(name, [thread.name for thread in threading.enumerate()])
        self.assertEqual([row['state'] for row in rows(tree)], ['RELEASED'])
        self.assertEqual(run_.lost, {})

    def test_heartbeat_stops_on_transfer(self):
        # Sample: M-1 holds journal.py with a heartbeat. Transfer it to M-2. Check the heartbeat of M-1 ends with no lost flag and the heartbeat of M-2 runs. Release M-2. Check it ends.
        tree, first, second = worktree(), 'A-' + uid()[:6], 'B-' + uid()[:6]
        members = [{'memberId': first, 'role': 'implementer'}, {'memberId': second, 'role': 'test-writer'}]
        run_ = TeamRun(FileLeases(DATABASE), tree, 'W-' + uid()[:8], 'S-1', members, 30)
        run_.every = 0.2
        run_.claim({'memberId': first, 'role': 'implementer', 'writePaths': [JOURNAL]})
        run_.transfer(first, JOURNAL, second)
        time.sleep(0.8)
        names = [thread.name for thread in threading.enumerate()]
        self.assertNotIn('lease-heartbeat-' + first, names)
        self.assertIn('lease-heartbeat-' + second, names)
        self.assertEqual(run_.lost, {})
        run_.release(second)
        time.sleep(0.5)
        self.assertNotIn('lease-heartbeat-' + second, [thread.name for thread in threading.enumerate()])

    def test_lost_lease_blocks_writes(self):
        # Sample: M-1 claims journal.py for 60 seconds at second 1000. The clock moves to second 1100. The heartbeat renew is refused as LOST. A write of M-1 to journal.py raises LEASE_LOST naming the heartbeat refusal. The file keeps "v1".
        clock, tree, member = Clock(), worktree(), 'M-' + uid()[:6]
        run_ = TeamRun(FileLeases(DATABASE, clock=clock), tree, 'W-' + uid()[:8], 'S-1', [{'memberId': member, 'role': 'implementer'}], 60)
        run_.every = 0.05
        workspace = FolderWorkspace('S-1.team', {JOURNAL: 'v1'})
        self.addCleanup(workspace.close)
        run_.claim({'memberId': member, 'role': 'implementer', 'writePaths': [JOURNAL]})
        LeasedWorkspace(workspace, run_, member).write(JOURNAL, 'v2')
        workspace.write(JOURNAL, 'v1')
        clock.now = 1100.0
        deadline = time.time() + 5
        while member not in run_.lost and time.time() < deadline:
            time.sleep(0.05)
        self.assertIn(member, run_.lost)
        with self.assertRaisesRegex(Conflict, 'LEASE_LOST.*heartbeat refusal'):
            LeasedWorkspace(workspace, run_, member).write(JOURNAL, 'v3')
        self.assertEqual(workspace.read(JOURNAL), b'v1')
        run_.release_all()


class TeamResumeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply the core migrations and the checkpoint tables to durai_test.
        prepare(DATABASE)
        prepare_checkpoints(DATABASE)

    def executor(self, scratch, workflow_id, **options):
        # Sample: Build a TEAM executor with fake members, acceptance, leases on durai_test, and a disk scratch folder. Keep the acceptance and the member log.
        from durai.strategy_team import TeamExecutor
        self.acceptance, self.members = Acceptance(), Members()
        return TeamExecutor(self.members, acceptance=self.acceptance, leases=FileLeases(DATABASE), scratch=scratch, **options)

    def call(self, executor, workflow_id, files=None):
        # Sample: Run step S-1 of workflow W-1 with events written to the durai_test event log. Return the result.
        value = goal()
        if files is not None:
            value['files'] = files
        context = {'workflowId': workflow_id, 'stepId': 'S-1', 'goal': value, 'steps': {},
                   'emit': lambda kind, payload: gate_emit(workflow_id, kind, payload, DATABASE)}
        return executor({'id': 'S-1', 'goal': value, 'strategy': 'TEAM'}, context)

    @unittest.skipUnless(docker_ready(), 'Docker or the locked sandbox image is not available')
    def test_team_resumes_after_restart(self):
        # Sample: TEAM step with implementer and test writer. The implementer fixes journal.py and finishes. The test writer blocks holding its lease. Kill the process. After 3.5 seconds the lease has expired. Recover from a new registry. The workspace folder is re-opened, the implementer is not run again, the test writer claims generation 2 and writes the test file, and the Docker acceptance passes.
        scratch = Path(tempfile.mkdtemp(prefix='durai-resume-'))
        self.addCleanup(shutil.rmtree, scratch, True)
        spec = {'format': 'durai-workflow/v1', 'id': 'wf-' + uid()[:8], 'steps': [{'id': 'S-1', 'goal': team_goal(), 'strategy': 'TEAM'}]}
        definition = scratch / 'definition.json'
        definition.write_text(json.dumps(spec), encoding='utf-8')
        environment = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT), str(TESTS_FOLDER)]))
        child = subprocess.Popen([sys.executable, str(CHILD), str(scratch), str(definition)], env=environment,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(child.kill)
        deadline, ready = time.time() + 300, False
        while time.time() < deadline and child.poll() is None and not ready:
            seen = events(spec['id']) if self.has_run(spec['id']) else []
            ready = any(kind == 'TEAM_MEMBER_FINISHED' and payload['memberId'] == 'implementer' and payload['state'] == 'SUCCEEDED' for kind, payload in seen) \
                and any(kind == 'TEAM_MEMBER_STARTED' and payload['memberId'] == 'test-writer' for kind, payload in seen)
            time.sleep(0.5)
        self.assertTrue(ready, 'child did not reach the blocked test writer; exit code ' + str(child.poll()))
        child.kill()
        child.wait(30)
        time.sleep(3.5)
        providers = default_providers(DATABASE, {'scratch': str(scratch), 'leaseSeconds': 3}, model_factory=__import__('test_workflow_wiring').scripted)
        result = recover_workflow(build_workflow(spec, providers, DATABASE), spec['id'], DATABASE)
        seen = events(spec['id'])
        names = [kind for kind, payload in seen]
        started = [payload['memberId'] for kind, payload in seen if kind == 'TEAM_MEMBER_STARTED']
        created = next(payload for kind, payload in seen if kind == 'TEAM_WORKSPACE_CREATED')
        resumed = next(payload for kind, payload in seen if kind == 'TEAM_RESUMED')
        accepted = next(payload for kind, payload in seen if kind == 'ACCEPTANCE_FINISHED')
        self.assertEqual(result['status'], 'SUCCEEDED', [payload for kind, payload in seen if kind in ('STEP_FINISHED', 'TEAM_MEMBER_FINISHED')])
        self.assertEqual((names.count('TEAM_WORKSPACE_CREATED'), names.count('TEAM_WORKSPACE_REOPENED')), (1, 1))
        self.assertEqual((started.count('implementer'), started.count('test-writer'), started.count('reviewer')), (1, 2, 1))
        self.assertEqual(resumed['skipped'], ['implementer'])
        self.assertEqual(resumed['leases'], {'test-writer': {TESTS_PATH: 'LOST'}, 'reviewer': {}})
        self.assertTrue(accepted['passed'], accepted)
        self.assertFalse(Path(created['path']).exists())
        states = [(row['generation'], row['state']) for row in rows(spec['id'] + '/S-1', TESTS_PATH)]
        self.assertEqual(states, [(1, 'LOST'), (2, 'RELEASED')])

    def has_run(self, workflow_id):
        # Sample: Ask whether run W-1 has any event yet. Return False before the child writes its first event.
        try:
            return bool(replay(workflow_id, database=DATABASE))
        except Exception:
            return False

    def test_missing_workspace_fails_closed(self):
        # Sample: W-1 recorded TEAM_WORKSPACE_CREATED for a folder. The folder is gone. Run the step again. Check FAILED with WORKSPACE_LOST, a WORKSPACE_LOST event, no member started, no lease claimed, and no acceptance.
        scratch, workflow_id = Path(tempfile.mkdtemp(prefix='durai-lost-')), 'W-' + uid()[:8]
        self.addCleanup(shutil.rmtree, scratch, True)
        gate_emit(workflow_id, 'TEAM_WORKSPACE_CREATED', {'stepId': 'S-1', 'path': str(scratch / 'gone'), 'baseDigest': '0' * 64}, DATABASE)
        executor = self.executor(scratch, workflow_id)
        result = self.call(executor, workflow_id)
        names = [kind for kind, payload in recorded(workflow_id)]
        self.assertEqual(result['state'], 'FAILED')
        self.assertIn('WORKSPACE_LOST', result['detail'])
        self.assertIn('WORKSPACE_LOST', names)
        self.assertEqual((self.members.calls, self.acceptance.calls), ([], 0))
        self.assertEqual(rows(workflow_id + '/S-1'), [])

    def test_changed_base_fails_closed(self):
        # Sample: W-1 recorded a folder made from base journal.py "v1". The goal now has journal.py "v9". Run the step again. Check FAILED with WORKSPACE_LOST naming the base digest and no member started.
        scratch, workflow_id = Path(tempfile.mkdtemp(prefix='durai-base-')), 'W-' + uid()[:8]
        self.addCleanup(shutil.rmtree, scratch, True)
        folder = DiskWorkspace(scratch / 'old', BASE)
        gate_emit(workflow_id, 'TEAM_WORKSPACE_CREATED', {'stepId': 'S-1', 'path': str(folder.root), 'baseDigest': folder.base_digest}, DATABASE)
        executor = self.executor(scratch, workflow_id)
        result = self.call(executor, workflow_id, dict(BASE, **{JOURNAL: 'v9'}))
        self.assertEqual(result['state'], 'FAILED')
        self.assertRegex(result['detail'], 'WORKSPACE_LOST.*base digest')
        self.assertEqual(self.members.calls, [])

    def test_disk_workspace_team_runs_and_cleans(self):
        # Sample: Run a TEAM step with a scratch folder and fake members. Check SUCCEEDED, one TEAM_WORKSPACE_CREATED, acceptance once, and the folder removed at the end.
        scratch, workflow_id = Path(tempfile.mkdtemp(prefix='durai-disk-')), 'W-' + uid()[:8]
        self.addCleanup(shutil.rmtree, scratch, True)
        executor = self.executor(scratch, workflow_id)
        result = self.call(executor, workflow_id)
        created = [payload for kind, payload in recorded(workflow_id) if kind == 'TEAM_WORKSPACE_CREATED']
        self.assertEqual(result['state'], 'SUCCEEDED', result)
        self.assertEqual((len(created), self.acceptance.calls), (1, 1))
        self.assertFalse(Path(created[0]['path']).exists())
        self.assertEqual(self.acceptance.seen[0][JOURNAL], b'# edited by implementer\n')


TESTS_PATH = '/workspace/tests/test_journal.py'


class WorkflowKillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply the core migrations and the checkpoint tables to durai_test.
        prepare(DATABASE)
        prepare_checkpoints(DATABASE)

    def test_kill_mid_loop_then_recover_applies_once(self):
        # Sample: Start W-2 with S-1, S-2, S-3 in a child process. S-1 appends its effect and commits. S-2 blocks. Kill the child with no clean shutdown. Recover from a new registry. Check status SUCCEEDED and the effects file holds S-1, S-2, S-3 once each.
        import workflow_kill_child as child_module
        folder = Path(tempfile.mkdtemp(prefix='durai-kill-'))
        self.addCleanup(shutil.rmtree, folder, True)
        effects = folder / 'effects.txt'
        spec = {'format': 'durai-workflow/v1', 'id': 'wf-' + uid()[:8],
                'steps': [{'id': name, 'goal': 'Book 2 lots of 100 in ' + name, 'strategy': 'SINGLE'} for name in ('S-1', 'S-2', 'S-3')]}
        definition = folder / 'definition.json'
        definition.write_text(json.dumps(spec), encoding='utf-8')
        environment = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT), str(TESTS_FOLDER)]))
        child = subprocess.Popen([sys.executable, str(KILL_CHILD), str(effects), str(definition)], env=environment,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(child.kill)
        deadline, ready = time.time() + 120, False
        while time.time() < deadline and child.poll() is None and not ready:
            try:
                seen = [(row['kind'], row['payload']) for row in replay(spec['id'], database=DATABASE)]
            except Exception:
                seen = []
            ready = any(kind == 'STEP_FINISHED' and payload['stepId'] == 'S-1' for kind, payload in seen)                 and any(kind == 'STEP_STARTED' and payload['stepId'] == 'S-2' for kind, payload in seen)
            time.sleep(0.2)
        self.assertTrue(ready, 'child did not block inside S-2; exit code ' + str(child.poll()))
        child.kill()
        child.wait(30)
        self.assertEqual(effects.read_text(encoding='utf-8').split(), ['S-1'])
        result = recover_workflow(build_workflow(spec, child_module.providers_for(effects), DATABASE), spec['id'], DATABASE)
        finished = [row['payload']['stepId'] for row in replay(spec['id'], database=DATABASE) if row['kind'] == 'STEP_FINISHED']
        self.assertEqual((result['status'], result['steps']), ('SUCCEEDED', {'S-1': 'SUCCEEDED', 'S-2': 'SUCCEEDED', 'S-3': 'SUCCEEDED'}))
        self.assertEqual(effects.read_text(encoding='utf-8').split(), ['S-1', 'S-2', 'S-3'])
        self.assertEqual(finished, ['S-1', 'S-2', 'S-3'])




class TeamWorkspaceRetentionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply the core migrations and the checkpoint tables to durai_test.
        prepare(DATABASE)
        prepare_checkpoints(DATABASE)

    def failed_run(self, scratch):
        # Sample: Run a TEAM step on scratch folder F-1 where the implementer returns FAILED. Return the result, the events, and the workflow id.
        result, seen, acceptance, workflow_id = run(Members(states={'implementer': 'FAILED'}), scratch=scratch)
        self.assertEqual((result['state'], acceptance.calls), ('FAILED', 0))
        return result, seen, workflow_id

    def test_failed_team_workspace_kept(self):
        # Sample: Run a TEAM with an implementer that returns FAILED. Check the workspace folder still exists, TEAM_WORKSPACE_KEPT holds its path, and the digest equals the digest of the folder files. A passed run removes its folder.
        scratch = Path(tempfile.mkdtemp(prefix='durai-kept-'))
        self.addCleanup(shutil.rmtree, scratch, True)
        _, seen, _ = self.failed_run(scratch)
        kept = next(payload for kind, payload in seen if kind == 'TEAM_WORKSPACE_KEPT')
        created = next(payload for kind, payload in seen if kind == 'TEAM_WORKSPACE_CREATED')
        self.assertEqual(kept['path'], created['path'])
        self.assertTrue(Path(kept['path']).is_dir())
        self.assertEqual(kept['digest'], package_digest(DiskWorkspace(kept['path'], None, reopen=True).files()))
        result, passed, _, _ = run(Members(), scratch=scratch)
        created = next(payload for kind, payload in passed if kind == 'TEAM_WORKSPACE_CREATED')
        self.assertEqual(result['state'], 'SUCCEEDED')
        self.assertNotIn('TEAM_WORKSPACE_KEPT', [kind for kind, payload in passed])
        self.assertFalse(Path(created['path']).exists())

    def test_failed_workspace_cap(self):
        # Sample: Fail 6 TEAM runs on one scratch folder. Check 5 kept folders. The folder of run 1 is removed. The folders of runs 2 to 6 exist.
        scratch = Path(tempfile.mkdtemp(prefix='durai-cap-'))
        self.addCleanup(shutil.rmtree, scratch, True)
        paths = []
        for _ in range(6):
            _, seen, _ = self.failed_run(scratch)
            paths.append(Path(next(payload['path'] for kind, payload in seen if kind == 'TEAM_WORKSPACE_KEPT')))
        self.assertEqual([path.exists() for path in paths], [False, True, True, True, True, True])
        self.assertEqual(len(list(scratch.glob('*/*/team'))), 5)


class WorkspaceTests(unittest.TestCase):
    def repo(self):
        # Sample: Create a git repo with /workspace/journal.py "v1" in one commit. Return the repo folder and the commit id.
        folder = Path(tempfile.mkdtemp(prefix='durai-repo-'))
        self.addCleanup(shutil.rmtree, folder, True)
        git(folder, 'init', '-q')
        (folder / 'workspace').mkdir()
        (folder / 'workspace' / 'journal.py').write_bytes(b'v1')
        (folder / 'workspace' / 'location_search.py').write_bytes(b'w1')
        git(folder, 'add', '.')
        git(folder, 'commit', '-q', '-m', 'base')
        return folder, git(folder, 'rev-parse', 'HEAD').strip()

    def test_git_worktree_workspace_round_trip(self):
        # Sample: Add a worktree of commit C-1 with base journal.py "v1". Check files() holds journal.py "v1" only. Write journal.py "v2" and test_journal.py "t1". Read both back. The main repo journal.py stays "v1". A base "v0" is refused with BASE_MISMATCH.
        repo, commit = self.repo()
        scratch = Path(tempfile.mkdtemp(prefix='durai-gitscratch-'))
        self.addCleanup(shutil.rmtree, scratch, True)
        workspace = GitWorktreeWorkspace(repo, commit, 'S-1.sub-1', scratch, {'/workspace/journal.py': 'v1'})
        self.addCleanup(workspace.close)
        self.assertEqual(workspace.files(), {'/workspace/journal.py': b'v1'})
        self.assertEqual(len(worktrees(repo)), 2)
        workspace.write('/workspace/journal.py', 'v2')
        workspace.write('/workspace/tests/test_journal.py', 't1')
        self.assertEqual(workspace.read('/workspace/journal.py'), b'v2')
        self.assertEqual(workspace.files(), {'/workspace/journal.py': b'v2', '/workspace/tests/test_journal.py': b't1'})
        self.assertEqual((repo / 'workspace' / 'journal.py').read_bytes(), b'v1')
        with self.assertRaisesRegex(Conflict, 'BASE_MISMATCH'):
            GitWorktreeWorkspace(repo, commit, 'S-1.sub-2', scratch, {'/workspace/journal.py': 'v0'})
        self.assertEqual(len(worktrees(repo)), 2)

    def test_git_worktree_removed_on_close(self):
        # Sample: Add a worktree for the repo. Close it. Check git worktree list shows the main tree only and the folder is gone. Close again. Nothing happens.
        repo, commit = self.repo()
        scratch = Path(tempfile.mkdtemp(prefix='durai-gitscratch-'))
        self.addCleanup(shutil.rmtree, scratch, True)
        workspace = GitWorktreeWorkspace(repo, commit, 'S-1.merge', scratch)
        folder = workspace.folder
        self.assertTrue(folder.exists())
        workspace.close()
        workspace.close()
        self.assertEqual(len(worktrees(repo)), 1)
        self.assertFalse(folder.exists())

    def test_goal_workspace_selects_git(self):
        # Sample: Goal workspace "git" with repo R-1 and commit C-1. Two subagents write their module. Check each child got a GitWorktreeWorkspace, the merge passed, and no extra worktree stays. Goal workspace "cloud" is refused with WORKSPACE_REFUSED. Goal workspace "folder" uses folder copies.
        repo, commit = self.repo()
        scratch = Path(tempfile.mkdtemp(prefix='durai-gitscratch-'))
        self.addCleanup(shutil.rmtree, scratch, True)
        kinds_seen, accepted = [], []

        def child(step, context):
            # Sample: Child writes its own module and returns the package.
            kinds_seen.append(type(context['workspace']).__name__)
            path = context['goal']['spec']['writePaths'][0]
            return {'state': 'SUCCEEDED', 'package': {'files': {path: b'fixed'}}}

        def accept(workspace, step, context):
            # Sample: Read the merge workspace. Record its type and files. Pass.
            accepted.append((type(workspace).__name__, workspace.files()))
            return {'passed': True}

        executor = SubagentsExecutor(child, acceptance=accept, max_parallel=2, git={'repo': str(repo), 'baseCommit': commit, 'scratch': str(scratch)})
        base = {'/workspace/journal.py': 'v1', '/workspace/location_search.py': 'w1'}

        def step(workspace):
            # Sample: Run step S-1 with goal workspace W-1 over two modules. Return the result.
            value = {'spec': {'writePaths': sorted(base)}, 'files': base, 'budget': {'modelCalls': 4}, 'workspace': workspace}
            return executor({'id': 'S-1', 'goal': value}, {'goal': value, 'workflowId': 'W-1', 'emit': lambda kind, payload: None})

        self.assertEqual(step('git')['state'], 'SUCCEEDED')
        self.assertEqual(kinds_seen, ['GitWorktreeWorkspace', 'GitWorktreeWorkspace'])
        self.assertEqual(accepted[0][0], 'GitWorktreeWorkspace')
        self.assertEqual(len(worktrees(repo)), 1)
        refused = step('cloud')
        self.assertEqual(refused['state'], 'FAILED')
        self.assertIn('WORKSPACE_REFUSED', refused['detail'])
        self.assertEqual(step('folder')['state'], 'SUCCEEDED')
        self.assertEqual(kinds_seen[2:], ['FolderWorkspace', 'FolderWorkspace'])


class ParallelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply the core migrations and the checkpoint tables to durai_test.
        prepare(DATABASE)
        prepare_checkpoints(DATABASE)

    @unittest.skipUnless(docker_ready(), 'Docker or the locked sandbox image is not available')
    def test_two_subagents_overlap_and_merge(self):
        # Sample: Explicit SUBAGENTS step over journal.py and location_search.py with maxParallel 2 and the real dev loop. A barrier of 2 holds each loop at its first model call until both loops reached it. Check both SUBAGENT_STARTED events come before the first SUBAGENT_FINISHED, both SUCCEEDED, two MERGE_APPLIED, and the Docker acceptance passes once.
        tag = uid()[:8]
        suites_all = {path: value for suite in MODULE_SUITES.values() for path, value in suite.items()}
        subtasks, suites, scripts = {}, {}, {}
        fixes = {'/workspace/journal.py': ('jrnl', {'old_string': '== 1', 'new_string': '== 0'}),
                 '/workspace/location_search.py': ('loc', {'old_string': 'max(points', 'new_string': 'min(points'})}
        for path, (short, change) in fixes.items():
            module = path[:-3]
            subtasks[module] = {'taskId': 'T-' + short + '-' + tag, 'deliverables': [path], 'sourceRevision': manifest({path: MODULE_BASE[path]})['digest'],
                                'protectedSuite': {'id': 'H-' + short, 'digest': manifest(MODULE_SUITES[path])['digest']}}
            suites[module] = text(MODULE_SUITES[path])
            scripts['"taskId": "T-' + short + '-' + tag + '"'] = [('edit_file', dict(change, file_path=path))]
        gate, state = threading.Lock(), {'count': 0}
        barrier = threading.Barrier(2, timeout=240)

        def factory(attempt):
            # Sample: Return the scripted model for the task named in the prompt. The first two calls wait for each other.
            with gate:
                state['count'] += 1
                first = state['count'] <= 2
            if first:
                barrier.wait()
            return TaskModel(steps=[], replies=[], scripts=scripts)

        parent = loop_spec(suites_all, writePaths=list(MODULE_BASE), deliverables=list(MODULE_BASE), deniedPaths=[],
                           sourceRevision=manifest(MODULE_BASE)['digest'], taskId='T-par-' + tag)
        goal_value = {'spec': parent, 'files': text(MODULE_BASE), 'suite': text(suites_all), 'budget': {'modelCalls': 40},
                      'subtasks': subtasks, 'suites': suites}
        providers = default_providers(DATABASE, {'scratch': tempfile.mkdtemp(prefix='durai-par-'), 'maxParallel': 2}, model_factory=factory)
        spec = {'format': 'durai-workflow/v1', 'id': 'wf-' + uid()[:8], 'steps': [{'id': 'S-1', 'goal': goal_value, 'strategy': 'SUBAGENTS'}]}
        result = start_workflow(build_workflow(spec, providers, DATABASE), spec['id'], DATABASE)
        rows_ = [(row['sequence'], row['kind'], row['payload']) for row in replay(spec['id'], database=DATABASE)]
        names = [kind for _, kind, _ in rows_]
        finished = {payload['subagentId']: payload for _, kind, payload in rows_ if kind == 'SUBAGENT_FINISHED'}
        first_finish = next(sequence for sequence, kind, _ in rows_ if kind == 'SUBAGENT_FINISHED')
        starts = [sequence for sequence, kind, _ in rows_ if kind == 'SUBAGENT_STARTED']
        self.assertEqual(result['status'], 'SUCCEEDED', [payload for _, kind, payload in rows_ if kind in ('STEP_FINISHED', 'SUBAGENT_FINISHED', 'MERGE_CONFLICT')])
        self.assertEqual(len(starts), 2)
        self.assertTrue(all(sequence < first_finish for sequence in starts), (starts, first_finish))
        self.assertEqual({name: item['state'] for name, item in finished.items()}, {'sub-1': 'SUCCEEDED', 'sub-2': 'SUCCEEDED'})
        self.assertEqual((names.count('MERGE_APPLIED'), names.count('ACCEPTANCE_FINISHED'), state['count']), (2, 1, 2))
        accepted = next(payload for _, kind, payload in rows_ if kind == 'ACCEPTANCE_FINISHED')
        self.assertTrue(accepted['passed'], accepted)
        self.assertIn('Sandbox PASS', accepted['detail'])


class StubReviewer:
    def __init__(self, reply):
        # Sample: Script one reply text. Keep every message list and every bind_tools request.
        self.reply, self.messages, self.bound = reply, [], []

    def invoke(self, messages):
        # Sample: Receive a system and a user message. Keep them. Return the scripted reply.
        self.messages.append(messages)
        return SimpleNamespace(content=self.reply)

    def bind_tools(self, tools, **options):
        # Sample: A call here means the reviewer asked for tools. Keep it so the test fails.
        self.bound.append(tools)
        return self


RULES = [{'id': 'R-sample', 'rule': 'Each changed function has one Sample comment.', 'audience': ['coding-worker']}]
OBJECTIVE = 'Post lines 100 and -100 as a balanced journal.'


class ReviewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply the core migrations and the checkpoint tables to durai_test.
        prepare(DATABASE)
        prepare_checkpoints(DATABASE)

    def team(self, reply):
        # Sample: Run a TEAM step with fake writers and a model-backed reviewer that answers reply. Return the result, the model, the acceptance, the events, and the workflow id.
        model = StubReviewer(reply)
        writers = Members()
        members = {'implementer': writers, 'test-writer': writers, 'reviewer': ReviewerMember(model, RULES)}
        value = goal()
        value['spec']['objective'] = OBJECTIVE
        result, seen, acceptance, workflow_id = run(members, goal_value=value)
        return result, model, acceptance, seen, workflow_id

    def test_reviewer_approve(self):
        # Sample: Writers change journal.py and test_journal.py. The reviewer model replies APPROVE with no findings. Check the step SUCCEEDED, acceptance ran once, the model was called once, and its prompt holds the diff line "+# edited by implementer", the objective, and the rule R-sample.
        result, model, acceptance, seen, _ = self.team('{"verdict": "APPROVE", "findings": []}')
        prompt = ' '.join(message.content for message in model.messages[0])
        finished = next(payload for kind, payload in seen if kind == 'TEAM_MEMBER_FINISHED' and payload['memberId'] == 'reviewer')
        self.assertEqual(result['state'], 'SUCCEEDED', result)
        self.assertEqual((acceptance.calls, len(model.messages)), (1, 1))
        self.assertIn('+# edited by implementer', prompt)
        self.assertIn(OBJECTIVE, prompt)
        self.assertIn('R-sample', prompt)
        self.assertEqual((finished['state'], finished['usage']), ('SUCCEEDED', {'modelCalls': 1}))

    def test_reviewer_changes_fails_step(self):
        # Sample: The reviewer model replies CHANGES with the finding "journal.py has no Sample comment". Check the step FAILED naming that finding, the reviewer FAILED, and acceptance never ran. A reply that is prose also fails closed.
        result, model, acceptance, seen, _ = self.team('{"verdict": "CHANGES", "findings": ["journal.py has no Sample comment"]}')
        self.assertEqual(result['state'], 'FAILED')
        self.assertIn('journal.py has no Sample comment', result['detail'])
        self.assertEqual(acceptance.calls, 0)
        self.assertEqual(self.team('Looks fine to me.')[0]['state'], 'FAILED')
        self.assertIsNone(parse_verdict('{"verdict": "CHANGES", "findings": []}'))
        self.assertIsNone(parse_verdict('{"verdict": "APPROVE", "findings": [], "extra": 1}'))
        self.assertEqual(parse_verdict(' {"verdict": "APPROVE", "findings": []} ')['verdict'], 'APPROVE')

    def test_reviewer_has_no_write_tools(self):
        # Sample: Run the model-backed reviewer. Check the model got no bind_tools call, the system prompt says "You have no tools", the reviewer holds no lease row, and a write by the reviewer through the leased workspace raises LEASE_REQUIRED and leaves the file unchanged.
        result, model, acceptance, seen, workflow_id = self.team('{"verdict": "APPROVE", "findings": []}')
        tree = workflow_id + '/S-1'
        self.assertEqual(result['state'], 'SUCCEEDED', result)
        self.assertEqual(model.bound, [])
        self.assertIn('You have no tools', model.messages[0][0].content)
        self.assertNotIn('reviewer', {row['owner'] for row in rows(tree, JOURNAL) + rows(tree, TESTS)})
        workspace = FolderWorkspace('S-1.team', {JOURNAL: 'v1'})
        self.addCleanup(workspace.close)
        guard = TeamRun(FileLeases(DATABASE), tree, workflow_id, 'S-1', [{'memberId': 'reviewer', 'role': 'reviewer'}], 30)
        with self.assertRaisesRegex(Conflict, 'LEASE_REQUIRED'):
            LeasedWorkspace(workspace, guard, 'reviewer').write(JOURNAL, 'v2')
        self.assertEqual(workspace.read(JOURNAL), b'v1')

    def test_workspace_diff_names_changed_files_only(self):
        # Sample: Base journal.py "v1" and test_journal.py "t1". The workspace has journal.py "v2" and test_journal.py "t1". Check the diff names journal.py with -v1 and +v2 and does not name test_journal.py.
        diff = workspace_diff({JOURNAL: b'v1', TESTS: b't1'}, {JOURNAL: b'v2', TESTS: b't1'})
        self.assertIn('-v1', diff)
        self.assertIn('+v2', diff)
        self.assertNotIn(TESTS, diff)


if __name__ == '__main__':
    unittest.main()
