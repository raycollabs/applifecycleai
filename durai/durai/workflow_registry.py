"""Sample: Build the registry for durai_local. Run a SINGLE, SUBAGENTS, or TEAM step through the dev loop. Accept the shared workspace in the Docker sandbox."""
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
from .candidate_store import CandidateStore
from .common import Conflict
from .development_loop import CAPABILITY as WORKER, ExternalRuns, build_loop
from .file_leases import FileLeases
from .providers import Providers
from .runtime_deepagents import DeepAgentsWorker
from .strategy_subagents import DevLoopChild, SubagentsExecutor, as_bytes, register_subagents
from .strategy_team import ReviewerMember, TeamExecutor, register_team
from .task_contract import build_task
from .task_evaluator import SandboxEvaluator
from .task_ledger import TaskLedger
from .workflow_runner import CAPABILITY, DevLoopExecutor
from .workspace_manifest import manifest


def bytes_of(files):
    # Sample: Read files {"/workspace/a.py": "x = 1"}. Return {"/workspace/a.py": b"x = 1"}. Read none. Return {}.
    return {path: as_bytes(value) for path, value in (files or {}).items()}


def sandbox_acceptance(evaluator):
    # Sample: Build acceptance A-1 on SandboxEvaluator E-1. Run the shared workspace through E-1 and its Docker image. Return passed, detail, and the evidence digest. Never run Python on the host.
    def accept(workspace, step, context):
        # Sample: Read the merged journal.py and the protected suite of the step goal. Run them in the sandbox. Return passed True with evidence D-1, or passed False with the failed command names.
        goal = context['goal'] if isinstance(context.get('goal'), dict) else {}
        setup = goal.get('acceptance') or {}
        spec = setup.get('spec') or goal.get('spec')
        suite = bytes_of(setup.get('suite') or goal.get('suite'))
        if not isinstance(spec, dict) or not suite:
            return {'passed': False, 'detail': 'Acceptance needs a task spec and a protected suite in the step goal'}
        built = build_task(spec)
        if built['state'] != 'READY':
            return {'passed': False, 'detail': 'Acceptance task is not ready: ' + ', '.join(built['missing'])}
        try:
            verdict = evaluator.evaluate(built['task'], workspace.files(), suite, baseline=bytes_of(goal.get('files')))
        except Conflict as error:
            return {'passed': False, 'detail': 'Acceptance refused: ' + str(error)}
        failed = [item['command'] for item in verdict['results'] if item['state'] != 'PASS']
        passed = verdict['verdict'] == 'PASS'
        return {'passed': passed, 'evidence': verdict['digest'],
                'detail': ('Sandbox PASS evidence ' + verdict['digest']) if passed else
                          'Sandbox FAIL in ' + ', '.join(failed) + ' evidence ' + verdict['digest']}
    return accept


class GoalSources:
    def __init__(self):
        # Sample: Keep the baseline files and protected suite of the running step goals by task digest.
        self.lock, self.goals = threading.Lock(), {}

    def register(self, task, files, suite):
        # Sample: Store files F-1 and suite H-1 for task T-1 by its digest.
        with self.lock:
            self.goals[task['digest']] = (dict(files), dict(suite))

    def known(self, task):
        # Sample: Read task T-1. Return its registered files and suite, or refuse an unknown task.
        with self.lock:
            found = self.goals.get(task['digest'])
        if found is None:
            raise Conflict('No baseline is registered for task ' + task['taskId'])
        return found

    def select(self, task):
        # Sample: Read task T-1 whose sourceRevision covers only journal.py. Return journal.py from the goal files. Return all files when they match instead.
        files, _ = self.known(task)
        for chosen in (files, {path: value for path, value in files.items() if path in task['writePaths']}):
            if manifest(chosen)['digest'] == task['sourceRevision']:
                return chosen
        return files

    def files(self, task):
        # Sample: Read task T-1. Return the baseline files that match its sourceRevision. Refuse a different baseline.
        chosen = self.select(task)
        if manifest(chosen)['digest'] != task['sourceRevision']:
            raise Conflict('Baseline files differ from the task sourceRevision')
        return dict(chosen)

    def suite(self, task):
        # Sample: Read task T-1. Return its registered protected suite.
        return dict(self.known(task)[1])


