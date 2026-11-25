"""Sample: Run implementer M-1 on journal.py and test writer M-2 on test_journal.py with write leases. Review. Accept once. Check SUCCEEDED."""
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
import psycopg
from langchain_core.messages import ToolMessage
from durai.common import Conflict, uid
from durai.db import connect, prepare, transaction
from durai.edit_guard import EditGuardMiddleware
from durai.execution_events import replay
from durai.file_leases import FileLeases
from durai.harness import prepare_checkpoints
from durai.providers import Providers
from durai.qualification import FIXTURE, MODULE, load
from durai.strategy_team import TeamExecutor, register_team, roster
from durai.task_contract import build_task
from durai.workflow_runner import CAPABILITY, build_workflow, start_workflow
from durai.workspace_manifest import materialize
from test_task_contract import feature_spec

DATABASE = 'durai_test'
JOURNAL, TESTS = '/w/journal.py', '/w/test_journal.py'
BASE = {JOURNAL: 'def post(lines):\n    return None\n', TESTS: 'import unittest\n'}
ROLES = ['implementer', 'test-writer', 'reviewer']


class Clock:
    def __init__(self, now=1000.0):
        # Sample: Start the clock at second 1000. Move it by 11 seconds. Read 1011.
        self.now = now

    def __call__(self):
        # Sample: Read the clock. Return the current second.
        return self.now


def worktree():
    # Sample: Name a new worktree W-ab12cd34 so tests never share lease rows.
    return 'wt-' + uid()[:8]


def rows(tree, path=None):
    # Sample: Read all lease rows of journal.py in W-1 by generation. Return state and owner per row.
    with transaction('durai_runtime', DATABASE) as db:
        return db.execute('SELECT generation,owner,role,state,step_id FROM durai_coordination.file_lease WHERE worktree=%s AND path=%s ORDER BY generation',
                          (tree, path or JOURNAL)).fetchall()


def goal(roles=ROLES, calls=30):
    # Sample: Build a goal that writes journal.py and test_journal.py with roles implementer, test-writer, reviewer and 30 model calls.
    return {'spec': {'writePaths': [JOURNAL, TESTS], 'roles': list(roles)}, 'files': dict(BASE), 'budget': {'modelCalls': calls}}


class Acceptance:
    def __init__(self, passed=True):
        # Sample: Pass acceptance. Keep the count of calls and the files seen.
        self.passed, self.calls, self.seen = passed, 0, []

    def __call__(self, workspace, step, context):
        # Sample: Read the shared workspace. Count one call. Return passed.
        self.calls += 1
        self.seen.append(workspace.files())
        return {'passed': self.passed, 'detail': 'scripted acceptance'}


class Members:
    def __init__(self, states=None, boom=(), script=None):
        # Sample: Script reviewer FAILED. Keep a call log and the files the reviewer saw.
        self.states, self.boom, self.script = states or {}, boom, script or {}
        self.calls, self.lock, self.reviewed = [], threading.Lock(), None

    def __call__(self, step, context):
        # Sample: Run implementer. Write journal.py with its lease. Return SUCCEEDED with 3 model calls.
        name = context['memberId']
        with self.lock:
            self.calls.append(name)
        if name in self.boom:
            raise RuntimeError('worker crashed')
        if name in self.script:
            self.script[name](step, context)
        elif context['role'] != 'reviewer':
            for path in step['goal']['spec']['writePaths']:
                context['workspace'].write(path, '# edited by ' + name + '\n')
        else:
            self.reviewed = context['workspace'].files()
        return {'state': self.states.get(name, 'SUCCEEDED'), 'detail': 'fake ' + name, 'usage': {'modelCalls': 3}}


