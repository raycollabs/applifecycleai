"""Sample: Run Task B through the loop. Fail attempt 1. Pass attempt 2. Restore its package. Stop at WAITING_FOR_REVIEW."""
import json
from pathlib import Path
from .candidate_store import CandidateStore
from .common import Conflict, uid
from .development_loop import CAPABILITY, build_loop, development_graph
from .execution_events import replay
from .providers import Providers
from .runtime_deepagents import DeepAgentsWorker
from .task_evaluator import SandboxEvaluator
from .task_ledger import TaskLedger
from .workspace_manifest import manifest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / 'templates'
FIXTURE = TEMPLATES / 'task-b-change-manifest'
BINDING = 'foundation-generic-v7'
MODULE = '/workspace/change_manifest.py'
BASELINE_BODY = '    return True\n'
CORRECT_BODY = ("    if not isinstance(path, str) or not path.startswith('/workspace/') or '..' in path.split('/') or '\\\\' in path:\n"
                "        raise ValueError('Path escapes the workspace: ' + str(path))\n"
                "    if path not in allowed:\n"
                "        raise ValueError('Path is not declared: ' + path)\n"
                "    if file_digest(content) != expected_digest:\n"
                "        raise ValueError('Content digest differs: ' + path)\n"
                "    return True\n")
PARTIAL_BODY = ("    if file_digest(content) != expected_digest:\n"
                "        raise ValueError('Content digest differs: ' + path)\n"
                "    return True\n")


def load(folder):
    # Sample: Read the Task B fixture folder. Return /workspace/change_manifest.py with its exact bytes.
    folder = Path(folder)
    return {'/workspace/' + item.relative_to(folder).as_posix(): item.read_bytes()
            for item in sorted(folder.rglob('*')) if item.is_file() and '__pycache__' not in item.parts}


def contract_digest(database, binding=None, version=None):
    # Sample: Resolve binding foundation-generic-v7 in durai_test. Install and bind G-7 when absent. Record its 21 policies. Return the digest. Use foundation-generic-v10 with version 10. Bind pack v10.
    from .packs import PackStore, generic_pack
    from .policy_store import record_policies
    from .worker_contract import resolve_contract
    binding = binding or BINDING
    store = PackStore(database)
    try:
        resolve_contract(binding, database=database)
    except KeyError:
        store.bind(binding, [store.install(generic_pack(version) if version else generic_pack())], 'SYNTHETIC_LOCAL')
    for reference in store.resolve(binding)['document']['packs']:
        record_policies(reference, database)
    return resolve_contract(binding, database=database)['digest']


def task_b_spec(database, image):
    # Sample: Declare FEATURE Task B. Permit change_manifest.py. Deny /workspace/tests/**. Pin suite H-B and deepagents.
    files, suite = load(FIXTURE / 'workspace'), load(FIXTURE / 'protected')
    return {'applicationId': 'trading', 'taskId': 'TASK-B-' + uid()[:8], 'kind': 'FEATURE', 'profile': 'software',
            'objective': 'In /workspace/change_manifest.py, change only the function validate_change. Raise ValueError for: '
                         'a path that does not start with /workspace/, a path that has a .. path segment, a path that has a backslash, '
                         'a path that is not in allowed, and content whose sha256 hex digest differs from expected_digest. '
                         'Return True for a valid change. Accept a valid name that only contains two dots, such as '
                         '/workspace/a..b.py, when it is in allowed.',
            'editScope': {MODULE: ['validate_change']},
            'sourceRevision': manifest(files)['digest'],
            'binding': {'bindingId': BINDING, 'contractDigest': contract_digest(database)},
            'readPaths': ['/workspace/**'], 'writePaths': [MODULE], 'deniedPaths': ['/workspace/tests/**'],
            'validationCommands': ['python-compile', 'python-unittest', 'comment-policy'],
            'protectedSuite': {'id': 'H-B', 'digest': manifest(suite)['digest']}, 'environment': {'imageId': image},
            'runtime': {'name': 'deepagents', 'version': DeepAgentsWorker.version},
            'budget': {'modelCalls': 40, 'toolCalls': 40, 'seconds': 600, 'attempts': 2},
            'deliverables': [MODULE], 'readiness': {'shellRunnable': True, 'failingAcceptanceGoal': 'test_escaping_path_is_rejected'}}


