"""Sample: Start the dashboard on 54430. Open / to get a session cookie. Read /api/gates. Check pending gate G-1 of W-1 with its approval command."""
import base64
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import parse_qs, urlsplit
from . import bench_store, review_page
from .db import transaction
from .execution_events import SECRET_NAME, known_secrets

SECRET_TEXT = re.compile(r'(?<![A-Za-z0-9])(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9._\-]+)')
PAGE = Path(__file__).resolve().parent / 'dashboard_page.html'
COOKIE = 'durai_dashboard'
RUN_LIMIT = 200
WORKFLOW_LIMIT = 100
STEP_TERMINAL = ('SUCCEEDED', 'FAILED', 'CANCELLED', 'LOST')


def scrub(value, secrets_found=None):
    # Sample: Scrub {"apiKey": "sk-test-123456789", "shares": 200}. Check the result is {"shares": 200}.
    secrets_found = known_secrets() if secrets_found is None else secrets_found
    if isinstance(value, dict):
        return {key: scrub(child, secrets_found) for key, child in value.items() if not SECRET_NAME.search(str(key))}
    if isinstance(value, list):
        return [scrub(child, secrets_found) for child in value]
    if isinstance(value, str):
        for secret in secrets_found:
            value = value.replace(secret, '[REDACTED]')
        return SECRET_TEXT.sub('[REDACTED]', value)
    return value


def read(sql, params, database):
    # Sample: Read the event rows of run W-1 with the runtime login in a READ ONLY transaction. Check no row changes.
    with transaction('durai_runtime', database) as db:
        db.execute('SET TRANSACTION READ ONLY')
        return db.execute(sql, params).fetchall()


def approval_command(workflow_id, subject_digest, step_id, database):
    # Sample: Gate S-2 of W-1 with digest D-1. Return the python -c approve command from docs/workflow.md.
    suffix = '' if database == 'durai_local' else ", database='" + database + "'"
    return ("python -c \"from durai.workflow_gates import approve; approve('" + workflow_id + "', '" + subject_digest
            + "', 'Approved for release of " + step_id + ".'" + suffix + ")\"")


def workflow_rows(database):
    # Sample: Read W-1 with S-1 SUCCEEDED and S-2 waiting at a gate. Return state WAITING_GATE and both step states.
    started = read("SELECT run_id, payload, created_at FROM durai_coordination.event WHERE kind='WORKFLOW_STARTED' "
                   "ORDER BY created_at DESC LIMIT %s", (WORKFLOW_LIMIT,), database)
    ids = [row['run_id'] for row in started]
    events = read("SELECT run_id, sequence, kind, payload, created_at FROM durai_coordination.event WHERE run_id = ANY(%s) "
                  "AND kind IN ('STEP_STARTED','STEP_FINISHED','GATE_OPENED','GATE_APPROVED','GATE_CANCELLED','WORKFLOW_FINISHED') "
                  "ORDER BY run_id, sequence", (ids,), database) if ids else []
    by_run = {}
    for event in events:
        by_run.setdefault(event['run_id'], []).append(event)
    result = []
    for row in started:
        steps = {name: 'PENDING' for name in (row['payload'] or {}).get('steps', [])}
        finished_at, state, open_gate = None, 'RUNNING', False
        for event in by_run.get(row['run_id'], []):
            payload = event['payload'] or {}
            if event['kind'] == 'STEP_STARTED':
                steps[payload.get('stepId')] = 'RUNNING'
            elif event['kind'] == 'STEP_FINISHED':
                steps[payload.get('stepId')] = payload.get('state')
            elif event['kind'] == 'GATE_OPENED':
                open_gate = True
            elif event['kind'] in ('GATE_APPROVED', 'GATE_CANCELLED'):
                open_gate = False
            elif event['kind'] == 'WORKFLOW_FINISHED':
                state, finished_at, open_gate = payload.get('state'), event['created_at'], False
        if state == 'RUNNING' and open_gate:
            state = 'WAITING_GATE'
        result.append({'workflowId': row['run_id'], 'state': state, 'steps': [{'stepId': key, 'state': value} for key, value in steps.items()],
                       'startedAt': row['created_at'], 'finishedAt': finished_at})
    return result