def run(members, acceptance=None, goal_value=None, leases=None, workflow_id=None, **options):
    # Sample: Run step S-1 of W-1 with fake members. Return the result, the emitted events, the acceptance, and the workflow id.
    events, acceptance = [], acceptance or Acceptance()
    workflow_id = workflow_id or 'W-' + uid()[:8]
    executor = TeamExecutor(members, acceptance=acceptance, leases=leases or FileLeases(DATABASE), **options)
    context = {'workflowId': workflow_id, 'stepId': 'S-1', 'goal': goal_value or goal(), 'steps': {},
               'emit': lambda kind, payload: events.append((kind, payload))}
    result = executor({'id': 'S-1', 'goal': context['goal'], 'strategy': 'TEAM'}, context)
    return result, events, acceptance, workflow_id


def kinds(events):
    # Sample: Read events. Return only their kinds in order.
    return [kind for kind, payload in events]


def recorded(workflow_id):
    # Sample: Replay run W-1. Return its event kinds with payloads in order.
    return [(row['kind'], row['payload']) for row in replay(workflow_id, database=DATABASE)]


class TeamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply the core migrations, including core_0018, and the checkpoint tables to durai_test.
        prepare(DATABASE)
        prepare_checkpoints(DATABASE)

    def guard(self, leases=None, owner='M-1', tree=None):
        # Sample: Build the Task B shell with one guard for owner M-1 and a record of applied writes. Without leases the guard has no lease check.
        temporary = tempfile.TemporaryDirectory(prefix='durai-team-guard-')
        self.addCleanup(temporary.cleanup)
        root, files = Path(temporary.name), load(FIXTURE / 'workspace')
        materialize(root, files)
        task = build_task(feature_spec(writePaths=[MODULE], editScope={MODULE: ['validate_change']}))['task']
        self.writes = []
        guard = EditGuardMiddleware(task, files, root, lambda *args: None, leases=(leases, tree, owner) if leases else None)
        call = SimpleNamespace(tool_call={'id': 'C-1', 'name': 'edit_file', 'args': {
            'file_path': MODULE, 'old_string': 'validate_change', 'new_string': 'validate_change', 'replace_all': False}})

        def handler(request):
            # Sample: Apply one accepted replacement and record one write.
            self.writes.append(request.tool_call['id'])
            return ToolMessage(content='applied', tool_call_id=request.tool_call['id'])
        return guard, call, handler

    def test_write_without_lease_refused(self):
        # Sample: Member M-1 edits change_manifest.py with no lease. Check a LEASE_REQUIRED tool error and zero writes. Without the leases argument the same call is not refused for a lease.
        tree, leases = worktree(), FileLeases(DATABASE)
        guard, call, handler = self.guard(leases, 'M-1', tree)
        result = guard.wrap_tool_call(call, handler)
        self.assertEqual(result.status, 'error')
        self.assertIn('LEASE_REQUIRED', result.content)
        self.assertEqual(self.writes, [])
        plain, call, handler = self.guard()
        self.assertNotIn('LEASE', plain.wrap_tool_call(call, handler).content)

    def test_write_with_live_lease_not_refused_for_lease(self):
        # Sample: Member M-1 holds a live lease on change_manifest.py. Check the guard raises no lease finding. Member M-2 edits the same path. Check LEASE_REQUIRED naming M-1.
        tree, leases = worktree(), FileLeases(DATABASE)
        leases.claim(tree, MODULE, 'M-1', 'implementer')
        guard, call, handler = self.guard(leases, 'M-1', tree)
        self.assertEqual(guard.lease_findings(call.tool_call), [])
        other, call, handler = self.guard(leases, 'M-2', tree)
        self.assertIn('held by M-1', other.lease_findings(call.tool_call)[0])

    def test_expired_lease_is_lost(self):
        # Sample: M-1 claims journal.py for 10 seconds at second 1000. At second 1011 the guard check gives LOST. The row is LOST, LEASE_LOST is recorded once, and renew is refused.
        tree, clock, workflow_id = worktree(), Clock(), 'W-' + uid()[:8]
        leases = FileLeases(DATABASE, clock=clock)
        lease = leases.claim(tree, MODULE, 'M-1', 'implementer', 10, workflow_id, 'S-1')
        clock.now = 1011.0
        guard, call, handler = self.guard(leases, 'M-1', tree)
        result = guard.wrap_tool_call(call, handler)
        self.assertIn('LEASE_LOST', result.content)
        self.assertEqual(self.writes, [])
        self.assertEqual([(row['generation'], row['state']) for row in rows(tree, MODULE)], [(1, 'LOST')])
        self.assertEqual(leases.check(tree, MODULE, 'M-1')['status'], 'LOST')
        lost = [payload for kind, payload in recorded(workflow_id) if kind == 'LEASE_LOST']
        self.assertEqual([(item['memberId'], item['stepId'], item['generation']) for item in lost], [('M-1', 'S-1', 1)])
        with self.assertRaisesRegex(Conflict, 'LEASE_STALE|LEASE_LOST'):
            leases.renew(lease)
        self.assertEqual(leases.claim(tree, MODULE, 'M-1', 'implementer').generation, 2)

    def test_lease_transfer_recorded(self):
        # Sample: Test writer M-2 holds test_journal.py generation 1 and hands it to implementer M-1. Check old RELEASED, new HELD generation 2 for M-1, and one LEASE_TRANSFERRED with fromMemberId M-2.
        tree, workflow_id, leases = worktree(), 'W-' + uid()[:8], FileLeases(DATABASE)
        first = leases.claim(tree, TESTS, 'M-2', 'test-writer', 600, workflow_id, 'S-1')
        second = leases.transfer(first, 'M-1', 'implementer')
        self.assertEqual((second.generation, second.owner, second.role), (2, 'M-1', 'implementer'))
        self.assertEqual([(row['generation'], row['owner'], row['state']) for row in rows(tree, TESTS)], [(1, 'M-2', 'RELEASED'), (2, 'M-1', 'HELD')])
        moved = [payload for kind, payload in recorded(workflow_id) if kind == 'LEASE_TRANSFERRED']
        self.assertEqual([(item['memberId'], item['fromMemberId'], item['generation'], item['stepId']) for item in moved], [('M-1', 'M-2', 2, 'S-1')])
        with self.assertRaises(Conflict):
            leases.transfer(first, 'M-3', 'implementer')

    def test_second_claim_conflicts(self):
        # Sample: M-1 claims journal.py. M-2 claims the same path. Check Conflict naming M-1 and one HELD row. M-1 claims again. Check generation 1 stays.
        tree, leases = worktree(), FileLeases(DATABASE)
        first = leases.claim(tree, JOURNAL, 'M-1', 'implementer')
        with self.assertRaisesRegex(Conflict, 'LEASE_CONFLICT.*M-1'):
            leases.claim(tree, JOURNAL, 'M-2', 'implementer')
        self.assertEqual(leases.claim(tree, JOURNAL, 'M-1', 'implementer').generation, first.generation)
        self.assertEqual([(row['generation'], row['owner'], row['state']) for row in rows(tree)], [(1, 'M-1', 'HELD')])

    def test_concurrent_claims_have_one_winner(self):
        # Sample: Five members claim journal.py of one worktree at the same time. Check exactly one claim returns and four raise Conflict.
        tree, leases, results = worktree(), FileLeases(DATABASE), []

        def attempt(name):
            # Sample: Claim journal.py as member N-1. Record WON or LOST.
            try:
                leases.claim(tree, JOURNAL, name, 'implementer')
                results.append('WON')
            except Conflict:
                results.append('CONFLICT')
        threads = [threading.Thread(target=attempt, args=('N-' + str(index),)) for index in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(results), ['CONFLICT'] * 4 + ['WON'])

    def test_generation_compare_and_set(self):
        # Sample: M-1 holds generation 1 and transfers to M-2 as generation 2. Check M-1 renew, release and transfer with generation 1 raise Conflict. M-2 renews generation 2 and keeps it.
        tree, leases = worktree(), FileLeases(DATABASE)
        old = leases.claim(tree, JOURNAL, 'M-1', 'implementer')
        new = leases.transfer(old, 'M-2', 'implementer')
        for action in (lambda: leases.renew(old), lambda: leases.release(old), lambda: leases.transfer(old, 'M-3', 'implementer')):
            with self.assertRaisesRegex(Conflict, 'LEASE_STALE'):
                action()
        self.assertEqual(leases.renew(new).generation, 2)
        leases.release(new)
        with self.assertRaisesRegex(Conflict, 'LEASE_STALE'):
            leases.renew(new)
        self.assertEqual(leases.claim(tree, JOURNAL, 'M-3', 'implementer').generation, 3)

    def test_reviewer_has_no_write(self):
        # Sample: Reviewer R-1 claims journal.py. Check LEASE_REFUSED and no row. The edit guard for R-1 refuses a write with LEASE_REQUIRED. A transfer to a reviewer is refused.
        tree, leases = worktree(), FileLeases(DATABASE)
        with self.assertRaisesRegex(Conflict, 'LEASE_REFUSED'):
            leases.claim(tree, JOURNAL, 'R-1', 'reviewer')
        self.assertEqual(rows(tree), [])
        guard, call, handler = self.guard(leases, 'R-1', tree)
        self.assertIn('LEASE_REQUIRED', guard.wrap_tool_call(call, handler).content)
        held = leases.claim(tree, JOURNAL, 'M-1', 'implementer')
        with self.assertRaisesRegex(Conflict, 'LEASE_REFUSED'):
            leases.transfer(held, 'R-1', 'reviewer')
        with self.assertRaisesRegex(Conflict, 'no write paths|no write leases'):
            roster({'writePaths': [JOURNAL], 'roles': [{'role': 'reviewer', 'writePaths': [JOURNAL]}]})

    def test_team_acceptance_runs_once(self):
        # Sample: Run implementer, test writer, and reviewer. All SUCCEEDED. Check acceptance called once after every TEAM_MEMBER_FINISHED, both edits in the shared workspace, the reviewer saw both, and every lease RELEASED.
        members = Members()
        result, events, acceptance, workflow_id = run(members)
        self.assertEqual((result['state'], acceptance.calls), ('SUCCEEDED', 1))
        names = kinds(events)
        self.assertEqual((names.count('TEAM_MEMBER_STARTED'), names.count('TEAM_MEMBER_FINISHED'), names.count('ACCEPTANCE_FINISHED')), (3, 3, 1))
        self.assertEqual(names.index('ACCEPTANCE_FINISHED'), len(names) - 1)
        self.assertEqual(acceptance.seen[0][JOURNAL], b'# edited by implementer\n')
        self.assertEqual(acceptance.seen[0][TESTS], b'# edited by test-writer\n')
        self.assertEqual(members.reviewed[JOURNAL], b'# edited by implementer\n')
        self.assertTrue(all(payload['stepId'] == 'S-1' and payload['memberId'] for kind, payload in events))
        seen = [kind for kind, payload in recorded(workflow_id)]
        self.assertEqual((seen.count('LEASE_CLAIMED'), seen.count('LEASE_RELEASED')), (2, 2))
        with transaction('durai_runtime', DATABASE) as db:
            states = db.execute('SELECT DISTINCT state FROM durai_coordination.file_lease WHERE workflow_id=%s', (workflow_id,)).fetchall()
        self.assertEqual([row['state'] for row in states], ['RELEASED'])

    def test_failed_writer_blocks_reviewer_and_acceptance(self):
        # Sample: Let the test writer end FAILED. Check the reviewer is CANCELLED and never runs, acceptance is never called, the step is FAILED, and no lease stays HELD.
        members = Members(states={'test-writer': 'FAILED'})
        result, events, acceptance, workflow_id = run(members)
        finished = {payload['memberId']: payload['state'] for kind, payload in events if kind == 'TEAM_MEMBER_FINISHED'}
        self.assertEqual(finished, {'implementer': 'SUCCEEDED', 'test-writer': 'FAILED', 'reviewer': 'CANCELLED'})
        self.assertEqual((result['state'], acceptance.calls, 'reviewer' in members.calls), ('FAILED', 0, False))
        with transaction('durai_runtime', DATABASE) as db:
            held = db.execute("SELECT count(*) AS n FROM durai_coordination.file_lease WHERE workflow_id=%s AND state='HELD'", (workflow_id,)).fetchone()['n']
        self.assertEqual(held, 0)

    def test_write_outside_own_lease_loses_member(self):
        # Sample: Let the implementer write test_journal.py that the test writer holds. Check Conflict LEASE_REQUIRED, implementer LOST, journal.py unchanged, acceptance never called.
        def trespass(step, context):
            context['workspace'].write(TESTS, '# stolen\n')
        result, events, acceptance, workflow_id = run(Members(script={'implementer': trespass}))
        lost = next(payload for kind, payload in events if kind == 'TEAM_MEMBER_FINISHED' and payload['memberId'] == 'implementer')
        self.assertEqual(lost['state'], 'LOST')
        self.assertIn('LEASE_REQUIRED', lost['detail'])
        self.assertEqual((result['state'], acceptance.calls), ('FAILED', 0))

    def test_handoff_lets_receiver_write(self):
        # Sample: Test writer M-2 writes test_journal.py and hands it to implementer M-1. Before the hand-off M-1 write is refused. After it M-1 writes generation 2. Check one LEASE_TRANSFERRED and a passing step.
        handed, tried, refused = threading.Event(), threading.Event(), []

        def writer(step, context):
            context['workspace'].write(TESTS, '# first draft\n')
            self.assertTrue(tried.wait(10))
            context['leases'].transfer(TESTS, 'implementer')
            handed.set()

        def implementer(step, context):
            try:
                context['workspace'].write(TESTS, '# too early\n')
            except Conflict as error:
                refused.append(str(error))
            tried.set()
            self.assertTrue(handed.wait(10))
            context['workspace'].write(TESTS, '# reviewed by implementer\n')
            context['workspace'].write(JOURNAL, '# edited by implementer\n')
        acceptance = Acceptance()
        result, events, acceptance, workflow_id = run(Members(script={'test-writer': writer, 'implementer': implementer}),
                                                      goal_value={'spec': {'writePaths': [JOURNAL, TESTS], 'roles': [
                                                          {'role': 'implementer', 'writePaths': [JOURNAL]}, {'role': 'test-writer', 'writePaths': [TESTS]}]},
                                                                  'files': dict(BASE), 'budget': {'modelCalls': 20}}, acceptance=acceptance)
        self.assertEqual(result['state'], 'SUCCEEDED', result)
        self.assertEqual(len(refused), 1)
        self.assertIn('LEASE_REQUIRED', refused[0])
        moved = [payload for kind, payload in recorded(workflow_id) if kind == 'LEASE_TRANSFERRED']
        self.assertEqual([(item['memberId'], item['fromMemberId'], item['generation']) for item in moved], [('implementer', 'test-writer', 2)])
        self.assertEqual(acceptance.seen[0][TESTS], b'# reviewed by implementer\n')

    def test_claim_conflict_fails_step_before_members_start(self):
        # Sample: Outsider X-1 already holds journal.py of the same worktree. Run the team. Check LEASE_CONFLICT, no member started, acceptance never called, and X-1 still holds generation 1.
        leases, members = FileLeases(DATABASE), Members()
        workflow_id = 'W-' + uid()[:8]
        held = leases.claim(workflow_id + '/S-1', JOURNAL, 'X-1', 'implementer')
        result, events, acceptance, workflow_id = run(members, leases=leases, workflow_id=workflow_id)
        self.assertEqual(result['state'], 'FAILED')
        self.assertIn('LEASE_CONFLICT', result['detail'])
        self.assertEqual((members.calls, acceptance.calls, 'TEAM_MEMBER_STARTED' in kinds(events)), ([], 0, False))
        self.assertEqual(rows(workflow_id + '/S-1')[0]['owner'], held.owner)

    def test_roster_refusals(self):
        # Sample: Ask for 4 members, an unknown role, two members on journal.py, and a path outside writePaths. Check TEAM_REFUSED each time. Default paths give journal.py to the implementer and test_journal.py to the test writer.
        for roles in (ROLES + ['implementer'], ['planner'],
                      [{'role': 'implementer', 'writePaths': [JOURNAL]}, {'role': 'test-writer', 'memberId': 'tw', 'writePaths': [JOURNAL]}],
                      [{'role': 'implementer', 'writePaths': ['/w/other.py']}]):
            with self.assertRaisesRegex(Conflict, 'TEAM_REFUSED'):
                roster({'writePaths': [JOURNAL, TESTS], 'roles': roles})
        found = roster({'writePaths': [JOURNAL, TESTS], 'roles': ROLES})
        self.assertEqual([(item['memberId'], item['writePaths']) for item in found],
                         [('implementer', [JOURNAL]), ('test-writer', [TESTS]), ('reviewer', [])])

    def test_auto_role_split_runs_team(self):
        # Sample: AUTO step S-1 has roles implementer, test-writer, reviewer. SINGLE and TEAM are registered. Check no STRATEGY_FALLBACK, STEP_STARTED strategy TEAM decided by role-split, three members, acceptance once, and LEASE_CLAIMED events with stepId and memberId.
        acceptance, single = Acceptance(), []
        providers = Providers()
        providers.register(CAPABILITY, 'SINGLE', lambda step, context: single.append(step['id']) or {'state': 'SUCCEEDED', 'detail': 'single'})
        register_team(providers, TeamExecutor(Members(), acceptance=acceptance, leases=FileLeases(DATABASE)))
        spec = {'format': 'durai-workflow/v1', 'id': 'wf-' + uid()[:8], 'steps': [{'id': 'S-1', 'goal': goal(), 'strategy': 'AUTO'}]}
        result = start_workflow(build_workflow(spec, providers, DATABASE), spec['id'], DATABASE)
        seen = recorded(spec['id'])
        names = [kind for kind, payload in seen]
        self.assertEqual((result['status'], single, acceptance.calls), ('SUCCEEDED', [], 1))
        self.assertNotIn('STRATEGY_FALLBACK', names)
        started = next(payload for kind, payload in seen if kind == 'STEP_STARTED')
        self.assertEqual((started['strategy'], started['decidedBy']), ('TEAM', 'decision.execution-strategy:role-split'))
        self.assertEqual((names.count('TEAM_MEMBER_STARTED'), names.count('TEAM_MEMBER_FINISHED'), names.count('ACCEPTANCE_FINISHED')), (3, 3, 1))
        claimed = sorted((payload['stepId'], payload['memberId'], payload['path']) for kind, payload in seen if kind == 'LEASE_CLAIMED')
        self.assertEqual(claimed, [('S-1', 'implementer', JOURNAL), ('S-1', 'test-writer', TESTS)])

    def test_auto_role_split_falls_back_without_team(self):
        # Sample: AUTO step S-1 has roles but only SINGLE is registered. Check STRATEGY_FALLBACK wanted TEAM used SINGLE.
        providers = Providers()
        providers.register(CAPABILITY, 'SINGLE', lambda step, context: {'state': 'SUCCEEDED', 'detail': 'single'})
        spec = {'format': 'durai-workflow/v1', 'id': 'wf-' + uid()[:8], 'steps': [{'id': 'S-1', 'goal': goal(), 'strategy': 'AUTO'}]}
        start_workflow(build_workflow(spec, providers, DATABASE), spec['id'], DATABASE)
        fallback = next(payload for kind, payload in recorded(spec['id']) if kind == 'STRATEGY_FALLBACK')
        self.assertEqual((fallback['wanted'], fallback['used']), ('TEAM', 'SINGLE'))


