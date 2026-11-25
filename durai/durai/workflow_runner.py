"""Sample: Run workflow W-1 with steps S-1 and S-2. Wait at the gate before S-2. Resume after approval. Check both steps SUCCEEDED."""
import time
from typing import TypedDict
from langgraph.errors import GraphBubbleUp
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command, interrupt
from .common import Conflict
from .execution_events import replay
from .development_loop import development_graph
from .workflow_definition import digest as definition_digest, validate
from . import workflow_gates

CAPABILITY = 'workflow-step'
POINT = 'decision.execution-strategy'
TERMINAL = ('SUCCEEDED', 'FAILED', 'CANCELLED', 'LOST')
workflow_graph = development_graph


class WorkflowState(TypedDict, total=False):
    workflow_id: str
    status: str
    steps: dict
    details: dict


def build_workflow(definition, providers, database='durai_local', clock=time.time, chooser=None):
    # Sample: Build the graph of W-1 with steps S-1 and S-2 and executors from registry P-1. Return a graph builder.
    definition = validate(definition)
    checksum = definition_digest(definition)
    workflow_id = definition['id']

    def emit(kind, payload):
        # Sample: Record STEP_STARTED for S-1 of W-1 in the event log of run W-1.
        workflow_gates.emit(workflow_id, kind, payload, database)

    def start(state):
        # Sample: Start W-1. Record WORKFLOW_STARTED with 2 steps and the definition digest. Return status RUNNING.
        emit('WORKFLOW_STARTED', {'definitionDigest': checksum, 'steps': [step['id'] for step in definition['steps']]})
        return {'workflow_id': workflow_id, 'status': 'RUNNING', 'steps': {}, 'details': {}}

    def finish_step(state, step, result, extra=None):
        # Sample: Finish S-2 as CANCELLED. Record STEP_FINISHED. Return status CANCELLED and the step state.
        outcome = result['state']
        emit('STEP_FINISHED', dict({'stepId': step['id'], 'state': outcome, 'detail': result.get('detail'),
                                    'outcome': result.get('outcome')}, **(extra or {})))
        update = {'steps': dict(state['steps'], **{step['id']: outcome}),
                  'details': dict(state['details'], **{step['id']: result.get('detail')})}
        if outcome != 'SUCCEEDED':
            update['status'] = outcome
        return update

    def gate_node(step, when):
        def node(state):
            # Sample: Open the before gate of S-2. Wait for the reviewer. Record GATE_APPROVED. Cancel S-2 at the deadline.
            if state['status'] != 'RUNNING' or step['gate']['when'] != when:
                return {}
            gate = step['gate']
            subject = workflow_gates.propose_subject(workflow_id, step['id'], when, checksum, database)['digest']
            opened = workflow_gates.open_gate(workflow_id, step['id'], subject, gate['question'],
                                              clock() + gate['deadlineSeconds'], database, when)
            check = lambda: workflow_gates.gate_state(workflow_id, step['id'], subject, database, when, clock())
            verdict = check()
            while verdict == workflow_gates.WAITING:
                interrupt({'workflowId': workflow_id, 'stepId': step['id'], 'when': when, 'subjectDigest': subject,
                           'question': gate['question'], 'deadline': opened['deadline']})
                verdict = check()
            facts = {'stepId': step['id'], 'when': when, 'subjectDigest': subject}
            if verdict == workflow_gates.APPROVED:
                emit('GATE_APPROVED', facts)
                return {}
            emit('GATE_CANCELLED', dict(facts, reason='Deadline passed or reviewer rejected without approval'))
            if when == 'after':
                return {'status': 'CANCELLED'}
            return finish_step(state, step, {'state': 'CANCELLED', 'detail': 'Gate ' + when + ' cancelled'}, {'gate': when})
        return node

    def decide(step, goal, recorded):
        # Sample: Read AUTO step S-2 with 2 loose write paths. Record DECISION_RECORDED once. Resume S-2. Reuse choice SUBAGENTS without a second record.
        for kind, payload in recorded:
            if kind == 'DECISION_RECORDED' and payload.get('stepId') == step['id']:
                return payload['choice'], payload['reasons'][0]
        spec = goal.get('spec') if isinstance(goal, dict) else None
        task = spec if isinstance(spec, dict) else {}
        files = {path: text for path, text in (goal.get('files') or {}).items()} if isinstance(goal, dict) else {}
        budget = goal.get('budget') if isinstance(goal, dict) else None
        stamp = lambda kind, payload: emit(kind, dict(payload, stepId=step['id']))
        if chooser is not None:
            result = chooser(task, step, files, budget, stamp)
        else:
            from .strategy_select import choose
            result = choose(task, step, files, budget, stamp, database)
        return result['choice'], result['reasons'][0]

    def run_node(step):
        def node(state):
            # Sample: Run S-1 with the SINGLE executor. Record STEP_STARTED then STEP_FINISHED SUCCEEDED. Skip a finished step. Decide AUTO once.
            if state['status'] != 'RUNNING' or step['id'] in state['steps']:
                return {}
            strategy, decided_by = step['strategy'], 'workflow-definition'
            if strategy == 'AUTO':
                recorded = [(row['kind'], row['payload']) for row in replay(workflow_id, database=database)
                            if row['kind'] in ('DECISION_RECORDED', 'STRATEGY_FALLBACK')]
                wanted, reason = decide(step, step['goal'], recorded)
                strategy, decided_by = wanted, POINT + ':' + reason
                if not providers.available(CAPABILITY, wanted):
                    strategy = 'SINGLE'
                    if not any(kind == 'STRATEGY_FALLBACK' and payload.get('stepId') == step['id'] for kind, payload in recorded):
                        emit('STRATEGY_FALLBACK', {'stepId': step['id'], 'wanted': wanted, 'used': 'SINGLE'})
            emit('STEP_STARTED', {'stepId': step['id'], 'strategy': strategy, 'decidedBy': decided_by})
            context = {'workflowId': workflow_id, 'stepId': step['id'], 'goal': step['goal'], 'steps': dict(state['steps']), 'emit': emit}
            try:
                with providers.resolve(CAPABILITY, strategy) as executor:
                    result = executor(step, context)
            except GraphBubbleUp:
                raise
            except Exception as error:
                result = {'state': 'FAILED', 'detail': type(error).__name__ + ': ' + str(error)}
            if not isinstance(result, dict) or result.get('state') not in TERMINAL:
                result = {'state': 'FAILED', 'detail': 'Executor returned no terminal state'}
            return finish_step(state, step, result)
        return node

    def finish(state):
        # Sample: Finish W-1 with S-1 and S-2 SUCCEEDED. Record WORKFLOW_FINISHED with status SUCCEEDED.
        status = 'SUCCEEDED' if state['status'] == 'RUNNING' else state['status']
        emit('WORKFLOW_FINISHED', {'state': status, 'steps': state['steps'], 'definitionDigest': checksum})
        return {'status': status}

    def route(target):
        def pick(state):
            # Sample: Read status RUNNING. Return the next node. Read CANCELLED. Return finish.
            return target if state['status'] == 'RUNNING' else 'finish'
        return pick

    builder = StateGraph(WorkflowState)
    builder.add_node('start', start)
    builder.add_node('finish', finish)
    names = []
    for index, step in enumerate(definition['steps']):
        for label, action in (('before', gate_node(step, 'before')), ('run', run_node(step)), ('after', gate_node(step, 'after'))):
            if label == 'run' or step['gate']['when'] == label:
                builder.add_node(label + '_' + str(index), action)
                names.append(label + '_' + str(index))
    builder.add_edge(START, 'start')
    chain = ['start'] + names + ['finish']
    for current, following in zip(chain[:-1], chain[1:]):
        if following == 'finish':
            builder.add_edge(current, 'finish')
        else:
            builder.add_conditional_edges(current, route(following), [following, 'finish'])
    builder.add_edge('finish', END)
    return builder