def gate_rows(database):
    # Sample: Open gate S-2 for W-1 with deadline T+3600. Return one row with the subject digest, question, deadline, and waiting seconds.
    rows = read("SELECT DISTINCT ON (run_id) run_id, kind, payload, created_at, extract(epoch FROM clock_timestamp() - created_at) AS waiting "
                "FROM durai_coordination.event WHERE kind IN ('GATE_OPENED','GATE_APPROVED','GATE_CANCELLED','WORKFLOW_FINISHED') "
                "AND run_id IN (SELECT run_id FROM durai_coordination.event WHERE kind='GATE_OPENED') "
                "ORDER BY run_id, sequence DESC", (), database)
    result = []
    for row in rows:
        payload = row['payload'] or {}
        if row['kind'] != 'GATE_OPENED':
            continue
        result.append({'workflowId': row['run_id'], 'stepId': payload.get('stepId'), 'when': payload.get('when'),
                       'subjectDigest': payload.get('subjectDigest'), 'question': payload.get('question'),
                       'deadline': payload.get('deadline'), 'openedAt': row['created_at'],
                       'waitingSeconds': round(float(row['waiting']), 1),
                       'secondsToDeadline': round(float(payload.get('deadline', 0)) - time.time(), 1),
                       'approvalCommand': approval_command(row['run_id'], payload.get('subjectDigest', ''), payload.get('stepId', ''), database)})
    return sorted(result, key=lambda item: item['openedAt'])


def run_rows(database):
    # Sample: Import one run of profile P-1 with outcome PASSED on attempt 2. Return it first with its calls and wall seconds.
    return read("SELECT r.run_id, r.label, t.case_name, t.variant, r.model_profile_id, r.outcome, r.pass_attempt, r.model_calls, "
                "r.wall_seconds, r.started_at, r.finished_at FROM durai_bench.run r JOIN durai_bench.task_case t ON t.id = r.task_case_id "
                "ORDER BY coalesce(r.started_at, r.imported_at) DESC, r.run_id LIMIT %s", (RUN_LIMIT,), database)


def experiment_rows(database):
    # Sample: Read experiment pack-order on 22 pairs. Return its four counts without the pair rows.
    ids = [row['id'] for row in read('SELECT id FROM durai_bench.experiment ORDER BY id', (), database)]
    found = []
    for experiment_id in ids:
        counts = {key: value for key, value in bench_store.compare(experiment_id, database).items() if key != 'rows'}
        found.append(counts)
    return found


def decision_rows(database):
    # Sample: Record decision SUBAGENTS for S-2 of W-1 then fallback to SINGLE. Return both rows newest first with features and rule digest.
    rows = read("SELECT run_id, kind, payload, created_at FROM durai_coordination.event WHERE "
                "(kind='DECISION_RECORDED' AND payload->>'point'='decision.execution-strategy') OR kind='STRATEGY_FALLBACK' "
                "ORDER BY created_at DESC, sequence DESC LIMIT %s", (RUN_LIMIT,), database)
    result = []
    for row in rows:
        payload = row['payload'] or {}
        features = payload.get('features') or {}
        fallback = row['kind'] == 'STRATEGY_FALLBACK'
        result.append({'workflowId': row['run_id'], 'stepId': payload.get('stepId'), 'event': row['kind'],
                       'choice': payload.get('used') if fallback else payload.get('choice'),
                       'wanted': payload.get('wanted'), 'rule': ', '.join(payload.get('reasons') or []),
                       'ruleDigest': payload.get('ruleDigest'),
                       'features': ', '.join(str(key) + '=' + str(features[key]) for key in sorted(features)),
                       'createdAt': row['created_at']})
    return result


