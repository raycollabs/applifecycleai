"""Sample: Run workflow W-1 with one AUTO step over journal.py and location_search.py. Fix both with a scripted model. Check SUCCEEDED."""
import subprocess
import sys
import threading
import unittest
from durai.common import uid
from durai.db import prepare
from durai.development_loop import build_loop
from durai.execution_events import replay
from durai.harness import prepare_checkpoints
from durai.providers import Providers
from durai.strategy_subagents import DevLoopChild, SubagentsExecutor, register_subagents
from durai.workflow_runner import CAPABILITY, DevLoopExecutor, build_workflow, start_workflow
from durai.workspace_manifest import manifest
from test_dev_loop import LoopHarness, loop_spec, pins_for
from test_workspace_worker import SequenceModel

DATABASE = 'durai_test'
JOURNAL = b'def balanced(lines):\n    # Sample: Read lines 100 and -100. Return True.\n    return sum(lines) == 1\n'
SEARCH = b'def nearest(points, target):\n    # Sample: Read points 1, 5, 9 and target 6. Return 5.\n    return max(points, key=lambda point: abs(point - target))\n'
BASE = {'/workspace/journal.py': JOURNAL, '/workspace/location_search.py': SEARCH}
SUITES = {
    '/workspace/journal.py': {
        '/workspace/tests/__init__.py': b'',
        '/workspace/tests/test_journal.py': b'"""Sample: Post lines 100 and -100. Check balanced."""\nimport unittest\nfrom journal import balanced\n\n\n'
                                           b'class JournalTests(unittest.TestCase):\n    def test_balanced(self):\n'
                                           b'        # Sample: Read lines 100 and -100. Check True.\n        self.assertTrue(balanced([100, -100]))\n'},
    '/workspace/location_search.py': {
        '/workspace/tests/__init__.py': b'',
        '/workspace/tests/test_location_search.py': b'"""Sample: Search points 1, 5, 9 near 6. Check 5."""\nimport unittest\nfrom location_search import nearest\n\n\n'
                                                   b'class SearchTests(unittest.TestCase):\n    def test_nearest(self):\n'
                                                   b'        # Sample: Read points 1, 5, 9 and target 6. Check 5.\n        self.assertEqual(nearest([1, 5, 9], 6), 5)\n'}}
FIXES = [('edit_file', {'file_path': '/workspace/journal.py', 'old_string': '== 1', 'new_string': '== 0'}),
         ('edit_file', {'file_path': '/workspace/location_search.py', 'old_string': 'max(points', 'new_string': 'min(points'})]


class ModuleSources:
    def files(self, task):
        # Sample: Read task T-1 that writes journal.py. Return only the baseline journal.py.
        return {task['writePaths'][0]: BASE[task['writePaths'][0]]}

    def suite(self, task):
        # Sample: Read task T-1 that writes journal.py. Return only the journal suite.
        return dict(SUITES[task['writePaths'][0]])


class Ordered:
    def __init__(self):
        # Sample: Hand out the journal fix first and the location_search fix second. Keep the model list.
        self.lock, self.count, self.models = threading.Lock(), 0, []

    def __call__(self, attempt):
        # Sample: Return the scripted model for call 1 with the journal fix. Return call 2 with the location_search fix.
        with self.lock:
            model = SequenceModel(steps=[FIXES[self.count]], replies=[])
            self.count += 1
            self.models.append(model)
            return model


def accept(workspace, step, context):
    # Sample: Write both suites next to the merged files. Run unittest discover. Return passed when both modules pass.
    for suite in SUITES.values():
        for path, value in suite.items():
            workspace.write(path, value)
    run = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests'], cwd=str(workspace.root / 'workspace'),
                         capture_output=True, text=True, timeout=120)
    return {'passed': run.returncode == 0, 'detail': run.stderr[-300:]}


class SubagentIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply the core migrations and the checkpoint tables to durai_test.
        prepare(DATABASE)
        prepare_checkpoints(DATABASE)

    def test_two_module_synthetic_task(self):
        # Sample: AUTO step over journal.py and location_search.py. Each subagent runs the real dev loop with a scripted model, passes its own protected suite, and the merge passes acceptance.
        harness = LoopHarness([])
        models = Ordered()
        loop = build_loop(harness.ledger, harness.providers, harness.evaluator, harness.store, ModuleSources(), models,
                          harness.scratch / 'work')
        single = DevLoopExecutor(loop, DATABASE, pins_for)
        child = DevLoopChild(single, harness.store)
        subtasks = {}
        for module, path in (('/workspace/journal', '/workspace/journal.py'), ('/workspace/location_search', '/workspace/location_search.py')):
            subtasks[module] = {'taskId': 'T-' + uid()[:8], 'deliverables': [path], 'sourceRevision': manifest({path: BASE[path]})['digest'],
                                'protectedSuite': {'id': 'H-' + module.rsplit('/', 1)[1], 'digest': manifest(SUITES[path])['digest']}}
        parent = loop_spec(writePaths=list(BASE), deliverables=list(BASE))
        goal = {'spec': parent, 'files': {path: value.decode() for path, value in BASE.items()},
                'budget': {'modelCalls': 40}, 'subtasks': subtasks}
        providers = Providers()
        providers.register(CAPABILITY, 'SINGLE', single)
        register_subagents(providers, SubagentsExecutor(child, acceptance=accept, max_parallel=1))
        spec = {'format': 'durai-workflow/v1', 'id': 'wf-' + uid()[:8], 'steps': [{'id': 'S-1', 'goal': goal, 'strategy': 'AUTO'}]}
        result = start_workflow(build_workflow(spec, providers, DATABASE), spec['id'], DATABASE)
        recorded = [(row['kind'], row['payload']) for row in replay(spec['id'], database=DATABASE)]
        names = [kind for kind, payload in recorded]
        finished = [payload for kind, payload in recorded if kind == 'SUBAGENT_FINISHED']
        self.assertEqual([(item['subagentId'], item['state']) for item in finished], [('sub-1', 'SUCCEEDED'), ('sub-2', 'SUCCEEDED')], finished)
        self.assertNotIn('STRATEGY_FALLBACK', names)
        self.assertEqual((result['status'], models.count), ('SUCCEEDED', 2))
        self.assertEqual(names.count('MERGE_APPLIED'), 2)
        self.assertTrue(next(payload for kind, payload in recorded if kind == 'ACCEPTANCE_FINISHED')['passed'])


if __name__ == '__main__':
    unittest.main()