def start_workflow(builder, workflow_id, database='durai_local'):
    # Sample: Start W-1 on the PostgreSQL checkpointer. Return the state with status RUNNING, or an interrupt at a gate.
    with workflow_graph(builder, database) as graph:
        return graph.invoke({'workflow_id': workflow_id}, {'configurable': {'thread_id': workflow_id}})


def resume_workflow(builder, workflow_id, database='durai_local'):
    # Sample: Rebuild W-1 after a restart. Resume from its checkpoint. Run S-2 once after the approval.
    with workflow_graph(builder, database) as graph:
        return graph.invoke(Command(resume={'poll': True}), {'configurable': {'thread_id': workflow_id}})


def sweep_gates(builder_for, database='durai_local', now=None, clock=time.time):
    # Sample: W-1 waits at a gate past its deadline and W-2 waits inside its deadline. Sweep. Resume W-1 only. Sweep again. Resume none.
    from .db import transaction
    moment = clock() if now is None else now
    with transaction('durai_runtime', database) as db:
        rows = db.execute("SELECT DISTINCT ON (run_id) run_id,kind,payload FROM durai_coordination.event "
                          "WHERE run_id IN (SELECT run_id FROM durai_coordination.event WHERE kind='GATE_OPENED') "
                          "ORDER BY run_id, sequence DESC").fetchall()
    resumed = []
    for row in rows:
        if row['kind'] != 'GATE_OPENED':
            continue
        payload = row['payload']
        state = workflow_gates.gate_state(row['run_id'], payload['stepId'], payload['subjectDigest'], database,
                                          payload['when'], moment)
        builder = builder_for(row['run_id'])
        if state != workflow_gates.WAITING and builder is not None:
            resume_workflow(builder, row['run_id'], database)
            resumed.append(row['run_id'])
    return resumed


