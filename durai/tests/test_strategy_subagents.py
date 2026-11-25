"""Sample: Split step S-1 into journal.py and location_search.py. Run two fake subagents. Merge in order. Accept once."""
import threading
import time
import unittest
from durai.strategy_subagents import FolderWorkspace, SubagentsExecutor, package_digest, sha, split

BASE = {'/w/journal.py': b'def post(lines):\n    return None\n', '/w/location_search.py': b'def near(point):\n    return []\n',
        '/w/outbox.py': b'QUEUE = []\n'}


def goal(paths=('/w/journal.py', '/w/location_search.py'), scope=None, calls=40):
    # Sample: Build a goal that writes journal.py and location_search.py with 40 model calls left.
    spec = {'writePaths': list(paths)}
    if scope is not None:
        spec['editScope'] = scope
    return {'spec': spec, 'files': {path: value.decode() for path, value in BASE.items()}, 'budget': {'modelCalls': calls}}


class Fake:
    def __init__(self, states=None, extra=None, boom=(), delay=0.0):
        # Sample: Script sub-1 SUCCEEDED and sub-2 FAILED. Keep a call log and the peak concurrency.
        self.states, self.extra, self.boom, self.delay = states or {}, extra or {}, boom, delay
        self.calls, self.running, self.peak, self.lock, self.contexts = [], 0, 0, threading.Lock(), []

    def __call__(self, step, context):
        # Sample: Run sub-1. Return a package that rewrites its own write path.
        with self.lock:
            self.running += 1
            self.peak = max(self.peak, self.running)
            self.calls.append(context['subagentId'])
            self.contexts.append(context)
        try:
            time.sleep(self.delay)
            if context['subagentId'] in self.boom:
                raise RuntimeError('worker crashed')
            state = self.states.get(context['subagentId'], 'SUCCEEDED')
            paths = step['goal']['spec']['writePaths']
            files = {path: b'# edited by ' + context['subagentId'].encode() + b'\n' for path in paths}
            files.update(self.extra.get(context['subagentId'], {}))
            base = {path: sha(BASE[path]) for path in files if path in BASE}
            return {'state': state, 'detail': 'fake ' + state, 'usage': {'modelCalls': 3},
                    'package': {'files': files, 'digest': package_digest(files), 'base': base}}
        finally:
            with self.lock:
                self.running -= 1


class Acceptance:
    def __init__(self, passed=True):
        # Sample: Pass acceptance. Keep the count of calls and the merged files seen.
        self.passed, self.calls, self.seen = passed, 0, []

    def __call__(self, workspace, step, context):
        # Sample: Read the merged workspace. Count one call. Return passed.
        self.calls += 1
        self.seen.append(workspace.files())
        return {'passed': self.passed, 'detail': 'scripted acceptance'}


def run(child, acceptance=None, goal_value=None, **options):
    # Sample: Run step S-1 of W-1 with fake child C-1. Return the result and the emitted events.
    events = []
    acceptance = acceptance or Acceptance()
    executor = SubagentsExecutor(child, acceptance=acceptance, **options)
    context = {'workflowId': 'W-1', 'stepId': 'S-1', 'goal': goal_value or goal(), 'steps': {},
               'emit': lambda kind, payload: events.append((kind, payload))}
    result = executor({'id': 'S-1', 'goal': context['goal'], 'strategy': 'SUBAGENTS'}, context)
    return result, events, acceptance


def kinds(events):
    # Sample: Read events. Return only their kinds in order.
    return [kind for kind, payload in events]


