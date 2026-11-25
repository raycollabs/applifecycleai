"""Sample: Run suite H-1 on a candidate that returns 200 shares. Return PASS. Run it on 199. Return FAIL."""
from contextlib import ExitStack
import json
from pathlib import Path
import re
import shutil
import subprocess
from .common import Conflict, digest, uid
from .engineering import check_walkthroughs
from .execution_events import redact
from .failure_triage import fault
from .process_owner import OwnedProcess
from .task_contract import VALIDATION_COMMANDS
from .workspace_manifest import manifest, materialize

ROOT = Path(__file__).resolve().parents[1]
SETUP_FAULTS = {3: ('fixture', 'FIXTURE_SETUP'), 4: ('candidate', 'CANDIDATE_SETUP_SQL'), 5: ('fixture', 'FIXTURE_SEED'),
                6: ('evaluator', 'EVALUATOR_DATABASE'), 125: ('evaluator', 'EVALUATOR_START'),
                126: ('evaluator', 'EVALUATOR_START'), 127: ('evaluator', 'EVALUATOR_START')}
COMMAND_FAULTS = {'python-compile': 'CANDIDATE_COMPILE', 'python-unittest': 'CANDIDATE_ASSERTION'}


class StalePin(Conflict):
    state = 'STALE'


def error_lines(text):
    # Sample: Read 6000 characters after one UndefinedObject line. Return that line, cut to 300 characters.
    return [redact(line[:300]) for line in text.splitlines() if line.startswith('psycopg.errors.')][:5]


def failed_goals(text):
    # Sample: Read "FAIL: test_two_lots (tests.T.test_two_lots)" before 6000 characters. Return ['test_two_lots'].
    return sorted(set(re.findall(r'^(?:FAIL|ERROR): (test_[A-Za-z0-9_]+) \(', text, re.M)))


def setup_fault(outcome):
    # Sample: Read runner exit code 4. Return candidate CANDIDATE_SETUP_SQL. Read exit code 1. Return no fault.
    if outcome.get('exitCode') == 0:
        return None
    if outcome.get('exitCode') in SETUP_FAULTS:
        return fault(*SETUP_FAULTS[outcome['exitCode']])
    return None


def run_fault(command, outcome):
    # Sample: Read a started unittest container with exit code 125 and text "fixture fault". Return candidate CANDIDATE_ASSERTION.
    if outcome.get('exitCode') == 0 and not outcome.get('timedOut'):
        return None
    if not outcome.get('started'):
        return fault('evaluator', 'EVALUATOR_START')
    if outcome.get('timedOut'):
        return fault('candidate', 'CANDIDATE_TIMEOUT')
    if outcome.get('oomKilled'):
        return fault('candidate', 'CANDIDATE_RESOURCE_LIMIT')
    return fault('candidate', COMMAND_FAULTS.get(command, 'CANDIDATE_ASSERTION'))


def deferred_migrations(task,files,baseline=None):
    # Sample: Task T-1 declares migrations 1 to 11. Exclude later migration 12 and defer its changed task copy.
    prefix = '/workspace/migrations/core/'
    declared = {path.rsplit('/',1)[1]:content for path,content in (files if baseline is None else baseline).items()
                if path.startswith(prefix) and path.endswith('.sql') and path.count('/') == 4}
    deferred = {path.rsplit('/',1)[1] for path in task['writePaths']
                if path.startswith(prefix) and path.endswith('.sql') and path.count('/') == 4}
    if not {'core_0001.sql','core_0009.sql'} <= declared.keys():
        return sorted(deferred)
    for path in (ROOT/'migrations/core').glob('*.sql'):
        if path.name not in declared or (baseline is not None and path.read_bytes() != declared[path.name]):
            deferred.add(path.name)
    return sorted(deferred)