class DevLoopExecutor:
    def __init__(self, loop_builder, database, current_for, run_prefix='', external=None, leases=None):
        # Sample: Wrap the development loop builder L-1 with pins from spec T-1. With external runs X-1 and leases F-1, a team member run passes its shared workspace and lease to the worker. Keep no other run state.
        self.loop_builder, self.database, self.current_for, self.run_prefix = loop_builder, database, current_for, run_prefix
        self.external, self.leases = external, leases

    def __call__(self, step, context):
        # Sample: Run the shares task. Loop outcome PASSED. Return SUCCEEDED. Loop outcome STALE. Return FAILED.
        goal = context['goal']
        spec = goal.get('spec') if isinstance(goal, dict) else None
        if not isinstance(spec, dict):
            raise Conflict('SINGLE step goal requires a task spec')
        run_id = self.run_prefix + context['workflowId'] + '.' + context['stepId']
        state = {'run_id': run_id, 'spec': spec, 'current': self.current_for(spec)}
        shared = bool(context.get('worktree') and context.get('memberId') and self.external is not None and self.leases is not None)
        if shared:
            self.external.put(run_id, context['workspace'], (self.leases, context['worktree'], context['memberId']))
            state['external'] = True
        try:
            with development_graph(self.loop_builder, self.database) as graph:
                result = graph.invoke(state, {'configurable': {'thread_id': run_id}})
        finally:
            if shared:
                self.external.drop(run_id)
        outcome = result.get('outcome')
        return {'state': 'SUCCEEDED' if outcome == 'PASSED' else 'FAILED', 'outcome': outcome,
                'detail': result.get('detail') or outcome, 'package': result.get('package')}


def recover_workflow(builder, workflow_id, database='durai_local'):
    # Sample: Process of W-1 died inside step S-1. Rebuild W-1 and run from its last checkpoint. S-1 runs again from its recorded events.
    with workflow_graph(builder, database) as graph:
        return graph.invoke(None, {'configurable': {'thread_id': workflow_id}})
