"""Sample: Declare FEATURE T-1 with a.py writable. Seal it. Check READY."""
import re
from .common import Conflict, digest
from .domain_profiles import PROFILES as DOMAIN_PROFILES

KINDS = ('REPAIR', 'FEATURE', 'REFACTOR')
PROFILES = ('software', 'trading') + DOMAIN_PROFILES
READINESS = {'REPAIR': ('baselineRunnable', 'regressionSuite'),
             'REFACTOR': ('baselineRunnable', 'regressionSuite'),
             'FEATURE': ('shellRunnable', 'failingAcceptanceGoal')}
REQUIRED = ('applicationId', 'taskId', 'kind', 'profile', 'objective', 'sourceRevision', 'binding',
            'readPaths', 'writePaths', 'deniedPaths', 'validationCommands', 'protectedSuite',
            'environment', 'runtime', 'budget', 'deliverables', 'readiness')
VALIDATION_COMMANDS = {
    'python-compile': ('python', '-m', 'compileall', '-q', '/workspace'),
    'python-unittest': ('python', '-m', 'unittest', 'discover', '-s', '/workspace/tests', '-t', '/workspace'),
    'comment-policy': ('durai-internal', 'check_walkthroughs')}
BUDGET_LIMITS = {'modelCalls': 200, 'toolCalls': 400, 'seconds': 3600, 'attempts': 5}
DEFAULT_BUDGET = {'modelCalls': 40, 'toolCalls': 40, 'seconds': 600, 'attempts': 2}
DATABASES = ('none', 'ephemeral-postgres-age')
HEX = re.compile(r'[a-f0-9]{64}')
SEGMENT = re.compile(r'[A-Za-z0-9_.\-]+')


def check_path(path, allow_tree=False):
    # Sample: Accept /workspace/a.py. Refuse /workspace/../secrets and C:\x.py.
    if not isinstance(path, str) or not path.startswith('/workspace/'):
        raise Conflict('Path must be inside /workspace: ' + repr(path))
    parts = path[len('/workspace/'):].split('/')
    if allow_tree and parts[-1] == '**':
        parts = parts[:-1]
        if not parts:
            return path
    if not parts or any(part in ('', '.', '..') or not SEGMENT.fullmatch(part) for part in parts):
        raise Conflict('Path segment is not permitted: ' + path)
    return path


def matches(path, patterns):
    # Sample: Match /workspace/tests/h.py against /workspace/tests/**. Return True.
    return any(path == item or (item.endswith('/**') and path.startswith(item[:-2])) for item in patterns)


def missing_readiness(kind, readiness):
    # Sample: Omit failingAcceptanceGoal from FEATURE. Return that name.
    missing = []
    for name in READINESS[kind]:
        value = readiness.get(name) if isinstance(readiness, dict) else None
        if value is not True and not (isinstance(value, str) and value.strip()):
            missing.append('readiness.' + name)
    return missing


def check_budget(budget):
    # Sample: Accept 40 tool calls and 600 seconds. Refuse 0 attempts.
    if not isinstance(budget, dict) or set(budget) != set(BUDGET_LIMITS):
        raise Conflict('Budget must declare ' + ', '.join(sorted(BUDGET_LIMITS)))
    for name, limit in BUDGET_LIMITS.items():
        value = budget[name]
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= limit:
            raise Conflict('Budget value is outside bounds: ' + name)
    return dict(budget)


def pins(spec):
    # Sample: Read source S-1, contract C-1, suite H-1, image I-1, deepagents 0.7.38. Return five pins.
    return {'sourceRevision': spec['sourceRevision'], 'contractDigest': spec['binding']['contractDigest'],
            'suiteDigest': spec['protectedSuite']['digest'], 'imageId': spec['environment']['imageId'],
            'runtime': spec['runtime']['name'] + '@' + spec['runtime']['version']}


