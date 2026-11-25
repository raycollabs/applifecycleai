"""Sample: Run workflow W-1 with steps S-1 and S-2. Gate before S-2. Approve as durai_reviewer. Check S-2 runs once."""
import time
import unittest
from langgraph.types import interrupt
from durai.common import Conflict, uid
from durai.context_store import ContextStore
from durai.db import prepare
from durai.execution_events import replay
from durai.harness import prepare_checkpoints
from durai.providers import Providers
from durai.workflow_definition import digest, validate
from durai.workflow_gates import approve, gate_state
from durai.workflow_runner import CAPABILITY, DevLoopExecutor, build_workflow, resume_workflow, start_workflow, sweep_gates

DATABASE = 'durai_test'


class Scripted:
    def __init__(self, log, states=None):
        # Sample: Script S-1 SUCCEEDED and S-2 FAILED. Keep an empty call log.
        self.log, self.states = log, states or {}

    def __call__(self, step, context):
        # Sample: Run S-2. Append S-2 to the call log. Return the scripted state.
        self.log.append(step['id'])
        return {'state': self.states.get(step['id'], 'SUCCEEDED'), 'detail': 'scripted ' + step['id']}


def registry(log, states=None):
    # Sample: Register the scripted SINGLE executor in a new registry. Return it.
    providers = Providers()
    providers.register(CAPABILITY, 'SINGLE', Scripted(log, states))
    return providers


def definition(gate=None, workflow_id=None):
    # Sample: Build W-1 with S-1 and S-2. Put the given gate on S-2.
    second = {'id': 'S-2', 'goal': 'Book 2 lots of 100', 'strategy': 'SINGLE'}
    if gate:
        second['gate'] = gate
    return {'format': 'durai-workflow/v1', 'id': workflow_id or 'wf-' + uid()[:8],
            'steps': [{'id': 'S-1', 'goal': 'Validate lots', 'strategy': 'SINGLE'}, second]}


def events(workflow_id):
    # Sample: Replay run W-1. Return its event kinds with payloads in order.
    return [(row['kind'], row['payload']) for row in replay(workflow_id, database=DATABASE)]


def kinds(workflow_id):
    # Sample: Replay run W-1. Return only its event kinds.
    return [kind for kind, payload in events(workflow_id)]