class SubagentTests(unittest.TestCase):
    def test_split_by_module(self):
        # Sample: Split writePaths journal.py, test_journal.py, location_search.py. Check 2 subtasks, journal first, test file with journal.
        parts = split({'writePaths': ['/w/location_search.py', '/w/test_journal.py', '/w/journal.py']})
        self.assertEqual([part['writePaths'] for part in parts], [['/w/journal.py', '/w/test_journal.py'], ['/w/location_search.py']])
        self.assertEqual([part['subagentId'] for part in parts], ['sub-1', 'sub-2'])

    def test_symbol_edit_scope_split_per_module(self):
        # Sample: Give editScope journal.py post and location_search.py near. Check each subtask keeps only its own path and symbol.
        parts = split({'writePaths': ['/w/journal.py', '/w/location_search.py'],
                       'editScope': {'/w/journal.py': ['post'], '/w/location_search.py': ['near']}})
        self.assertEqual([part['editScope'] for part in parts], [{'/w/journal.py': ['post']}, {'/w/location_search.py': ['near']}])

    def test_overlapping_scopes_refused(self):
        # Sample: Give editScope /w/ that covers journal.py and location_search.py. Check SPLIT_REFUSED and no child call.
        child = Fake()
        result, events, acceptance = run(child, goal_value=goal(scope=['/w/']))
        self.assertEqual(result['state'], 'FAILED')
        self.assertIn('SPLIT_REFUSED', result['detail'])
        self.assertEqual((child.calls, events, acceptance.calls), ([], [], 0))

    def test_subagents_merge_in_order(self):
        # Sample: Run sub-1 and sub-2 with sub-1 slower. Check MERGE_APPLIED sub-1 then sub-2 and the merged files hold both edits.
        child = Fake(delay=0.0)
        slow = lambda step, context: (time.sleep(0.15) if context['subagentId'] == 'sub-1' else None) or child(step, context)
        result, events, acceptance = run(slow)
        self.assertEqual(result['state'], 'SUCCEEDED')
        merges = [payload['subagentId'] for kind, payload in events if kind == 'MERGE_APPLIED']
        self.assertEqual(merges, ['sub-1', 'sub-2'])
        merged = acceptance.seen[0]
        self.assertEqual((merged['/w/journal.py'], merged['/w/location_search.py'], merged['/w/outbox.py']),
                         (b'# edited by sub-1\n', b'# edited by sub-2\n', BASE['/w/outbox.py']))

    def test_merge_conflict_returns_to_orchestrator(self):
        # Sample: Let sub-2 also write journal.py. Check MERGE_CONFLICT, step FAILED, acceptance never called, no MERGE_APPLIED.
        child = Fake(extra={'sub-2': {'/w/journal.py': b'# stolen\n'}})
        result, events, acceptance = run(child)
        self.assertEqual(result['state'], 'FAILED')
        self.assertIn('MERGE_CONFLICT: sub-2 path /w/journal.py', result['detail'])
        self.assertIn('MERGE_CONFLICT', kinds(events))
        self.assertNotIn('MERGE_APPLIED', kinds(events))
        self.assertEqual(acceptance.calls, 0)

    def test_failed_subagent_blocks_merge(self):
        # Sample: Let sub-2 end FAILED. Check the step FAILED, no merge events, acceptance never called, sub-1 still recorded SUCCEEDED.
        child = Fake(states={'sub-2': 'FAILED'})
        result, events, acceptance = run(child)
        self.assertEqual(result['state'], 'FAILED')
        self.assertIn('sub-2 FAILED', result['detail'])
        self.assertEqual(acceptance.calls, 0)
        self.assertFalse({'MERGE_APPLIED', 'MERGE_CONFLICT'} & set(kinds(events)))
        finished = {payload['subagentId']: payload['state'] for kind, payload in events if kind == 'SUBAGENT_FINISHED'}
        self.assertEqual(finished, {'sub-1': 'SUCCEEDED', 'sub-2': 'FAILED'})

    def test_lost_subagent_recorded(self):
        # Sample: Let sub-1 raise an exception. Check SUBAGENT_FINISHED sub-1 LOST with the error and the step FAILED without merge.
        result, events, acceptance = run(Fake(boom=('sub-1',)))
        finished = {payload['subagentId']: payload for kind, payload in events if kind == 'SUBAGENT_FINISHED'}
        self.assertEqual(finished['sub-1']['state'], 'LOST')
        self.assertIn('RuntimeError: worker crashed', finished['sub-1']['detail'])
        self.assertEqual((result['state'], acceptance.calls), ('FAILED', 0))

    def test_timeout_marks_subagent_lost(self):
        # Sample: Let sub-1 sleep 0.4 seconds with timeout 0.05. Check sub-1 LOST with Timeout and the step FAILED.
        result, events, acceptance = run(Fake(delay=0.4), timeout=0.05)
        finished = {payload['subagentId']: payload for kind, payload in events if kind == 'SUBAGENT_FINISHED'}
        self.assertEqual(finished['sub-1']['state'], 'LOST')
        self.assertIn('Timeout', finished['sub-1']['detail'])
        self.assertEqual((result['state'], acceptance.calls), ('FAILED', 0))

    def test_parallel_limit_respected(self):
        # Sample: Run 4 subtasks with maxParallel 2. Check the peak of running subagents is 2. Run with maxParallel 1. Check peak 1.
        paths = ['/w/a.py', '/w/b.py', '/w/c.py', '/w/d.py']
        for limit in (2, 1):
            child = Fake(delay=0.1)
            result, events, acceptance = run(child, goal_value=goal(paths=paths), max_parallel=limit)
            self.assertEqual((child.peak, sorted(child.calls)), (limit, ['sub-1', 'sub-2', 'sub-3', 'sub-4']))

    def test_budget_divided_by_subtask_count(self):
        # Sample: Give 40 model calls to 2 subtasks. Check each child sees 20 calls and its own step id.
        child = Fake()
        run(child)
        self.assertEqual(sorted(context['budget']['modelCalls'] for context in child.contexts), [20, 20])
        self.assertEqual(sorted(context['stepId'] for context in child.contexts), ['S-1.sub-1', 'S-1.sub-2'])

    def test_acceptance_runs_once(self):
        # Sample: Run 3 subagents that all succeed. Check acceptance called once, after all MERGE_APPLIED, and ACCEPTANCE_FINISHED once.
        paths = ['/w/a.py', '/w/b.py', '/w/c.py']
        result, events, acceptance = run(Fake(), goal_value=goal(paths=paths))
        self.assertEqual((result['state'], acceptance.calls), ('SUCCEEDED', 1))
        names = kinds(events)
        self.assertEqual(names.count('ACCEPTANCE_FINISHED'), 1)
        self.assertEqual(names.index('ACCEPTANCE_FINISHED'), len(names) - 1)
        self.assertEqual(names.count('MERGE_APPLIED'), 3)

    def test_failed_acceptance_fails_step(self):
        # Sample: Fail acceptance on the merged workspace. Check step FAILED and ACCEPTANCE_FINISHED passed False.
        result, events, acceptance = run(Fake(), acceptance=Acceptance(passed=False))
        self.assertEqual(result['state'], 'FAILED')
        self.assertFalse(next(payload for kind, payload in events if kind == 'ACCEPTANCE_FINISHED')['passed'])

    def test_stale_base_digest_conflicts(self):
        # Sample: Let sub-1 claim a base digest that differs from the base journal.py. Check MERGE_CONFLICT base digest differs.
        class Stale(Fake):
            def __call__(self, step, context):
                found = super().__call__(step, context)
                found['package']['base']['/w/journal.py'] = sha(b'other')
                return found
        result, events, acceptance = run(Stale())
        self.assertIn('base digest differs', result['detail'])

    def test_stale_base_from_dev_loop_child(self):
        # Sample: The orchestrator base journal.py is "v1". The child dev loop started from "v0" and passed. Check DevLoopChild reports the "v0" digest and the merge ends in MERGE_CONFLICT base digest differs.
        from durai.strategy_subagents import DevLoopChild
        from test_task_contract import feature_spec
        journal, search = '/workspace/journal.py', '/workspace/location_search.py'
        base = {journal: b'v1\n', search: b'w1\n'}
        started = {journal: b'v0\n', search: b'w1\n'}

        class Single:
            def __call__(self, step, context):
                return {'state': 'SUCCEEDED', 'detail': 'PASSED', 'package': {'path': step['id'], 'digest': 'd'}}

        class Store:
            def restore(self, path, digest):
                return {journal: b'v2\n'} if path.endswith('sub-1') else {search: b'w2\n'}

        class Sources:
            def files(self, task):
                return {path: started[path] for path in task['writePaths']}

        parent = feature_spec(writePaths=[journal, search], deliverables=[journal])
        subtasks = {'/workspace/journal': {'taskId': 'T-journal', 'deliverables': [journal]},
                    '/workspace/location_search': {'taskId': 'T-search', 'deliverables': [search]}}
        step_goal = {'spec': parent, 'files': {path: value.decode() for path, value in base.items()}, 'subtasks': subtasks}
        child = DevLoopChild(Single(), Store(), Sources())
        context = {'workflowId': 'W-1', 'stepId': 'S-1', 'goal': step_goal, 'steps': {}, 'emit': lambda kind, payload: None}
        result = SubagentsExecutor(child, acceptance=Acceptance(), max_parallel=1)({'id': 'S-1', 'goal': step_goal}, context)
        self.assertEqual(result['state'], 'FAILED')
        self.assertIn('MERGE_CONFLICT: sub-1 path ' + journal + ' base digest differs', result['detail'])

    def test_workspace_folder_copy(self):
        # Sample: Copy base files into a folder workspace. Write journal.py. Check the file on disk and the base digest.
        workspace = FolderWorkspace('S-1.sub-1', BASE)
        workspace.write('/w/journal.py', 'x = 2')
        self.assertEqual((workspace.root / 'w' / 'journal.py').read_bytes(), b'x = 2')
        self.assertEqual(workspace.base_digest, package_digest(BASE))
        workspace.close()


if __name__ == '__main__':
    unittest.main()