def agent_rows(database):
    # Sample: Record SUBAGENT_STARTED and SUBAGENT_FINISHED for sub-1 and sub-2 of S-1. Return two rows with state, package digest, budget and merge state.
    rows = read("SELECT run_id, sequence, kind, payload, created_at FROM durai_coordination.event WHERE kind IN "
                "('SUBAGENT_STARTED','SUBAGENT_FINISHED','TEAM_MEMBER_STARTED','TEAM_MEMBER_FINISHED','MERGE_APPLIED','MERGE_CONFLICT','ACCEPTANCE_FINISHED') "
                "ORDER BY created_at DESC, sequence DESC LIMIT %s", (RUN_LIMIT * 5,), database)
    found, steps = {}, {}
    for row in reversed(rows):
        payload = row['payload'] or {}
        key = (row['run_id'], payload.get('stepId'))
        step = steps.setdefault(key, {'merge': 'PENDING', 'acceptance': None})
        if row['kind'] == 'MERGE_CONFLICT':
            step['merge'] = 'MERGE_CONFLICT'
        elif row['kind'] == 'MERGE_APPLIED' and step['merge'] == 'PENDING':
            step['merge'] = 'APPLIED'
        elif row['kind'] == 'ACCEPTANCE_FINISHED':
            step['acceptance'] = 'PASSED' if payload.get('passed') else 'FAILED'
        elif row['kind'].startswith('SUBAGENT_') or row['kind'].startswith('TEAM_MEMBER_'):
            team = row['kind'].startswith('TEAM_')
            name = payload.get('memberId') if team else payload.get('subagentId')
            item = found.setdefault(key + (name,), {
                'workflowId': row['run_id'], 'stepId': payload.get('stepId'), 'subagentId': name,
                'state': 'RUNNING', 'module': payload.get('role') if team else None, 'packageDigest': None, 'budget': None, 'usage': None,
                'startedAt': row['created_at']})
            if team:
                step['merge'] = 'SHARED'
            if row['kind'] == 'SUBAGENT_STARTED':
                item['module'] = payload.get('module')
            else:
                item.update(state=payload.get('state'), packageDigest=payload.get('packageDigest'),
                            budget=payload.get('budget'), usage=payload.get('usage'))
    result = []
    for key, item in found.items():
        step = steps[key[:2]]
        item['merge'], item['acceptance'] = step['merge'], step['acceptance']
        for name in ('budget', 'usage'):
            item[name] = json.dumps(item[name], sort_keys=True) if item[name] is not None else None
        result.append(item)
    return sorted(result, key=lambda item: (item['startedAt'], item['subagentId']), reverse=True)


def lease_rows(database):
    # Sample: Claim journal.py for M-1 then transfer it to M-2. Return both rows newest first with owner, role, generation, state, live flag, and expiry.
    return read("SELECT workflow_id AS \"workflowId\", step_id AS \"stepId\", worktree, path, owner, role, generation, state, "
                "(state = 'HELD' AND expires_at > clock_timestamp()) AS live, expires_at AS \"expiresAt\", acquired_at AS \"acquiredAt\" "
                "FROM durai_coordination.file_lease ORDER BY acquired_at DESC, generation DESC LIMIT %s", (RUN_LIMIT,), database)


