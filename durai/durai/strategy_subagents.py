"""Sample: Split step S-1 into journal.py and location_search.py. Run two subagents. Merge in order. Run acceptance once. Return SUCCEEDED."""
import hashlib
import re
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as WaitTimeout
from pathlib import Path
from .common import Conflict

CAPABILITY = 'workflow-step'
STRATEGY = 'SUBAGENTS'
TERMINAL = ('SUCCEEDED', 'FAILED', 'CANCELLED', 'LOST')
TEST_NAME = re.compile(r'^(test_)?(.*?)(_test)?$')


class SplitRefused(Conflict):
    pass


def as_bytes(value):
    # Sample: Read "x = 1" and b"x = 1". Return b"x = 1" for both.
    return value if isinstance(value, bytes) else str(value).encode()


def sha(value):
    # Sample: Read b"x = 1". Return its 64 character SHA-256 hex digest.
    return hashlib.sha256(as_bytes(value)).hexdigest()


def package_digest(files):
    # Sample: Read files journal.py and outbox.py. Return one digest that does not depend on the key order.
    lines = ''.join(path + ':' + sha(files[path]) + '\n' for path in sorted(files))
    return hashlib.sha256(lines.encode()).hexdigest()


def module_of(path):
    # Sample: Read /w/journal.py and /w/test_journal.py. Return /w/journal for both.
    folder, _, name = path.rpartition('/')
    stem = name.rsplit('.', 1)[0]
    return folder + '/' + TEST_NAME.match(stem).group(2)


def covers(scope, path):
    # Sample: Read scope /w/ and path /w/journal.py. Return True. Read scope /w/journal.py and /w/outbox.py. Return False.
    return scope == path or (scope.endswith('/') and path.startswith(scope)) or path.startswith(scope.rstrip('/') + '/')


def split(task, base=None):
    # Sample: Read writePaths journal.py, test_journal.py, location_search.py. Return 2 subtasks ordered by first write path.
    paths = list(task.get('writePaths') or [])
    groups = {}
    for path in paths:
        groups.setdefault(module_of(path), []).append(path)
    ordered = sorted(groups.values(), key=lambda group: sorted(group)[0])
    if len(ordered) < 2:
        raise SplitRefused('SPLIT_REFUSED: SUBAGENTS needs at least 2 modules, found ' + str(len(ordered)))
    scopes = task.get('editScope') or []
    result = []
    for index, group in enumerate(ordered):
        if isinstance(scopes, dict):
            mine = {path: scopes[path] for path in group if path in scopes}
        else:
            mine = [scope for scope in scopes if any(covers(scope, path) for path in group)]
        result.append({'subagentId': 'sub-' + str(index + 1), 'module': module_of(group[0]),
                       'writePaths': sorted(group), 'editScope': mine or sorted(group)})
    for index, left in enumerate(result):
        for right in result[index + 1:]:
            for first in list(left['editScope']) + left['writePaths']:
                for second in list(right['editScope']) + right['writePaths']:
                    if covers(first, second) or covers(second, first):
                        raise SplitRefused('SPLIT_REFUSED: scope ' + first + ' of ' + left['subagentId'] + ' overlaps ' + second
                                           + ' of ' + right['subagentId'])
    return result


class FolderWorkspace:
    def __init__(self, name, base):
        # Sample: Copy base files journal.py and outbox.py into a new temp folder from one base digest.
        self.name, self.base_digest = name, package_digest(base)
        self.folder = tempfile.TemporaryDirectory(prefix='durai-sub-' + re.sub(r'[^A-Za-z0-9_.-]', '_', name) + '-')
        self.root = Path(self.folder.name)
        self.state = {}
        for path, value in base.items():
            self.write(path, value)

    def write(self, path, value):
        # Sample: Write /w/journal.py with "x = 2". Read it back as b"x = 2".
        target = self.root / path.lstrip('/')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(as_bytes(value))
        self.state[path] = as_bytes(value)

    def read(self, path):
        # Sample: Read /w/journal.py after a write of "x = 2". Return b"x = 2".
        return self.state[path]

    def files(self):
        # Sample: Return a copy of all files in the workspace.
        return dict(self.state)

    def close(self):
        # Sample: Remove the temp folder of the workspace.
        self.folder.cleanup()


def workspace_factory(name, base):
    # Sample: Create workspace sub-1 from base B-1. Return a FolderWorkspace.
    return FolderWorkspace(name, base)