class GoalDevLoop(DevLoopExecutor):
    def __init__(self, sources, *arguments, **options):
        # Sample: Wrap the dev loop with goal sources S-1. Register the goal files and suite before each run.
        super().__init__(*arguments, **options)
        self.sources = sources

    def __call__(self, step, context):
        # Sample: Read goal files F-1 and suite H-1 of step S-1. Register them under the task digest. Run the loop.
        goal = context['goal']
        built = build_task(goal['spec']) if isinstance(goal, dict) and isinstance(goal.get('spec'), dict) else {'state': 'NONE'}
        if built['state'] != 'READY':
            return super().__call__(step, context)
        self.sources.register(built['task'], bytes_of(goal.get('files')), bytes_of(goal.get('suite')))
        return super().__call__(step, context)


def default_providers(database='durai_local', settings=None, model_factory=None, evaluator=None, leases=None, advisor=None, reviewer_model=None):
    # Sample: Build registry P-1 for durai_local with workflow-step SINGLE, SUBAGENTS, and TEAM. Acceptance runs in the Docker sandbox. Settings may set scratch, maxParallel, leaseSeconds, git, and reviewerPolicies. A reviewer model makes the TEAM reviewer model-backed.
    settings = dict(settings or {})
    scratch = Path(settings.get('scratch') or tempfile.mkdtemp(prefix='durai-workflow-'))
    scratch.mkdir(parents=True, exist_ok=True)
    ledger, evaluator = TaskLedger(database), evaluator or SandboxEvaluator(scratch)
    store, sources, external = CandidateStore(settings.get('candidates') or scratch / 'candidates'), GoalSources(), ExternalRuns()
    leases = leases or FileLeases(database)
    if model_factory is None:
        def model_factory(attempt):
            # Sample: Return the first configured coding route for attempt 1. Make no call before the loop reserves budget.
            from .coding_worker import configured_model
            return configured_model()
    workers = Providers()
    workers.register(WORKER, 'deepagents@' + DeepAgentsWorker.version,
                     lambda model, folder, attempt_id, **shared: DeepAgentsWorker(model, folder, attempt_id, database=database, **shared))
    builder = build_loop(ledger, workers, evaluator, store, sources, model_factory, scratch / 'work', advisor, external)

    def current_for(spec):
        # Sample: Read the pins now in force for spec T-1: baseline revision, contract digest, suite digest, image, and runtime.
        from .worker_contract import resolve_contract
        built = build_task(spec)
        task = built['task'] if built['state'] == 'READY' else None
        if task is None:
            return {}
        _, suite = sources.known(task)
        return {'sourceRevision': manifest(sources.select(task))['digest'],
                'contractDigest': resolve_contract(spec['binding']['bindingId'], database=database)['digest'],
                'suiteDigest': manifest(suite)['digest'], 'imageId': evaluator.image_for(task),
                'runtime': spec['runtime']['name'] + '@' + DeepAgentsWorker.version}

    single = GoalDevLoop(sources, builder, database, current_for, external=external, leases=leases)
    acceptance = sandbox_acceptance(evaluator)
    providers = Providers()
    providers.register(CAPABILITY, 'SINGLE', single)
    register_subagents(providers, SubagentsExecutor(DevLoopChild(single, store, sources), acceptance=acceptance,
                                                     max_parallel=settings.get('maxParallel', 2), git=settings.get('git')))
    members = {'implementer': single, 'test-writer': single, 'reviewer': ReviewerMember(reviewer_model, settings.get('reviewerPolicies'))}
    register_team(providers, TeamExecutor(members, acceptance=acceptance, leases=leases,
                                          lease_seconds=settings.get('leaseSeconds', 900), scratch=scratch / 'teams'))
    providers.parts = SimpleNamespace(ledger=ledger, evaluator=evaluator, store=store, sources=sources, external=external,
                                      leases=leases, loop=builder, single=single, scratch=scratch)
    return providers
