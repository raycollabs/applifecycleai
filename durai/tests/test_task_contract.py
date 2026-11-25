"""Sample: Declare FEATURE T-1 for a.py. Seal it. Refuse a write to c.py."""
import copy
import unittest
from durai.common import Conflict, digest
from durai.generation import DEFAULT_SPEC
from durai.runtime_adapter import ScriptedWorker, check_candidate, invoke, transition
from durai.task_contract import DEFAULT_BUDGET, build_task, verify_task

SOURCE = digest(b'def book():\n    return 200\n')
CONTRACT = digest({'contract': 'C-1'})
SUITE = digest({'suite': 'H-1'})


def feature_spec(**changes):
    # Sample: Build FEATURE T-1. Permit a.py and b.py writes. Deny /workspace/tests/**.
    spec = {'applicationId': 'trading', 'taskId': 'T-1', 'kind': 'FEATURE', 'profile': 'software',
            'objective': 'Add declared path validation to the change manifest.',
            'sourceRevision': SOURCE, 'binding': {'bindingId': 'foundation-generic-v2', 'contractDigest': CONTRACT},
            'readPaths': ['/workspace/**'], 'writePaths': ['/workspace/a.py', '/workspace/b.py'],
            'deniedPaths': ['/workspace/tests/**'], 'validationCommands': ['python-compile', 'python-unittest'],
            'protectedSuite': {'id': 'H-1', 'digest': SUITE}, 'environment': {'imageId': 'sha256:I-1'},
            'runtime': {'name': 'deepagents', 'version': '0.7.38'}, 'budget': dict(DEFAULT_BUDGET),
            'deliverables': ['/workspace/a.py'],
            'readiness': {'shellRunnable': True, 'failingAcceptanceGoal': 'G-7'}}
    spec.update(changes)
    return spec


def current_pins(**changes):
    # Sample: Return source S-1, contract C-1, suite H-1, image I-1, and deepagents 0.7.38.
    value = {'sourceRevision': SOURCE, 'contractDigest': CONTRACT, 'suiteDigest': SUITE,
             'imageId': 'sha256:I-1', 'runtime': 'deepagents@0.7.38'}
    value.update(changes)
    return value