def build_task(spec):
    # Sample: Omit objective and writePaths. Return NEEDS_CLARIFICATION with both names.
    missing = [name for name in REQUIRED if spec.get(name) in (None, '', [], {})]
    if 'deniedPaths' in missing and spec.get('deniedPaths') == []:
        missing.remove('deniedPaths')
    if missing:
        return {'state': 'NEEDS_CLARIFICATION', 'missing': missing}
    if 'contextMemoryQuery' in spec:
        query = spec['contextMemoryQuery']
        plan_id = spec.get('contextPlanId')
        if not isinstance(plan_id, str) or not plan_id.strip() or not isinstance(query, str) or not query.strip() or len(query) > 240:
            raise Conflict('Context memory requires a pinned plan and a short query')
    if spec['kind'] not in KINDS or spec['profile'] not in PROFILES:
        raise Conflict('Task kind or profile is not supported')
    missing = missing_readiness(spec['kind'], spec['readiness'])
    if spec['profile'] != 'software':
        from .engineering import validate_domain
        try:
            domain = dict(spec.get('domain') or {})
            selected = 'trading-position' if spec['profile'] == 'trading' else spec['profile']
            if domain.get('profile', selected) != selected:
                raise Conflict('NEEDS_CLARIFICATION: task and domain profiles differ')
            domain['profile'] = selected
            validate_domain(domain)
        except Conflict as error:
            missing.append(str(error).removeprefix('NEEDS_CLARIFICATION: '))
    if missing:
        return {'state': 'NEEDS_CLARIFICATION', 'missing': missing}
    if not HEX.fullmatch(str(spec['sourceRevision'])):
        raise Conflict('sourceRevision must be a sha256 digest')
    for container, key in (('binding', 'contractDigest'), ('protectedSuite', 'digest')):
        if not isinstance(spec[container], dict) or not HEX.fullmatch(str(spec[container].get(key))):
            raise Conflict(container + '.' + key + ' must be a sha256 digest')
    if not spec['environment'].get('imageId') or not spec['runtime'].get('name') or not spec['runtime'].get('version'):
        raise Conflict('Environment image and runtime version must be pinned')
    if spec['environment'].get('database', 'none') not in DATABASES:
        raise Conflict('Environment database must be one of: ' + ', '.join(DATABASES))
    reads = [check_path(path, True) for path in spec['readPaths']]
    denied = [check_path(path, True) for path in spec['deniedPaths']]
    writes = [check_path(path) for path in spec['writePaths']]
    for path in writes:
        if matches(path, denied):
            raise Conflict('Write path is denied: ' + path)
        if not matches(path, reads):
            raise Conflict('Write path must also be readable: ' + path)
    unknown = sorted(set(spec['validationCommands']) - set(VALIDATION_COMMANDS))
    if unknown:
        raise Conflict('Validation command is not in the trusted catalog: ' + ', '.join(unknown))
    scope = spec.get('editScope')
    if scope is not None and (not isinstance(scope, dict) or not scope or any(path not in writes for path in scope) or any(
            not isinstance(names, list) or not names or not all(isinstance(name, str) and name.isidentifier() for name in names)
            for names in scope.values())):
        raise Conflict('editScope must map declared write paths to symbol names')
    body = dict(spec, readPaths=sorted(set(reads)), writePaths=sorted(set(writes)), deniedPaths=sorted(set(denied)),
                validationCommands=list(spec['validationCommands']), budget=check_budget(spec['budget']),
                format='durai-task/v1', releaseAuthority=False)
    if spec['profile'] != 'software':
        body['domain'] = domain
    body['pins'] = pins(body)
    return {'state': 'READY', 'task': dict(body, digest=digest(body))}


def verify_task(task, current):
    # Sample: Seal T-1 with source S-1. Verify against source S-2. Refuse the stale task.
    body = {key: value for key, value in task.items() if key != 'digest'}
    if digest(body) != task.get('digest'):
        raise Conflict('Task digest differs')
    changed = sorted(name for name, value in task['pins'].items() if current.get(name) != value)
    if changed:
        raise Conflict('Task is stale: ' + ', '.join(changed))
    return task