def fixture_config(folder):
    # Sample: Read task.json in fixture task-7-1b-policy-store. Return its objective, paths, includes, and database option.
    path = Path(folder) / 'task.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def fixture_files(folder):
    # Sample: Load the 7.1b shell and include durai/db.py and nine migrations from the repository. Return one file map.
    files = {}
    for pattern in fixture_config(folder).get('include', []):
        for item in sorted(ROOT.glob(pattern)):
            if item.is_file():
                files['/workspace/' + item.relative_to(ROOT).as_posix()] = item.read_bytes()
    files.update(load(Path(folder) / 'workspace'))
    return files


def fixture_suite(folder):
    # Sample: Read task-tx-journal-v14 with protectedInclude tests/seed/99.sql from qualification/phase4/protected/seed.sql. Return the protected folder files and that seed.
    suite = load(Path(folder) / 'protected')
    for target, source in fixture_config(folder).get('protectedInclude', {}).items():
        suite['/workspace/' + target] = (ROOT / source).read_bytes()
    return suite


def fixture_spec(name, database, image, database_image=None):
    # Sample: Read task.json of fixture task-7-1a-policy-catalog. Return a FEATURE spec for policy_catalog.py with its editScope.
    folder = TEMPLATES / name
    config = fixture_config(folder)
    files, suite = fixture_files(folder), fixture_suite(folder)
    module = config['module']
    scope = config['editScope']
    environment = {'imageId': database_image if config.get('database') else image}
    if config.get('database'):
        environment['database'] = 'ephemeral-postgres-age'
    chosen = config.get('binding') or {'id': BINDING}
    quality = {}
    if name.startswith('task-tx-') and config.get('case'):
        from .prompt_quality import fixture_requirements
        quality['promptQuality'] = fixture_requirements(config)
    edit_scope = ({'editScope': {module: list(scope)} if isinstance(scope, list) else
                   {path: list(names) for path, names in scope.items()}} if scope else {})
    return {'applicationId': 'trading', 'taskId': config['taskPrefix'] + '-' + uid()[:8], 'kind': config['kind'],
            'profile': 'software', 'objective': config['objective'], **edit_scope, **quality,
            'sourceRevision': manifest(files)['digest'],
            'binding': {'bindingId': chosen['id'], 'contractDigest': contract_digest(database, chosen['id'], chosen.get('version'))},
            'readPaths': ['/workspace/**'], 'writePaths': list(config.get('writePaths', [module])),
            'deniedPaths': ['/workspace/tests/**'],
            'validationCommands': ['python-compile', 'python-unittest', 'comment-policy'],
            'protectedSuite': {'id': 'H-' + config['taskPrefix'], 'digest': manifest(suite)['digest']},
            'environment': environment, 'runtime': {'name': 'deepagents', 'version': DeepAgentsWorker.version},
            'budget': dict(config['budget']), 'deliverables': [module],
            'readiness': {'shellRunnable': True, 'failingAcceptanceGoal': config['failingAcceptanceGoal']}}


def pins(spec):
    # Sample: Read Task B pins. Return source, contract, suite, image, and runtime.
    return {'sourceRevision': spec['sourceRevision'], 'contractDigest': spec['binding']['contractDigest'],
            'suiteDigest': spec['protectedSuite']['digest'], 'imageId': spec['environment']['imageId'],
            'runtime': spec['runtime']['name'] + '@' + spec['runtime']['version']}


