"""Sample: Start the dashboard on durai_test. Wait W-1 at gate G-1 and import one receipt. Read the endpoints and check rows."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
import urllib.error
import urllib.request
from durai.bench_store import add_profile, import_receipt, prepare_bench
from durai.common import uid
from durai.dashboard_server import scrub, serve
from durai.db import prepare, transaction
from durai.execution_events import known_secrets
from durai.harness import prepare_checkpoints
from durai.providers import Providers
from durai.workflow_runner import CAPABILITY, build_workflow, start_workflow
from test_bench_store import START, make_receipt

DATABASE = 'durai_test'
PORT = 54431
BASE = 'http://127.0.0.1:' + str(PORT)
ENDPOINTS = ('/api/workflows', '/api/gates', '/api/runs', '/api/scorecard', '/api/experiments', '/api/decisions', '/api/agents', '/api/leases')
PROTECTED = Path(__file__).resolve().parent / 'protected'


def scripted(step, context):
    # Sample: Run S-1. Return SUCCEEDED with a detail that holds apiKey sk-test-123456789.
    return {'state': 'SUCCEEDED', 'detail': 'done apiKey sk-test-123456789 for ' + step['id']}


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Prepare durai_test, wait W-1 at gate G-1, and start the dashboard on port 54431.
        prepare(DATABASE)
        prepare_checkpoints(DATABASE)
        prepare_bench(DATABASE)
        cls.folder = tempfile.TemporaryDirectory()
        cls.workflow_id = 'W-' + uid()[:8]
        spec = {'format': 'durai-workflow/v1', 'id': cls.workflow_id,
                'steps': [{'id': 'S-1', 'goal': 'Validate lots', 'strategy': 'SINGLE'},
                          {'id': 'S-2', 'goal': 'Book 2 lots of 100', 'strategy': 'SINGLE',
                           'gate': {'when': 'before', 'question': 'Release S-2?', 'deadlineSeconds': 3600}}]}
        providers = Providers()
        providers.register(CAPABILITY, 'SINGLE', scripted)
        start_workflow(build_workflow(spec, providers, DATABASE), cls.workflow_id, DATABASE)
        ready = threading.Event()

        def connected(server):
            cls.server = server
            ready.set()
        cls.thread = threading.Thread(target=serve, args=(PORT,), kwargs={'ready': connected, 'database': DATABASE}, daemon=True)
        cls.thread.start()
        if not ready.wait(5):
            raise RuntimeError('Dashboard test server did not start')
        cls.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
        cls.opener.open(BASE + '/').read()

    @classmethod
    def tearDownClass(cls):
        # Sample: Stop the dashboard and remove the temp folder.
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.folder.cleanup()

    def get(self, path):
        # Sample: Read /api/gates with the session cookie. Return the list under rows.
        with self.opener.open(BASE + path) as response:
            return json.load(response)['rows']

    def counts(self):
        # Sample: Count event rows and bench run rows. Return both numbers.
        with transaction('durai_runtime', DATABASE) as db:
            events = db.execute('SELECT count(*) AS n FROM durai_coordination.event').fetchone()['n']
            runs = db.execute('SELECT count(*) AS n FROM durai_bench.run').fetchone()['n']
        return events, runs

    def test_dashboard_is_read_only(self):
        # Sample: POST to /api/workflows. Check 405 and no row change.
        before = self.counts()
        for method in ('POST', 'PUT', 'DELETE', 'PATCH'):
            request = urllib.request.Request(BASE + '/api/workflows', data=b'{}', method=method,
                                             headers={'Content-Type': 'application/json', 'Origin': BASE})
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.opener.open(request)
            self.assertEqual(caught.exception.code, 405)
            self.assertEqual(caught.exception.headers['Allow'], 'GET')
        self.assertEqual(self.counts(), before)

    def test_pending_gate_listed(self):
        # Sample: Workflow W-1 waits at gate G-1. Check /api/gates lists G-1 with its deadline.
        gate = next(row for row in self.get('/api/gates') if row['workflowId'] == self.workflow_id)
        self.assertEqual((gate['stepId'], gate['question']), ('S-2', 'Release S-2?'))
        self.assertTrue(0 < gate['secondsToDeadline'] <= 3600)
        self.assertGreaterEqual(gate['waitingSeconds'], 0)
        self.assertIn("approve('" + self.workflow_id + "', '" + gate['subjectDigest'] + "'", gate['approvalCommand'])
        self.assertIn("database='durai_test'", gate['approvalCommand'])
        workflow = next(row for row in self.get('/api/workflows') if row['workflowId'] == self.workflow_id)
        self.assertEqual(workflow['state'], 'WAITING_GATE')
        self.assertEqual([(step['stepId'], step['state']) for step in workflow['steps']], [('S-1', 'SUCCEEDED'), ('S-2', 'PENDING')])

    def test_no_secret_in_responses(self):
        # Sample: Read every endpoint. Check no apiKey, password, or protected test line.
        text = ''
        for path in ENDPOINTS:
            text += json.dumps(self.get(path))
        with self.opener.open(BASE + '/') as response:
            text += response.read().decode()
        self.assertIn(self.workflow_id, text)
        lowered = text.lower()
        for word in ('apikey', 'password', 'sk-test-123456789', 'expectedshares', 'open-two-lots'):
            self.assertNotIn(word, lowered)
        for secret in known_secrets():
            self.assertNotIn(secret, text)
        for line in (PROTECTED / 'f101.json').read_text(encoding='utf-8').splitlines():
            if len(line.strip()) > 20:
                self.assertNotIn(line.strip(), text)
        self.assertEqual(scrub({'apiKey': 'sk-test-123456789', 'shares': 200, 'rows': [{'reviewKey': 'x', 'lots': 2}]}),
                         {'shares': 200, 'rows': [{'lots': 2}]})

    def test_decisions_listed(self):
        # Sample: AUTO step S-1 of W-2 decides SUBAGENTS and falls back to SINGLE. Check /api/decisions lists both rows with features and rule digest.
        workflow_id = 'W-' + uid()[:8]
        goal = {'spec': {'writePaths': ['/w/journal.py', '/w/location_search.py']}, 'files': {}}
        spec = {'format': 'durai-workflow/v1', 'id': workflow_id, 'steps': [{'id': 'S-1', 'goal': goal, 'strategy': 'AUTO'}]}
        providers = Providers()
        providers.register(CAPABILITY, 'SINGLE', scripted)
        start_workflow(build_workflow(spec, providers, DATABASE), workflow_id, DATABASE)
        rows = [row for row in self.get('/api/decisions') if row['workflowId'] == workflow_id]
        self.assertEqual(sorted((row['event'], row['choice']) for row in rows), [('DECISION_RECORDED', 'SUBAGENTS'), ('STRATEGY_FALLBACK', 'SINGLE')])
        decision = next(row for row in rows if row['event'] == 'DECISION_RECORDED')
        self.assertIn('moduleShape=LOOSE', decision['features'])
        self.assertEqual((decision['stepId'], decision['rule'], len(decision['ruleDigest'])), ('S-1', 'loose-modules', 64))

    def test_agents_listed(self):
        # Sample: AUTO step S-1 of W-3 runs sub-1 and sub-2 and merges. Check /api/agents lists both rows with state, package digest, budget, merge APPLIED, acceptance PASSED, and no secret text.
        from durai.strategy_subagents import SubagentsExecutor, register_subagents
        from test_strategy_subagents import Acceptance, Fake, goal as subagent_goal
        workflow_id = 'W-' + uid()[:8]
        spec = {'format': 'durai-workflow/v1', 'id': workflow_id, 'steps': [{'id': 'S-1', 'goal': subagent_goal(), 'strategy': 'AUTO'}]}
        providers = Providers()
        providers.register(CAPABILITY, 'SINGLE', scripted)
        register_subagents(providers, SubagentsExecutor(Fake(), acceptance=Acceptance(), max_parallel=2))
        start_workflow(build_workflow(spec, providers, DATABASE), workflow_id, DATABASE)
        rows = sorted((row for row in self.get('/api/agents') if row['workflowId'] == workflow_id), key=lambda row: row['subagentId'])
        self.assertEqual([(row['stepId'], row['subagentId'], row['state'], row['merge'], row['acceptance']) for row in rows],
                         [('S-1', 'sub-1', 'SUCCEEDED', 'APPLIED', 'PASSED'), ('S-1', 'sub-2', 'SUCCEEDED', 'APPLIED', 'PASSED')])
        self.assertEqual([len(row['packageDigest']) for row in rows], [64, 64])
        self.assertEqual(rows[0]['budget'], '{"modelCalls": 20}')
        self.assertEqual(rows[0]['usage'], '{"modelCalls": 3}')

    def test_leases_listed(self):
        # Sample: AUTO step S-1 of W-4 runs a team with implementer, test writer, and reviewer. Outsider X-1 holds journal.py of W-5 and hands it to X-2. Check /api/leases lists the released team leases, the live generation 2 of X-2, and /api/agents lists three team members with merge SHARED and acceptance PASSED, with no secret text.
        from durai.file_leases import FileLeases
        from durai.strategy_team import TeamExecutor, register_team
        from test_strategy_team import Acceptance, Members, goal as team_goal
        workflow_id, other = 'W-' + uid()[:8], 'W-' + uid()[:8]
        leases = FileLeases(DATABASE)
        spec = {'format': 'durai-workflow/v1', 'id': workflow_id, 'steps': [{'id': 'S-1', 'goal': team_goal(), 'strategy': 'AUTO'}]}
        providers = Providers()
        providers.register(CAPABILITY, 'SINGLE', scripted)
        register_team(providers, TeamExecutor(Members(), acceptance=Acceptance(), leases=leases))
        start_workflow(build_workflow(spec, providers, DATABASE), workflow_id, DATABASE)
        first = leases.claim(other + '/S-1', '/w/journal.py', 'X-1', 'implementer', 600, other, 'S-1')
        leases.transfer(first, 'X-2', 'implementer')
        found = self.get('/api/leases')
        team = sorted((row['owner'], row['state'], row['live']) for row in found if row['workflowId'] == workflow_id)
        self.assertEqual(team, [('implementer', 'RELEASED', False), ('test-writer', 'RELEASED', False)])
        handed = sorted((row['owner'], row['generation'], row['state'], row['live']) for row in found if row['workflowId'] == other)
        self.assertEqual(handed, [('X-1', 1, 'RELEASED', False), ('X-2', 2, 'HELD', True)])
        members = sorted((row['subagentId'], row['module'], row['state'], row['merge'], row['acceptance'])
                         for row in self.get('/api/agents') if row['workflowId'] == workflow_id)
        self.assertEqual(members, [('implementer', 'implementer', 'SUCCEEDED', 'SHARED', 'PASSED'),
                                   ('reviewer', 'reviewer', 'SUCCEEDED', 'SHARED', 'PASSED'),
                                   ('test-writer', 'test-writer', 'SUCCEEDED', 'SHARED', 'PASSED')])

    def test_api_requires_session_cookie(self):
        # Sample: Read /api/gates without the session cookie. Check 401.
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(BASE + '/api/gates')
        self.assertEqual(caught.exception.code, 401)

    def test_scorecard_endpoint(self):
        # Sample: Import one synthetic receipt that starts now and runs 120 seconds into durai_test. Check /api/scorecard lists its model profile and /api/runs lists the run.
        token = uid()
        profile_id = 'dash-' + token[:12]
        add_profile(profile_id, 'deepseek-' + token[:8], 'model-' + token[:8], 'direct', database=DATABASE)
        path, receipt = make_receipt(self.folder.name, 'task-tx-funds-v14', 'deepseek-' + token[:8], 'deepseek', 'deepseek-flash', [7, 21])
        started = datetime.now(timezone.utc)
        timings = dict(START, started=started, finished=started + timedelta(seconds=120))
        self.assertEqual(import_receipt(path, profile_id, DATABASE, None, timings=timings)['state'], 'IMPORTED')
        self.assertIn(profile_id, [row['model_profile_id'] for row in self.get('/api/scorecard')])
        run = next(row for row in self.get('/api/runs') if row['run_id'] == receipt['runId'])
        self.assertEqual((run['outcome'], run['pass_attempt'], run['model_calls'], float(run['wall_seconds'])), ('PASSED', 2, 28, 120.0))
        self.assertIsInstance(self.get('/api/experiments'), list)


if __name__ == '__main__':
    unittest.main()