def subject_of(workflow_id):
    # Sample: Read GATE_OPENED of W-1. Return its subject digest.
    return next(payload['subjectDigest'] for kind, payload in events(workflow_id) if kind == 'GATE_OPENED')


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply the core migrations and the LangGraph checkpoint tables to durai_test.
        prepare(DATABASE)
        prepare_checkpoints(DATABASE)

    def test_steps_run_in_order(self):
        # Sample: Workflow W-1 with steps S-1 and S-2. Check S-1 SUCCEEDED before S-2 starts.
        log, spec = [], definition()
        result = start_workflow(build_workflow(spec, registry(log), DATABASE), spec['id'], DATABASE)
        self.assertEqual(log, ['S-1', 'S-2'])
        self.assertEqual((result['status'], result['steps']), ('SUCCEEDED', {'S-1': 'SUCCEEDED', 'S-2': 'SUCCEEDED'}))
        seen = events(spec['id'])
        self.assertEqual([kind for kind, payload in seen],
                         ['WORKFLOW_STARTED', 'STEP_STARTED', 'STEP_FINISHED', 'STEP_STARTED', 'STEP_FINISHED', 'WORKFLOW_FINISHED'])
        self.assertEqual([payload['state'] for kind, payload in seen if kind == 'STEP_FINISHED'], ['SUCCEEDED', 'SUCCEEDED'])

    def test_failed_step_stops_the_workflow(self):
        # Sample: Workflow W-1 where S-1 returns FAILED. Check S-2 never starts and WORKFLOW_FINISHED is FAILED.
        log, spec = [], definition()
        result = start_workflow(build_workflow(spec, registry(log, {'S-1': 'FAILED'}), DATABASE), spec['id'], DATABASE)
        self.assertEqual((log, result['status']), (['S-1'], 'FAILED'))
        self.assertEqual(events(spec['id'])[-1][1]['state'], 'FAILED')

    def test_definition_digest_is_stable(self):
        # Sample: Validate W-1 twice. Check one digest.
        spec = definition(workflow_id='W-1')
        self.assertEqual(digest(spec), digest(validate(spec)))
        self.assertEqual(digest(spec), digest(dict(spec, steps=[dict(step) for step in spec['steps']])))
        self.assertNotEqual(digest(spec), digest(definition(workflow_id='W-2')))

    def test_unknown_strategy_is_refused(self):
        # Sample: Validate W-1 with S-1 strategy SWARM. Check refusal that lists the four strategies. Use AUTO. Check acceptance.
        spec = definition()
        spec['steps'][0]['strategy'] = 'SWARM'
        with self.assertRaisesRegex(Conflict, 'must be SINGLE, AUTO, SUBAGENTS, or TEAM'):
            validate(spec)
        spec['steps'][0]['strategy'] = 'AUTO'
        self.assertEqual(validate(spec)['steps'][0]['strategy'], 'AUTO')

    def test_explicit_team_strategy(self):
        # Sample: Validate W-1 with S-1 strategy TEAM. Check acceptance. Register a TEAM executor. Run. Check STEP_STARTED strategy TEAM decidedBy workflow-definition and no DECISION_RECORDED.
        spec = definition()
        spec['steps'][0]['strategy'] = 'TEAM'
        self.assertEqual(validate(spec)['steps'][0]['strategy'], 'TEAM')
        log, providers = [], registry([])
        providers.register(CAPABILITY, 'TEAM', Scripted(log))
        result = start_workflow(build_workflow(spec, providers, DATABASE), spec['id'], DATABASE)
        started = [payload for kind, payload in events(spec['id']) if kind == 'STEP_STARTED']
        self.assertEqual((result['status'], log), ('SUCCEEDED', ['S-1']))
        self.assertEqual((started[0]['strategy'], started[0]['decidedBy']), ('TEAM', 'workflow-definition'))
        self.assertNotIn('DECISION_RECORDED', kinds(spec['id']))

    def test_explicit_subagents_strategy(self):
        # Sample: Validate W-1 with S-1 strategy SUBAGENTS. Check acceptance. Run without a SUBAGENTS executor. Check S-1 FAILED with no fallback to SINGLE.
        spec = definition()
        spec['steps'][0]['strategy'] = 'SUBAGENTS'
        self.assertEqual(validate(spec)['steps'][0]['strategy'], 'SUBAGENTS')
        log = []
        result = start_workflow(build_workflow(spec, registry(log), DATABASE), spec['id'], DATABASE)
        self.assertEqual((result['status'], log), ('FAILED', []))
        self.assertNotIn('STRATEGY_FALLBACK', kinds(spec['id']))
        finished = next(payload for kind, payload in events(spec['id']) if kind == 'STEP_FINISHED')
        self.assertIn('Pinned provider version is unavailable', finished['detail'])

    def test_auto_step_records_decision_and_falls_back(self):
        # Sample: AUTO step S-1 writes journal.py and location_search.py. Only SINGLE is registered. Check DECISION_RECORDED SUBAGENTS, STRATEGY_FALLBACK to SINGLE, STEP_STARTED decidedBy.
        goal = {'spec': {'writePaths': ['/w/journal.py', '/w/location_search.py']}, 'files': {'/w/journal.py': 'x = 1', '/w/location_search.py': 'y = 2'}}
        spec = {'format': 'durai-workflow/v1', 'id': 'wf-' + uid()[:8], 'steps': [{'id': 'S-1', 'goal': goal, 'strategy': 'AUTO',
                'gate': {'when': 'after', 'question': 'Accept S-1?', 'deadlineSeconds': 3600}}]}
        log = []
        builder = build_workflow(spec, registry(log), DATABASE)
        self.assertIn('__interrupt__', start_workflow(builder, spec['id'], DATABASE))
        approve(spec['id'], subject_of(spec['id']), 'Synthetic test only.', database=DATABASE)
        result = resume_workflow(build_workflow(spec, registry(log), DATABASE), spec['id'], DATABASE)
        self.assertEqual((log, result['status']), (['S-1'], 'SUCCEEDED'))
        seen = events(spec['id'])
        self.assertEqual(kinds(spec['id'])[1:4], ['DECISION_RECORDED', 'STRATEGY_FALLBACK', 'STEP_STARTED'])
        self.assertEqual(kinds(spec['id']).count('DECISION_RECORDED'), 1)
        decision = seen[1][1]
        self.assertEqual((decision['choice'], decision['stepId'], decision['features']['writePathCount']), ('SUBAGENTS', 'S-1', 2))
        self.assertEqual(seen[2][1], {'stepId': 'S-1', 'wanted': 'SUBAGENTS', 'used': 'SINGLE'})
        self.assertEqual((seen[3][1]['strategy'], seen[3][1]['decidedBy']), ('SINGLE', 'decision.execution-strategy:loose-modules'))

    def test_auto_step_runs_subagents(self):
        # Sample: AUTO step S-1 writes journal.py and location_search.py. SINGLE and SUBAGENTS are registered. Check no STRATEGY_FALLBACK, STEP_STARTED strategy SUBAGENTS, two subagents merged, acceptance once.
        from durai.strategy_subagents import SubagentsExecutor, register_subagents
        from test_strategy_subagents import Acceptance, Fake, goal as subagent_goal
        acceptance, child, log = Acceptance(), Fake(), []
        providers = registry(log)
        register_subagents(providers, SubagentsExecutor(child, acceptance=acceptance))
        spec = {'format': 'durai-workflow/v1', 'id': 'wf-' + uid()[:8], 'steps': [{'id': 'S-1', 'goal': subagent_goal(), 'strategy': 'AUTO'}]}
        result = start_workflow(build_workflow(spec, providers, DATABASE), spec['id'], DATABASE)
        seen = kinds(spec['id'])
        self.assertEqual((result['status'], log, acceptance.calls), ('SUCCEEDED', [], 1))
        self.assertNotIn('STRATEGY_FALLBACK', seen)
        started = next(payload for kind, payload in events(spec['id']) if kind == 'STEP_STARTED')
        self.assertEqual((started['strategy'], started['decidedBy']), ('SUBAGENTS', 'decision.execution-strategy:loose-modules'))
        self.assertEqual((seen.count('SUBAGENT_STARTED'), seen.count('SUBAGENT_FINISHED'), seen.count('MERGE_APPLIED'),
                          seen.count('ACCEPTANCE_FINISHED')), (2, 2, 2, 1))
        stamped = [payload for kind, payload in events(spec['id']) if kind == 'SUBAGENT_FINISHED']
        self.assertEqual(sorted((item['stepId'], item['subagentId'], item['state']) for item in stamped),
                         [('S-1', 'sub-1', 'SUCCEEDED'), ('S-1', 'sub-2', 'SUCCEEDED')])

    def test_executor_resume_decides_once(self):
        # Sample: AUTO step S-1 whose executor interrupts once. Resume. Check one DECISION_RECORDED and one STRATEGY_FALLBACK for S-1.
        calls = []

        def pausing(step, context):
            calls.append(step['id'])
            if len(calls) == 1:
                interrupt({'stepId': step['id']})
            return {'state': 'SUCCEEDED', 'detail': 'resumed'}
        providers = Providers()
        providers.register(CAPABILITY, 'SINGLE', pausing)
        goal = {'spec': {'writePaths': ['/w/journal.py', '/w/location_search.py']}, 'files': {}}
        spec = {'format': 'durai-workflow/v1', 'id': 'wf-' + uid()[:8], 'steps': [{'id': 'S-1', 'goal': goal, 'strategy': 'AUTO'}]}
        builder = build_workflow(spec, providers, DATABASE)
        self.assertIn('__interrupt__', start_workflow(builder, spec['id'], DATABASE))
        self.assertEqual(resume_workflow(builder, spec['id'], DATABASE)['status'], 'SUCCEEDED')
        self.assertEqual((kinds(spec['id']).count('DECISION_RECORDED'), kinds(spec['id']).count('STRATEGY_FALLBACK')), (1, 1))

    def test_gate_deadline_range_is_enforced(self):
        # Sample: Validate gate before S-2 with deadlineSeconds 0 and 604801. Check refusal. Use 604800. Check acceptance.
        for seconds in (0, 604801):
            with self.assertRaises(Conflict):
                validate(definition({'when': 'before', 'question': 'Release S-2?', 'deadlineSeconds': seconds}))
        validate(definition({'when': 'before', 'question': 'Release S-2?', 'deadlineSeconds': 604800}))

    def test_resume_after_restart(self):
        # Sample: Stop the process while W-1 waits at G-1. Rebuild the graph from the checkpoint. Approve. Check S-2 runs once.
        log, spec = [], definition({'when': 'before', 'question': 'Release S-2?', 'deadlineSeconds': 3600})
        waiting = start_workflow(build_workflow(spec, registry(log), DATABASE), spec['id'], DATABASE)
        self.assertIn('__interrupt__', waiting)
        self.assertEqual(log, ['S-1'])
        approve(spec['id'], subject_of(spec['id']), 'Synthetic test only.', database=DATABASE)
        rebuilt = build_workflow(spec, registry(log), DATABASE)
        result = resume_workflow(rebuilt, spec['id'], DATABASE)
        self.assertEqual((log, result['status']), (['S-1', 'S-2'], 'SUCCEEDED'))
        self.assertEqual(kinds(spec['id']).count('STEP_STARTED'), 2)

    def test_single_step_runs_dev_loop(self):
        # Sample: Workflow with one SINGLE step for the loop_spec shares task and scripted WRONG then FIX. Check STEP_FINISHED SUCCEEDED and the dev loop outcome PASSED.
        from durai.development_loop import build_loop
        from test_dev_loop import FIX, WRONG, LoopHarness, Sources, loop_spec, pins_for
        harness = LoopHarness([[WRONG], [FIX]])
        loop = build_loop(harness.ledger, harness.providers, harness.evaluator, harness.store, Sources(), harness.model,
                          harness.scratch / 'work')
        providers = Providers()
        providers.register(CAPABILITY, 'SINGLE', DevLoopExecutor(loop, DATABASE, pins_for))
        spec = {'format': 'durai-workflow/v1', 'id': 'wf-' + uid()[:8],
                'steps': [{'id': 'S-1', 'goal': {'spec': loop_spec()}, 'strategy': 'SINGLE'}]}
        result = start_workflow(build_workflow(spec, providers, DATABASE), spec['id'], DATABASE)
        finished = [payload for kind, payload in events(spec['id']) if kind == 'STEP_FINISHED']
        self.assertEqual([(item['state'], item['outcome']) for item in finished], [('SUCCEEDED', 'PASSED')])
        self.assertEqual(result['status'], 'SUCCEEDED')


class GateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply the core migrations and the LangGraph checkpoint tables to durai_test.
        prepare(DATABASE)
        prepare_checkpoints(DATABASE)

    def test_gate_waits_for_reviewer_approval(self):
        # Sample: Gate before S-2 for subject D-1. Write the approval as durai_reviewer. Check S-2 resumes.
        log, spec = [], definition({'when': 'before', 'question': 'Release S-2?', 'deadlineSeconds': 3600})
        builder = build_workflow(spec, registry(log), DATABASE)
        self.assertIn('__interrupt__', start_workflow(builder, spec['id'], DATABASE))
        subject = subject_of(spec['id'])
        self.assertEqual(gate_state(spec['id'], 'S-2', subject, DATABASE), 'WAITING')
        self.assertIn('__interrupt__', resume_workflow(builder, spec['id'], DATABASE))
        self.assertEqual(log, ['S-1'])
        approve(spec['id'], subject, 'Synthetic test only.', database=DATABASE)
        self.assertEqual(gate_state(spec['id'], 'S-2', subject, DATABASE), 'APPROVED')
        result = resume_workflow(builder, spec['id'], DATABASE)
        self.assertEqual((log, result['status']), (['S-1', 'S-2'], 'SUCCEEDED'))
        self.assertEqual(kinds(spec['id']).count('GATE_OPENED'), 1)
        self.assertIn('GATE_APPROVED', kinds(spec['id']))

    def test_approval_for_other_subject_is_ignored(self):
        # Sample: Gate before S-2 for subject D-1. Approve another subject D-2 of the same plan. Check S-2 still WAITING.
        log, spec = [], definition({'when': 'before', 'question': 'Release S-2?', 'deadlineSeconds': 3600})
        builder = build_workflow(spec, registry(log), DATABASE)
        start_workflow(builder, spec['id'], DATABASE)
        subject = subject_of(spec['id'])
        other = ContextStore(DATABASE).append(spec['id'], 'finding', {'finding': 'Other subject', 'severity': 'LOW', 'files': [],
                                                                    'proposedCorrection': 'None.'}, 'other-subject')
        approve(spec['id'], other['digest'], 'Synthetic test only.', database=DATABASE)
        self.assertEqual(gate_state(spec['id'], 'S-2', subject, DATABASE), 'WAITING')
        self.assertEqual(log, ['S-1'])

    def test_gate_timeout_cancels(self):
        # Sample: Gate deadline 1 second. Wait 2 seconds. Check S-2 CANCELLED.
        log, spec = [], definition({'when': 'before', 'question': 'Release S-2?', 'deadlineSeconds': 1})
        offset, base = [0.0], time.time()
        builder = build_workflow(spec, registry(log), DATABASE, clock=lambda: base + offset[0])
        self.assertIn('__interrupt__', start_workflow(builder, spec['id'], DATABASE))
        offset[0] = 2.0
        result = resume_workflow(builder, spec['id'], DATABASE)
        self.assertEqual((log, result['status'], result['steps']['S-2']), (['S-1'], 'CANCELLED', 'CANCELLED'))
        seen = kinds(spec['id'])
        self.assertIn('GATE_CANCELLED', seen)
        self.assertNotIn('GATE_APPROVED', seen)
        self.assertEqual(events(spec['id'])[-1], ('WORKFLOW_FINISHED', dict(events(spec['id'])[-1][1], state='CANCELLED')))

    def test_runtime_approval_refused(self):
        # Sample: Write the approval for D-1 as durai_runtime. Check refusal and S-2 still waiting.
        log, spec = [], definition({'when': 'before', 'question': 'Release S-2?', 'deadlineSeconds': 3600})
        builder = build_workflow(spec, registry(log), DATABASE)
        start_workflow(builder, spec['id'], DATABASE)
        subject = subject_of(spec['id'])
        with self.assertRaises(PermissionError):
            approve(spec['id'], subject, 'Forged by the agent.', database=DATABASE, role='durai_runtime')
        self.assertEqual(gate_state(spec['id'], 'S-2', subject, DATABASE), 'WAITING')
        self.assertIn('__interrupt__', resume_workflow(builder, spec['id'], DATABASE))
        self.assertEqual(log, ['S-1'])

    def test_after_gate_waits_after_step_finishes(self):
        # Sample: Gate after S-2 with deadline 1 hour. Run W-1. Check S-2 ran and the workflow waits.
        log, spec = [], definition({'when': 'after', 'question': 'Accept S-2?', 'deadlineSeconds': 3600})
        builder = build_workflow(spec, registry(log), DATABASE)
        self.assertIn('__interrupt__', start_workflow(builder, spec['id'], DATABASE))
        self.assertEqual(log, ['S-1', 'S-2'])
        approve(spec['id'], subject_of(spec['id']), 'Synthetic test only.', database=DATABASE)
        self.assertEqual(resume_workflow(builder, spec['id'], DATABASE)['status'], 'SUCCEEDED')

    def test_after_gate_cancel_keeps_step_state(self):
        # Sample: Step S-1 SUCCEEDED, after-gate G-1 passes its deadline. Check one STEP_FINISHED for S-1 with SUCCEEDED, GATE_CANCELLED for G-1, WORKFLOW_FINISHED CANCELLED.
        log, spec = [], definition({'when': 'after', 'question': 'Accept S-2?', 'deadlineSeconds': 1})
        offset, base = [0.0], time.time()
        builder = build_workflow(spec, registry(log), DATABASE, clock=lambda: base + offset[0])
        self.assertIn('__interrupt__', start_workflow(builder, spec['id'], DATABASE))
        offset[0] = 2.0
        result = resume_workflow(builder, spec['id'], DATABASE)
        self.assertEqual((result['status'], result['steps']['S-2']), ('CANCELLED', 'SUCCEEDED'))
        seen = events(spec['id'])
        self.assertEqual([p['state'] for k, p in seen if k == 'STEP_FINISHED' and p['stepId'] == 'S-2'], ['SUCCEEDED'])
        self.assertEqual([p['stepId'] for k, p in seen if k == 'GATE_CANCELLED'], ['S-2'])
        self.assertEqual((seen[-1][0], seen[-1][1]['state']), ('WORKFLOW_FINISHED', 'CANCELLED'))

    def test_sweep_cancels_overdue_gate(self):
        # Sample: Two workflows wait at gates; one deadline passed. Sweep. Check only that one is CANCELLED and a second sweep changes nothing.
        offset, base = [0.0], time.time()
        clock = lambda: base + offset[0]
        short = definition({'when': 'before', 'question': 'Release S-2?', 'deadlineSeconds': 1})
        long = definition({'when': 'before', 'question': 'Release S-2?', 'deadlineSeconds': 3600})
        builders = {}
        for spec in (short, long):
            builders[spec['id']] = build_workflow(spec, registry([]), DATABASE, clock=clock)
            self.assertIn('__interrupt__', start_workflow(builders[spec['id']], spec['id'], DATABASE))
        offset[0] = 2.0
        self.assertEqual(sweep_gates(builders.get, DATABASE, clock=clock), [short['id']])
        self.assertEqual(kinds(short['id'])[-1], 'WORKFLOW_FINISHED')
        self.assertEqual(events(short['id'])[-1][1]['state'], 'CANCELLED')
        self.assertEqual(kinds(long['id'])[-1], 'GATE_OPENED')
        before = len(events(short['id'])), len(events(long['id']))
        self.assertEqual(sweep_gates(builders.get, DATABASE, clock=clock), [])
        self.assertEqual((len(events(short['id'])), len(events(long['id']))), before)


if __name__ == '__main__':
    unittest.main()