class DevLoopChild:
    def __init__(self, executor, store, sources=None):
        # Sample: Wrap the SINGLE DevLoopExecutor E-1, candidate store C-1, and optional sources S-1. Keep no run state.
        self.executor, self.store, self.sources = executor, store, sources

    def base_digests(self, step):
        # Sample: Read the baseline journal.py of the child task from sources S-1. Return its path with the SHA-256 of those bytes. Without sources return nothing.
        if self.sources is None:
            return {}
        from .task_contract import build_task
        built = build_task(step['goal']['spec'])
        if built['state'] != 'READY':
            return {}
        return {path: sha(value) for path, value in self.sources.files(built['task']).items()}

    def __call__(self, step, context):
        # Sample: Run subtask journal. Loop outcome PASSED. Return SUCCEEDED with the restored package files and the base digests of its start files.
        result = self.executor(step, context)
        reference = result.get('package')
        if result['state'] == 'SUCCEEDED' and reference:
            files = self.store.restore(path=reference['path'], digest=reference['digest'])
            package = {'files': files, 'digest': package_digest(files)}
            base = self.base_digests(step)
            if base:
                package['base'] = base
            return dict(result, package=package, verified=True)
        return result


def default_acceptance(workspace, step, context):
    # Sample: Read a merged workspace. Refuse to pass because no acceptance callable is configured.
    return {'passed': False, 'detail': 'No acceptance callable configured'}