def serve(port=54430, *, ready=None, database='durai_local'):
    origin = 'http://127.0.0.1:' + str(port)
    sessions = set()
    session_lock = threading.Lock()
    page = PAGE.read_bytes()
    text = page.decode()
    hashes = []
    for tag in ('script', 'style'):
        inner = text.split('<' + tag + '>', 1)[1].split('</' + tag + '>', 1)[0]
        hashes.append("'sha256-" + base64.b64encode(hashlib.sha256(inner.encode()).digest()).decode() + "'")
    policy = ("default-src 'none'; script-src " + hashes[0] + "; style-src " + hashes[1] +
              "; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
    endpoints = {'/api/workflows': workflow_rows, '/api/gates': gate_rows, '/api/runs': run_rows,
                 '/api/scorecard': lambda name: bench_store.scorecard(name), '/api/experiments': experiment_rows,
                 '/api/decisions': decision_rows, '/api/agents': agent_rows, '/api/leases': lease_rows}

    class Handler(BaseHTTPRequestHandler):
        server_version = 'DuraiDashboard/1'

        def log_message(self, format, *args):
            pass

        def send(self, code, value, content_type='application/json', cookie=None, extra=None, csp=None):
            body = value if isinstance(value, bytes) else json.dumps(scrub(value), default=str).encode()
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', csp or policy)
            for key, item in (extra or {}).items():
                self.send_header(key, item)
            if cookie:
                self.send_header('Set-Cookie', cookie)
            self.end_headers()
            self.wfile.write(body)

        def permitted(self):
            return self.headers.get('Host') == '127.0.0.1:' + str(port) and self.headers.get('Origin', origin) == origin

        def refuse_method(self):
            # Sample: Send POST to /api/workflows. Check 405 and no state change.
            self.send(405, {'error': 'Dashboard is read-only; use GET'}, extra={'Allow': 'GET'})

        do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = refuse_method

        def session_token(self):
            cookie = SimpleCookie(self.headers.get('Cookie', '')).get(COOKIE)
            with session_lock:
                return cookie.value if cookie is not None and cookie.value in sessions else None

        def show_review(self, code=200, error=None):
            # Sample: Open /review. Return the review page with a session cookie. Database down. Return 503 text.
            token = self.session_token()
            cookie = None
            if token is None:
                token = secrets.token_urlsafe(32)
                with session_lock:
                    if len(sessions) >= 64:
                        sessions.clear()
                    sessions.add(token)
                cookie = COOKIE + '=' + token + '; HttpOnly; SameSite=Strict; Path=/; Max-Age=3600'
            try:
                body = review_page.render(database, token, scrub, error)
            except Exception:
                return self.send(503, {'error': 'Review data is unavailable'})
            return self.send(code, body, 'text/html; charset=utf-8', cookie=cookie, csp=review_page.POLICY)

        def post_review(self):
            # Sample: Post FUNCTION APPROVED for trade-booking with the page token. Insert one row and answer 303 to /review. Post without the token. Answer 403 and no row.
            if self.headers.get('Origin') != origin:
                return self.send(403, {'error': 'Origin is not allowed'})
            token = self.session_token()
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                length = -1
            if not 0 < length <= review_page.MAX_BODY:
                return self.send(400, {'error': 'Body size is not accepted'})
            body = self.rfile.read(length)
            posted = parse_qs(body.decode('utf-8', 'replace')).get('csrf', [''])[0]
            if token is None or not secrets.compare_digest(posted, token):
                return self.send(403, {'error': 'Open the review page first'})
            try:
                fields = review_page.parse_form(body)
            except ValueError as error:
                return self.show_review(400, str(error))
            try:
                review_page.insert_review(fields, database)
            except Exception:
                return self.send(503, {'error': 'Review was not stored'})
            return self.send(303, b'', 'text/plain', extra={'Location': '/review'})

        def do_POST(self):
            if not self.permitted():
                return self.send(403, {'error': 'Origin or host is not allowed'})
            if urlsplit(self.path).path == '/review':
                return self.post_review()
            return self.refuse_method()

        def do_GET(self):
            if not self.permitted():
                return self.send(403, {'error': 'Origin or host is not allowed'})
            path = urlsplit(self.path).path
            if path == '/':
                token = secrets.token_urlsafe(32)
                with session_lock:
                    if len(sessions) >= 64:
                        sessions.clear()
                    sessions.add(token)
                return self.send(200, page, 'text/html; charset=utf-8',
                                 cookie=COOKIE + '=' + token + '; HttpOnly; SameSite=Strict; Path=/; Max-Age=3600')
            if path == '/review':
                return self.show_review()
            if path not in endpoints:
                return self.send(404, {'error': 'Not found'})
            cookie = SimpleCookie(self.headers.get('Cookie', '')).get(COOKIE)
            with session_lock:
                known = cookie is not None and cookie.value in sessions
            if not known:
                return self.send(401, {'error': 'Open the dashboard page first'})
            try:
                return self.send(200, {'rows': endpoints[path](database)})
            except Exception:
                return self.send(503, {'error': 'Data is unavailable for this tab'})

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    if ready:
        ready(server)
    print('Durai dashboard: ' + origin, flush=True)
    server.serve_forever()