class FixtureSources:
    def __init__(self, database='durai_test', folder=FIXTURE):
        # Sample: Open Task B sources for durai_test.
        self.database, self.folder = database, Path(folder)

    def policies(self, task):
        # Sample: Resolve binding foundation-generic-v3. Check its digest against the T-B pin. Return its policies.
        from . import ontology_delivery
        from .worker_contract import resolve_contract
        binding_id = task['binding']['bindingId']
        specification = task.get('domain')
        if specification is not None and task['profile'] not in ('software', 'trading'):
            specification = dict(specification, profile=task['profile'])
        if resolve_contract(binding_id, specification, database=self.database)['digest'] != task['pins']['contractDigest']:
            raise Conflict('Ontology contract differs from the task pin')
        return ontology_delivery.worker_view(binding_id, 'coding-worker', self.database, specification=specification)

    def files(self, task):
        # Sample: Return the Task B baseline that accepts every change.
        return fixture_files(self.folder)

    def suite(self, task):
        # Sample: Return protected suite H-B with five goals.
        return fixture_suite(self.folder)


def scripted_steps(attempt):
    # Sample: Return the digest-only edit for attempt 1. Return the complete validation edit for attempt 2.
    body = PARTIAL_BODY if attempt == 1 else CORRECT_BODY
    return [('read_file', {'file_path': MODULE}),
            ('edit_file', {'file_path': MODULE, 'old_string': BASELINE_BODY, 'new_string': body})]


def run_task_b(model_factory, scratch, database='durai_test', decision=None, budget=None):
    # Sample: Run Task B with scripted models and 40 model calls. Restore the passing package. Return WAITING_FOR_REVIEW evidence.
    return run_fixture('task-b-change-manifest', model_factory, scratch, database, decision, budget)


def run_fixture(name, model_factory, scratch, database='durai_test', decision=None, budget=None, candidate_root=None, advisor=None):
    # Sample: Run fixture task-7-1a-policy-catalog with a scripted model. Return the outcome, attempts, and module text.
    scratch = Path(scratch)
    ledger, store, evaluator = TaskLedger(database), CandidateStore(candidate_root or ROOT / 'private/candidates'), SandboxEvaluator(scratch)
    providers = Providers()
    providers.register(CAPABILITY, 'deepagents@' + DeepAgentsWorker.version,
                       lambda model, folder, attempt_id: DeepAgentsWorker(model, folder, attempt_id, database=database))
    folder = TEMPLATES / name
    spec = task_b_spec(database, evaluator.image) if name == 'task-b-change-manifest' else \
        fixture_spec(name, database, evaluator.image, evaluator.database_image)
    spec['budget'].update(budget or {})
    module = spec['deliverables'][0]
    builder = build_loop(ledger, providers, evaluator, store, FixtureSources(database, folder), model_factory, scratch / 'work',
                         advisor)
    run_id = name + '-' + uid()
    config = {'configurable': {'thread_id': run_id}}
    with development_graph(builder, database) as graph:
        state = graph.invoke({'run_id': run_id, 'spec': spec, 'current': pins(spec)}, config)
        if decision and '__interrupt__' in state:
            state = graph.invoke(__import__('langgraph.types', fromlist=['Command']).Command(resume=decision), config)
    attempts = []
    efficiency = {row['payload']['attemptId']: row['payload'] for row in replay(run_id, database=database)
                  if row['kind'] == 'WORKER_EFFICIENCY'}
    for entry in state.get('history', []):
        row = ledger.get(entry['attemptId'])
        restored = store.restore(**entry['package']) if entry.get('package') else None
        attempts.append({'attemptId': entry['attemptId'], 'verdict': entry['verdict'], 'state': row['state'],
                         'verdictDigest': entry.get('verdictDigest'), 'package': entry.get('package'),
                         'restoredManifest': manifest(restored)['digest'] if restored else None,
                         'usage': ledger.usage(row['budget_id']), 'efficiency': efficiency.get(entry['attemptId'])})
    passing = state.get('package')
    restored = store.restore(**passing) if passing else None
    return {'runId': run_id, 'outcome': state['outcome'], 'taskDigest': state['task']['digest'],
            'attempts': attempts, 'verdict': state.get('verdict'), 'package': passing,
            'fixture': name, 'module': restored[module].decode() if restored else None, 'stop': state.get('stop'),
            'events': [row['kind'] for row in replay(run_id, database=database)]}
