"""Resumable trading factory graph. Review is an explicit durable interrupt."""
from contextlib import contextmanager
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from langgraph.checkpoint.postgres import PostgresSaver
from .common import Conflict
from .coordination import Coordination
from .db import connect, transaction
from .trading import discover


class FactoryState(TypedDict, total=False):
    run_id: str
    target_id: str
    finding_id: str
    attempt: int
    version_id: str
    verdict: dict
    approval_id: str
    result: dict
    state: str
    use_model: bool
    binding_id: str | None


@contextmanager
def factory_graph(registry, evaluator, controller, proposer, max_attempts=2):
    database = registry.database
    coordinator = Coordination(database)

    def discovery(state):
        run_id, finding_id = discover(database, state['run_id'])
        return {'finding_id': finding_id, 'attempt': 0, 'state': 'RUNNING'}

    def propose(state):
        attempt = state['attempt'] + 1
        version_id = proposer(dict(state, attempt=attempt))
        registry.get(version_id)  # registration must precede evaluation
        return {'version_id': version_id, 'attempt': attempt}

    def evaluate(state):
        verdict = evaluator.evaluate(state['version_id'])
        coordinator.checkpoint(state['run_id'], {'findingId': state['finding_id'],
            'stage': 'EVALUATED', 'candidateVersion': state['version_id'],
            'candidateDigest': verdict['digest'], 'attempt': state['attempt'],
            'verdict': verdict['verdict']})
        return {'verdict': verdict}

    def route(state):
        if state['verdict']['verdict'] == 'PASS':
            return 'review'
        return 'propose' if state['attempt'] < max_attempts else 'stop'

    def review(state):
        coordinator.checkpoint(state['run_id'], {'findingId': state['finding_id'],
            'stage': 'AWAITING_APPROVAL', 'candidateVersion': state['version_id'],
            'candidateDigest': state['verdict']['digest']}, 'WAITING_FOR_REVIEW')
        decision = interrupt({'versionId': state['version_id'], 'digest': state['verdict']['digest'],
                              'findingId': state['finding_id'], 'targetId': state['target_id']})
        if not isinstance(decision, dict) or not decision.get('approvalId'):
            raise Conflict('A recorded reviewer approval is required to resume')
        return {'approval_id': decision['approvalId']}

    def activate(state):
        with transaction('durai_release', database) as db:
            approval = db.execute('SELECT version_id,digest,target FROM durai_governance.approval WHERE id=%s', (state['approval_id'],)).fetchone()
        if not approval or (approval['version_id'], approval['digest'], approval['target']) != (state['version_id'], state['verdict']['digest'], state['target_id']):
            raise Conflict('Factory review must approve its exact candidate and target')
        result = controller.activate(state['approval_id'], state['finding_id'], state['run_id'])
        return {'result': result, 'state': 'SUCCEEDED'}

    def stop(state):
        coordinator.checkpoint(state['run_id'], {'findingId': state['finding_id'],
            'stage': 'BUDGET_EXHAUSTED', 'attempts': state['attempt']}, 'FAILED')
        return {'state': 'FAILED'}

    builder = StateGraph(FactoryState)
    for name, action in [('discover', discovery), ('propose', propose), ('evaluate', evaluate),
                         ('review', review), ('activate', activate), ('stop', stop)]:
        builder.add_node(name, action)
    builder.add_edge(START, 'discover')
    builder.add_edge('discover', 'propose')
    builder.add_edge('propose', 'evaluate')
    builder.add_conditional_edges('evaluate', route, ['review', 'propose', 'stop'])
    builder.add_edge('review', 'activate')
    builder.add_edge('activate', END)
    builder.add_edge('stop', END)
    with connect('durai_runtime', database, autocommit=True) as connection:
        connection.execute('SET search_path=durai_checkpoints')
        yield builder.compile(checkpointer=PostgresSaver(connection))


def fixture_proposer(state):
    return 'quantity-only-v2' if state['attempt'] == 1 else 'repaired-v3'