class SandboxEvaluator:
    def __init__(self, scratch, image=None, seconds=120):
        # Sample: Open the evaluator with the locked python 3.13.7 image and a 120-second limit.
        self.scratch, self.seconds = Path(scratch), seconds
        self.image = image or json.loads((ROOT / 'sandbox-image.lock.json').read_text(encoding='utf-8'))['imageId']
        lock = ROOT / 'eval-image.lock.json'
        self.database_image = json.loads(lock.read_text(encoding='utf-8'))['imageId'] if lock.exists() else None

    def image_for(self, task):
        # Sample: Read a task with database ephemeral-postgres-age. Return the locked durai-eval-python image ID.
        if task['environment'].get('database') == 'ephemeral-postgres-age':
            if not self.database_image:
                raise Conflict('Build the evaluation image with tools/build_eval_image.py')
            return self.database_image
        return self.image

    def command(self, name, folder, argv, image, network=None):
        # Sample: Build the docker command for candidate C-1 with no network. Return the argument list.
        return ['docker', 'run', '--rm', '--name', name, '--label', 'com.durai.owner=task-evaluator',
                *(network or ['--network', 'none']), '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                '--pids-limit', '64', '--memory', '256m', '--cpus', '1', '--user', '65534:65534',
                '--tmpfs', '/tmp:rw,nosuid,size=32m', '-e', 'PYTHONPYCACHEPREFIX=/tmp/pyc', '-e', 'HOME=/tmp',
                '--mount', 'type=bind,source=' + str(folder) + ',target=/workspace,readonly',
                '-w', '/workspace', image, *argv]

    def run(self, folder, argv, image=None, network=None):
        # Sample: Create, start, and inspect a unittest container with no network. Return exit code 0 and started True.
        name = 'durai-eval-' + uid()
        command = self.command(name, folder, argv, image or self.image, network)
        try:
            try:
                created = subprocess.run(['docker', 'create', *command[3:]], capture_output=True, text=True, timeout=120)
            except subprocess.TimeoutExpired:
                return {'exitCode': None, 'output': 'Container create timed out', 'started': False}
            if created.returncode != 0:
                return {'exitCode': created.returncode, 'output': redact(created.stderr[-4000:]), 'started': False}
            process = OwnedProcess(['docker', 'start', '-a', name])
            try:
                code = process.wait(self.seconds)
            except Conflict:
                return {'exitCode': None, 'output': redact(process.text()[-4000:]), 'started': True, 'timedOut': True,
                        'databaseErrorLines': error_lines(process.text()), 'failedGoals': failed_goals(process.text())}
            inspected = subprocess.run(['docker', 'inspect', '--format', '{{json .State}}', name],
                                       capture_output=True, text=True, timeout=30)
            state = json.loads(inspected.stdout) if inspected.returncode == 0 and inspected.stdout.strip() else {}
            started = bool(state) and not state.get('Error') and not str(state.get('StartedAt', '')).startswith('0001-')
            return {'exitCode': code, 'output': redact(process.text()[-4000:]), 'started': started,
                    'oomKilled': bool(state.get('OOMKilled')), 'databaseErrorLines': error_lines(process.text()),
                    'failedGoals': failed_goals(process.text())}
        finally:
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=30)

    def evaluate(self, task, files, suite, baseline=None):
        # Sample: Check suite digest H-1. Run compile, unittest, and comment checks against baseline B-1. Return one verdict digest.
        from .comment_delta import comment_findings, sql_comment_findings
        if manifest(suite)['digest'] != task['protectedSuite']['digest']:
            raise StalePin('Protected suite digest differs')
        image = self.image_for(task)
        if task['environment']['imageId'] != image:
            raise StalePin('Task image pin differs from the evaluator image')
        workspace = dict(files)
        workspace.update(suite)
        folder = self.scratch / ('eval-' + uid())
        materialize(folder, workspace)
        results = []
        with ExitStack() as stack:
            stack.callback(shutil.rmtree, folder, True)
            network = None
            if task['environment'].get('database') == 'ephemeral-postgres-age':
                from .db_sandbox import EphemeralDatabase
                owned = deferred_migrations(task,files,baseline)
                try:
                    database = stack.enter_context(EphemeralDatabase(self.scratch, owned))
                except (subprocess.SubprocessError, OSError, TimeoutError) as error:
                    setup = {'exitCode': None, 'output': type(error).__name__, 'fault': fault('evaluator', 'EVALUATOR_DATABASE')}
                except Exception as error:
                    trusted = isinstance(error, ValueError) or getattr(error, 'sqlstate', None)
                    setup = {'exitCode': None, 'output': type(error).__name__,
                             'fault': fault('fixture', 'FIXTURE_SETUP') if trusted else fault('evaluator', 'EVALUATOR_DATABASE')}
                else:
                    setup = database.apply(files, suite, image, candidate=task['writePaths'])
                    database.seal()
                    setup = dict(setup, fault=setup_fault(setup))
                    network = database.candidate_args()
                results.append(dict(setup, command='database-setup', state='PASS' if setup['exitCode'] == 0 else 'FAIL'))
            for command in task['validationCommands']:
                if results and results[0]['command'] == 'database-setup' and results[0]['state'] == 'FAIL' and command != 'comment-policy':
                    results.append({'command': command, 'state': 'FAIL', 'output': 'Database setup failed',
                                    'fault': results[0].get('fault')})
                    continue
                if command == 'comment-policy':
                    findings = []
                    for path in task['writePaths']:
                        if path not in files or not path.endswith(('.py', '.sql')):
                            continue
                        if path.endswith('.sql'):
                            if baseline is None or baseline.get(path) != files[path]:
                                findings.extend(path + ': ' + item for item in
                                                sql_comment_findings((baseline or {}).get(path), files[path]))
                        elif baseline is None:
                            try:
                                check_walkthroughs(files[path])
                            except Conflict as error:
                                findings.append(path + ': ' + str(error))
                        elif baseline.get(path) != files[path]:
                            findings.extend(path + ': ' + item for item in comment_findings(baseline.get(path), files[path]))
                    results.append({'command': command, 'state': 'FAIL' if findings else 'PASS', 'output': '; '.join(findings)})
                    continue
                outcome = self.run(folder / 'workspace', VALIDATION_COMMANDS[command], image, network)
                results.append(dict(outcome, command=command, state='PASS' if outcome['exitCode'] == 0 else 'FAIL',
                                    fault=run_fault(command, outcome)))
        body = {'verdict': 'PASS' if all(item['state'] == 'PASS' for item in results) else 'FAIL',
                'results': results, 'taskDigest': task['digest'], 'candidateManifest': manifest(files)['digest'],
                'suiteDigest': task['protectedSuite']['digest'], 'imageId': image,
                'database': task['environment'].get('database', 'none')}
        return dict(body, digest=digest(body))
