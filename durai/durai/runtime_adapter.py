"""Sample: Describe a worker without resume. Call resume. Check UNSUPPORTED and zero worker calls."""
from .common import Conflict, digest

OPERATIONS = ('describe', 'prepare', 'execute', 'interrupt', 'resume', 'cancel', 'export_evidence', 'close')
STATES = ('DRAFT', 'NEEDS_CLARIFICATION', 'READY', 'RUNNING', 'PAUSED', 'WAITING_FOR_REVIEW',
          'SUCCEEDED', 'FAILED', 'CANCELLED', 'LOST')
TERMINAL = frozenset({'SUCCEEDED', 'FAILED', 'CANCELLED'})
TRANSITIONS = {
    'DRAFT': {'NEEDS_CLARIFICATION', 'READY', 'CANCELLED'},
    'NEEDS_CLARIFICATION': {'DRAFT', 'READY', 'CANCELLED'},
    'READY': {'RUNNING', 'NEEDS_CLARIFICATION', 'CANCELLED'},
    'RUNNING': {'PAUSED', 'WAITING_FOR_REVIEW', 'FAILED', 'CANCELLED', 'LOST'},
    'PAUSED': {'RUNNING', 'FAILED', 'CANCELLED', 'LOST'},
    'LOST': {'RUNNING', 'FAILED', 'CANCELLED'},
    'WAITING_FOR_REVIEW': {'SUCCEEDED', 'FAILED', 'CANCELLED'},
    'SUCCEEDED': set(), 'FAILED': set(), 'CANCELLED': set()}


def transition(current, new, actor='runtime'):
    # Sample: Move READY to RUNNING. Accept it. Record SUCCEEDED as runtime. Refuse it.
    if current not in TRANSITIONS or new not in TRANSITIONS[current]:
        raise Conflict('State transition is not permitted: ' + str(current) + ' -> ' + str(new))
    if new == 'SUCCEEDED' and actor != 'release':
        raise Conflict('Only the release controller can record SUCCEEDED')
    return new


class CodingWorker:
    runtime = 'abstract'
    version = '0'
    operations = frozenset({'describe'})

    def describe(self):
        # Sample: Describe deepagents 0.7.38 with execute. Return its name, version, and operations.
        return {'runtime': self.runtime, 'version': self.version, 'operations': sorted(self.operations)}


def invoke(worker, operation, *args, **kwargs):
    # Sample: Call resume on a worker without resume. Return UNSUPPORTED. Make no worker call.
    if operation not in OPERATIONS:
        raise Conflict('Operation is not part of the CodingWorker contract: ' + operation)
    described = worker.describe()
    if operation not in described['operations']:
        return {'state': 'UNSUPPORTED', 'operation': operation,
                'runtime': described['runtime'], 'version': described['version']}
    return getattr(worker, operation)(*args, **kwargs)


def check_candidate(task, before, after):
    # Sample: Permit /workspace/a.py. Change a.py. Accept it. Add /workspace/c.py. Refuse it.
    changed = sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
    undeclared = [path for path in changed if path not in task['writePaths']]
    if undeclared:
        raise Conflict('Candidate changed undeclared files: ' + ', '.join(undeclared))
    manifest = {path: digest(after[path]) for path in sorted(after)}
    return {'changed': changed, 'manifest': manifest, 'manifestDigest': digest(manifest),
            'taskDigest': task['digest'], 'authority': 'candidate-proposal-only'}


class ScriptedWorker(CodingWorker):
    runtime = 'scripted'
    version = '1'

    def __init__(self, writes, operations=OPERATIONS):
        # Sample: Script one write of 200 to /workspace/a.py. Allow all eight operations.
        self.writes, self.operations, self.calls = list(writes), frozenset(operations), []

    def prepare(self, task):
        # Sample: Prepare T-1. Return READY with the T-1 digest.
        self.calls.append('prepare')
        return {'state': 'READY', 'taskDigest': task['digest']}

    def execute(self, task, files, prompt=None, policies=None):
        # Sample: Apply the scripted a.py write to a copy. Return the copy and CANDIDATE.
        self.calls.append('execute')
        result = dict(files)
        for path, content in self.writes:
            result[path] = content
        return {'state': 'CANDIDATE', 'files': result, 'toolCalls': len(self.writes)}

    def resume(self, task, files, policies=None):
        # Sample: Resume T-1 after a pause. Repeat the scripted writes once.
        self.calls.append('resume')
        return self.execute(task, files)

    def interrupt(self, task):
        # Sample: Interrupt T-1. Return PAUSED.
        self.calls.append('interrupt')
        return {'state': 'PAUSED'}

    def cancel(self, task):
        # Sample: Cancel T-1. Return CANCELLED.
        self.calls.append('cancel')
        return {'state': 'CANCELLED'}

    def export_evidence(self, task):
        # Sample: Export T-1 evidence. Return the call names in order.
        return {'runtime': self.runtime, 'version': self.version, 'calls': list(self.calls)}

    def close(self):
        # Sample: Close the worker. Record close as the last call.
        self.calls.append('close')