class TaskSpecTests(unittest.TestCase):
    def test_complete_feature_task_is_ready(self):
        # Sample: Seal FEATURE T-1 twice. Check READY and the same digest.
        first, second = build_task(feature_spec()), build_task(feature_spec())
        self.assertEqual(first['state'], 'READY')
        self.assertEqual(first['task']['digest'], second['task']['digest'])
        self.assertFalse(first['task']['releaseAuthority'])

    def test_missing_fields_need_clarification(self):
        # Sample: Omit objective and writePaths. Check NEEDS_CLARIFICATION with both names.
        result = build_task(feature_spec(objective='', writePaths=[]))
        self.assertEqual(result, {'state': 'NEEDS_CLARIFICATION', 'missing': ['objective', 'writePaths']})

    def test_kind_readiness_is_required(self):
        # Sample: Remove G-7 from FEATURE T-1. Remove baselineRunnable from REPAIR T-2. Check two clarifications.
        feature = build_task(feature_spec(readiness={'shellRunnable': True}))
        repair = build_task(feature_spec(taskId='T-2', kind='REPAIR', readiness={'regressionSuite': 'R-1'}))
        self.assertEqual(feature['missing'], ['readiness.failingAcceptanceGoal'])
        self.assertEqual(repair['missing'], ['readiness.baselineRunnable'])

    def test_escaping_and_denied_paths_are_refused(self):
        # Sample: Request ../secrets, C:\x.py, a glob write, and a denied test file. Check four refusals.
        for changes in ({'readPaths': ['/workspace/../secrets']}, {'writePaths': ['C:\\x.py']},
                        {'writePaths': ['/workspace/*.py']}, {'writePaths': ['/workspace/tests/h.py']}):
            with self.subTest(changes=changes), self.assertRaises(Conflict):
                build_task(feature_spec(**changes))

    def test_unknown_validation_command_is_refused(self):
        # Sample: Request python-unittest. Check READY. Request bash-any. Check refusal.
        self.assertEqual(build_task(feature_spec(validationCommands=['python-unittest']))['state'], 'READY')
        with self.assertRaisesRegex(Conflict, 'bash-any'):
            build_task(feature_spec(validationCommands=['bash-any']))

    def test_trading_profile_keeps_domain_rules(self):
        # Sample: Submit a trading task with ADD_LOTS. Check NEEDS_CLARIFICATION from the domain check.
        domain = dict(DEFAULT_SPEC, correctionDirection='ADD_LOTS')
        result = build_task(feature_spec(profile='trading', domain=domain))
        self.assertEqual(result['state'], 'NEEDS_CLARIFICATION')
        self.assertIn('REMOVE_LOTS', result['missing'][0])
        self.assertEqual(build_task(feature_spec(profile='trading', domain=dict(DEFAULT_SPEC)))['state'], 'READY')

    def test_changed_pin_makes_task_stale(self):
        # Sample: Seal T-1 with source S-1. Verify against source S-2 and a new runtime. Check refusal.
        task = build_task(feature_spec())['task']
        self.assertEqual(verify_task(task, current_pins()), task)
        with self.assertRaisesRegex(Conflict, 'runtime, sourceRevision'):
            verify_task(task, current_pins(sourceRevision=digest(b'S-2'), runtime='deepagents@0.8.0'))
        changed = dict(copy.deepcopy(task), objective='Delete the manifest.')
        with self.assertRaisesRegex(Conflict, 'digest differs'):
            verify_task(changed, current_pins())


class RuntimeAdapterTests(unittest.TestCase):
    def test_unsupported_operation_is_structured(self):
        # Sample: Describe a worker without resume. Call resume. Check UNSUPPORTED and zero worker calls.
        task = build_task(feature_spec())['task']
        worker = ScriptedWorker([], operations=('describe', 'prepare', 'execute'))
        result = invoke(worker, 'resume', task, {})
        self.assertEqual(result['state'], 'UNSUPPORTED')
        self.assertEqual(worker.calls, [])
        with self.assertRaises(Conflict):
            invoke(worker, 'shell', task)

    def test_fake_worker_changes_only_declared_files(self):
        # Sample: Permit a.py. Write 200 to a.py. Accept it. Write c.py. Refuse it.
        task = build_task(feature_spec())['task']
        before = {'/workspace/a.py': b'return 199\n', '/workspace/asset.bin': b'\x00\xff'}
        good = invoke(ScriptedWorker([('/workspace/a.py', b'return 200\n')]), 'execute', task, before)
        receipt = check_candidate(task, before, good['files'])
        self.assertEqual(receipt['changed'], ['/workspace/a.py'])
        self.assertEqual(good['files']['/workspace/asset.bin'], b'\x00\xff')
        bad = invoke(ScriptedWorker([('/workspace/c.py', b'x = 1\n')]), 'execute', task, before)
        with self.assertRaisesRegex(Conflict, 'c.py'):
            check_candidate(task, before, bad['files'])

    def test_state_transitions_are_enforced(self):
        # Sample: Move READY to RUNNING. Move RUNNING to DRAFT. Record SUCCEEDED as runtime. Check two refusals.
        self.assertEqual(transition('READY', 'RUNNING'), 'RUNNING')
        with self.assertRaises(Conflict):
            transition('RUNNING', 'DRAFT')
        with self.assertRaisesRegex(Conflict, 'release controller'):
            transition('WAITING_FOR_REVIEW', 'SUCCEEDED')
        self.assertEqual(transition('WAITING_FOR_REVIEW', 'SUCCEEDED', actor='release'), 'SUCCEEDED')


if __name__ == '__main__':
    unittest.main()