class MigrationTests(unittest.TestCase):
    def test_core_0018_applies_on_disposable_server(self):
        # Sample: Create database durai_restore_<12 hex> and apply all core migrations. Check core_0018.sql is recorded, the table has row level security forced, the foreign login sees no trading rows, DELETE is refused, and a RELEASED row cannot become HELD. Drop the database.
        name = 'durai_restore_' + uid().replace('-', '')[:12]
        try:
            applied = prepare(name)
            self.assertIn('core_0018.sql', [item['name'] for item in applied])
            with transaction('postgres', name) as db:
                flags = db.execute("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='durai_coordination.file_lease'::regclass").fetchone()
                self.assertEqual((flags['relrowsecurity'], flags['relforcerowsecurity']), (True, True))
            with transaction('durai_runtime', name) as db:
                db.execute("INSERT INTO durai_coordination.file_lease(application_id,worktree,path,generation,owner,role,state,expires_at) "
                           "VALUES(durai_core.application_id(),'W-1','/w/journal.py',1,'M-1','implementer','HELD',now()+interval '1 minute')")
            with transaction('durai_foreign', name) as db:
                self.assertEqual(db.execute('SELECT * FROM durai_coordination.file_lease').fetchall(), [])
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with transaction('durai_runtime', name) as db:
                    db.execute('DELETE FROM durai_coordination.file_lease')
            with self.assertRaises(psycopg.errors.CheckViolation):
                with transaction('postgres', name) as db:
                    db.execute('DELETE FROM durai_coordination.file_lease')
            with transaction('durai_runtime', name) as db:
                db.execute("UPDATE durai_coordination.file_lease SET state='RELEASED'")
            with self.assertRaises(psycopg.errors.CheckViolation):
                with transaction('durai_runtime', name) as db:
                    db.execute("UPDATE durai_coordination.file_lease SET state='HELD'")
        finally:
            with connect('postgres', 'postgres', autocommit=True) as admin:
                admin.execute('DROP DATABASE IF EXISTS ' + name + ' WITH (FORCE)')

    def test_one_held_lease_per_path(self):
        # Sample: Insert journal.py generation 1 HELD for M-1. Insert generation 2 HELD for M-2. Check UniqueViolation. Release generation 1 and insert generation 2 HELD. Check it succeeds.
        prepare(DATABASE)
        tree = worktree()
        insert = ("INSERT INTO durai_coordination.file_lease(application_id,worktree,path,generation,owner,role,state,expires_at) "
                  "VALUES(durai_core.application_id(),%s,'/w/journal.py',%s,%s,'implementer','HELD',now()+interval '1 minute')")
        with transaction('durai_runtime', DATABASE) as db:
            db.execute(insert, (tree, 1, 'M-1'))
        with self.assertRaises(psycopg.errors.UniqueViolation):
            with transaction('durai_runtime', DATABASE) as db:
                db.execute(insert, (tree, 2, 'M-2'))
        with transaction('durai_runtime', DATABASE) as db:
            db.execute("UPDATE durai_coordination.file_lease SET state='RELEASED' WHERE worktree=%s", (tree,))
            db.execute(insert, (tree, 2, 'M-2'))
        self.assertEqual([(row['generation'], row['state']) for row in rows(tree)], [(1, 'RELEASED'), (2, 'HELD')])


if __name__ == '__main__':
    unittest.main()