class SubagentsExecutor:
    def __init__(self, child, acceptance=default_acceptance, workspace_factory=workspace_factory, max_parallel=2,
                 timeout=None, clock=time.monotonic, git=None):
        # Sample: Keep child executor C-1, acceptance A-1, workspace factory F-1, maxParallel 2, no timeout. With git {repo, baseCommit, scratch}, a step with workspace "git" uses git worktrees.
        self.child, self.acceptance, self.make_workspace = child, acceptance, workspace_factory
        self.max_parallel, self.timeout, self.clock, self.git = max(1, int(max_parallel)), timeout, clock, git or {}

    def factory_for(self, goal):
        # Sample: Read goal.workspace. "folder" or absent gives folder copies. "git" or {kind: git, repo, baseCommit} gives git worktrees. Any other value is refused.
        choice = goal.get('workspace')
        if choice in (None, 'folder'):
            return self.make_workspace
        setup = dict(self.git, **choice) if isinstance(choice, dict) and choice.get('kind') == 'git' else dict(self.git) if choice == 'git' else None
        if setup is None:
            raise SplitRefused('WORKSPACE_REFUSED: goal.workspace must be "folder" or "git", found ' + str(choice))
        if not setup.get('repo') or not setup.get('baseCommit'):
            raise SplitRefused('WORKSPACE_REFUSED: a git workspace needs repo and baseCommit')
        from .workspace_git import git_workspace_factory
        return git_workspace_factory(setup['repo'], setup['baseCommit'], setup.get('scratch'))

    def __call__(self, step, context):
        # Sample: Run S-1 with journal.py and location_search.py. Both subagents SUCCEEDED. Merge, accept once. Return SUCCEEDED.
        goal = context['goal']
        task = goal.get('spec') if isinstance(goal, dict) else None
        if not isinstance(task, dict):
            raise Conflict('SUBAGENTS step goal requires a task spec')
        lock = threading.Lock()
        raw = context.get('emit')

        def emit(kind, payload):
            # Sample: Record SUBAGENT_STARTED for sub-1 of S-1. Serialize writes from all threads.
            if raw is not None:
                with lock:
                    raw(kind, dict(payload, stepId=step['id']))

        try:
            make = self.factory_for(goal)
            parts = split(task, goal.get('files'))
        except SplitRefused as error:
            return {'state': 'FAILED', 'detail': str(error)}
        base = {path: as_bytes(value) for path, value in (goal.get('files') or {}).items()}
        calls = (goal.get('budget') or {}).get('modelCalls')
        share = max(1, calls // len(parts)) if isinstance(calls, int) and not isinstance(calls, bool) else None
        outcomes = self.run_all(step, context, goal, task, parts, base, share, emit, make)
        for part in parts:
            found = outcomes[part['subagentId']]
            emit('SUBAGENT_FINISHED', {'subagentId': part['subagentId'], 'state': found['state'], 'detail': found.get('detail'),
                                       'packageDigest': (found.get('package') or {}).get('digest'),
                                       'usage': found.get('usage'), 'budget': {'modelCalls': share}})
        bad = [(part['subagentId'], outcomes[part['subagentId']]['state']) for part in parts
               if not self.eligible(outcomes[part['subagentId']])]
        if bad:
            states = {state for _, state in bad}
            state = 'CANCELLED' if states == {'CANCELLED'} else 'FAILED'
            return {'state': state, 'detail': 'Merge blocked: ' + ', '.join(name + ' ' + value for name, value in bad)}
        return self.merge(step, context, parts, base, outcomes, emit, make)

    def eligible(self, found):
        # Sample: Read SUCCEEDED with a package. Return True. Read SUCCEEDED without a package or FAILED. Return False.
        package = found.get('package')
        return (found['state'] == 'SUCCEEDED' and isinstance(package, dict) and isinstance(package.get('files'), dict)
                and found.get('verified', True) is not False)

    def run_all(self, step, context, goal, task, parts, base, share, emit, make=None):
        # Sample: Run 3 subtasks with maxParallel 2. Never run 3 at once. Return one terminal outcome per subagent.
        def run_one(part):
            # Sample: Run sub-1 on its own workspace. Exception gives LOST. Return a terminal state.
            emit('SUBAGENT_STARTED', {'subagentId': part['subagentId'], 'module': part['module'], 'writePaths': part['writePaths']})
            workspace = None
            try:
                workspace = (make or self.make_workspace)(step['id'] + '.' + part['subagentId'], base)
                sub_spec = dict(task, writePaths=part['writePaths'])
                if 'editScope' in task:
                    sub_spec['editScope'] = part['editScope']
                sub_spec.update((goal.get('subtasks') or {}).get(part['module'], {}))
                child_goal = {'spec': sub_spec, 'files': base, 'budget': {'modelCalls': share}}
                if (goal.get('suites') or {}).get(part['module']):
                    child_goal['suite'] = goal['suites'][part['module']]
                child_step = dict(step, id=step['id'] + '.' + part['subagentId'], goal=child_goal)
                child_context = dict(context, stepId=child_step['id'], goal=child_goal, workspace=workspace,
                                     subagentId=part['subagentId'], budget={'modelCalls': share})
                child_context.pop('emit', None)
                found = self.child(child_step, child_context)
            except Exception as error:
                return {'state': 'LOST', 'detail': type(error).__name__ + ': ' + str(error)}
            finally:
                if workspace is not None and hasattr(workspace, 'close'):
                    workspace.close()
            if not isinstance(found, dict) or found.get('state') not in TERMINAL:
                return {'state': 'LOST', 'detail': 'Child returned no terminal state'}
            return found

        pool = ThreadPoolExecutor(max_workers=self.max_parallel)
        futures = {part['subagentId']: pool.submit(run_one, part) for part in parts}
        deadline = None if self.timeout is None else self.clock() + self.timeout
        outcomes = {}
        for name, future in futures.items():
            try:
                wait = None if deadline is None else max(0.0, deadline - self.clock())
                outcomes[name] = future.result(timeout=wait)
            except WaitTimeout:
                future.cancel()
                outcomes[name] = {'state': 'LOST', 'detail': 'Timeout after ' + str(self.timeout) + ' seconds'}
        pool.shutdown(wait=deadline is None)
        return outcomes

    def merge(self, step, context, parts, base, outcomes, emit, make=None):
        # Sample: Merge sub-1 then sub-2. Path outside scope gives MERGE_CONFLICT and no merged package. Else accept once.
        changed = {}
        for part in parts:
            package = outcomes[part['subagentId']]['package']
            for path, value in package['files'].items():
                problem = None
                if not any(covers(scope, path) for scope in list(part['editScope']) + part['writePaths']):
                    problem = 'outside scope'
                elif path in changed:
                    problem = 'already changed by ' + changed[path]
                elif path in package.get('base', {}) and path in base and package['base'][path] != sha(base[path]):
                    problem = 'base digest differs'
                if problem:
                    detail = 'MERGE_CONFLICT: ' + part['subagentId'] + ' path ' + path + ' ' + problem
                    emit('MERGE_CONFLICT', {'subagentId': part['subagentId'], 'path': path, 'reason': problem})
                    return {'state': 'FAILED', 'detail': detail}
                changed[path] = part['subagentId']
        workspace = (make or self.make_workspace)(step['id'] + '.merge', base)
        try:
            for part in parts:
                package = outcomes[part['subagentId']]['package']
                for path, value in package['files'].items():
                    workspace.write(path, value)
                emit('MERGE_APPLIED', {'subagentId': part['subagentId'], 'packageDigest': package.get('digest') or package_digest(package['files']),
                                       'paths': sorted(package['files'])})
            verdict = self.acceptance(workspace, step, context)
            passed = bool(verdict.get('passed')) if isinstance(verdict, dict) else bool(verdict)
            detail = verdict.get('detail') if isinstance(verdict, dict) else None
            emit('ACCEPTANCE_FINISHED', {'passed': passed, 'detail': detail,
                                         'mergedDigest': package_digest(workspace.files())})
        finally:
            if hasattr(workspace, 'close'):
                workspace.close()
        if passed:
            return {'state': 'SUCCEEDED', 'detail': 'Merged ' + str(len(parts)) + ' subagents; acceptance passed', 'outcome': 'PASSED'}
        return {'state': 'FAILED', 'detail': 'Acceptance failed' + (': ' + str(detail) if detail else '')}


def register_subagents(providers, executor):
    # Sample: Register executor E-1 as workflow-step SUBAGENTS in registry P-1. Ask SUBAGENTS. Return True.
    providers.register(CAPABILITY, STRATEGY, executor)
    return providers
