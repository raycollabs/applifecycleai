"""Sample: Open gate G-1 before step S-2 of workflow W-1. Write the approval as durai_reviewer. Check state APPROVED."""
import time
from .common import Conflict
from .context_store import ContextStore
from .coordination import Coordination
from .db import transaction
from . import execution_events

REVIEWER = 'durai_reviewer'
WAITING, APPROVED, CANCELLED = 'WAITING', 'APPROVED', 'CANCELLED'


def gate_name(step_id, when):
    # Sample: Name the before gate of S-2. Return S-2:before.
    return step_id + ':' + when


def emit(workflow_id, kind, payload, database):
    # Sample: Record GATE_OPENED for W-1 with subject D-1. Replay it from run W-1.
    Coordination(database).run(workflow_id)
    with transaction('durai_runtime', database) as db:
        execution_events.record(db, workflow_id, kind, payload)


def propose_subject(workflow_id, step_id, when, definition_digest, database='durai_local'):
    # Sample: Propose the before gate of S-2 in W-1. Return one PROPOSED decision record. Repeat it. Return the same digest.
    store = ContextStore(database)
    store.open_plan(workflow_id, workflow_id)
    value = {'decision': 'Gate ' + gate_name(step_id, when) + ' of workflow ' + workflow_id, 'alternatives': [],
             'evidence': [], 'authority': 'PROPOSED', 'definitionDigest': definition_digest}
    return store.append(workflow_id, 'decision', value, 'gate-subject:' + gate_name(step_id, when))


def find_gate(rows, step_id, when, subject_digest):
    # Sample: Read records of W-1. Return the gate progress record of S-2 before for subject D-1. Return None when absent.
    name = gate_name(step_id, when)
    for row in rows:
        value = row['value']
        if row['kind'] == 'progress' and value.get('gate') == name and value.get('subjectDigest') == subject_digest:
            return row
    return None


def open_gate(workflow_id, step_id, subject_digest, question, deadline, database='durai_local', when='before'):
    # Sample: Open the gate of S-2 for D-1 with deadline T+3600. Record GATE_OPENED once. Repeat. Return the same gate.
    store = ContextStore(database)
    if not isinstance(question, str) or not question.strip():
        raise Conflict('Gate requires a question')
    existing = find_gate(store.read(workflow_id), step_id, when, subject_digest)
    created = existing is None
    if created:
        existing = store.append(workflow_id, 'progress',
                                {'step': 'Gate ' + gate_name(step_id, when), 'result': WAITING, 'evidence': [],
                                 'gate': gate_name(step_id, when), 'subjectDigest': subject_digest, 'question': question,
                                 'deadline': '%.3f' % deadline}, 'gate-open:' + gate_name(step_id, when) + ':' + subject_digest)
        emit(workflow_id, 'GATE_OPENED', {'stepId': step_id, 'when': when, 'subjectDigest': subject_digest,
                                          'question': question, 'deadline': float(existing['value']['deadline'])}, database)
    value = existing['value']
    return {'workflowId': workflow_id, 'stepId': step_id, 'when': when, 'subjectDigest': subject_digest,
            'question': value['question'], 'deadline': float(value['deadline']), 'key': existing['key'], 'created': created}


def reviewer_decision(rows, subject_digest, deadline):
    # Sample: Read a durai_reviewer APPROVE for D-1 written before the deadline. Return the record. Read a runtime author. Ignore it.
    matches = [row for row in rows if row['kind'] == 'approval' and row['author'] == REVIEWER
               and row['value'].get('subjectDigest') == subject_digest and row['created_at'].timestamp() <= deadline]
    rejected = [row for row in matches if row['value'].get('decision') == 'REJECT']
    return (rejected or matches or [None])[0]


def gate_state(workflow_id, step_id, subject_digest, database='durai_local', when='before', now=None):
    # Sample: Read gate S-2 for D-1 with no approval and 10 seconds left. Return WAITING. Add the reviewer approval. Return APPROVED.
    rows = ContextStore(database).read(workflow_id)
    gate = find_gate(rows, step_id, when, subject_digest)
    if gate is None:
        raise KeyError('Gate is not open: ' + gate_name(step_id, when))
    deadline = float(gate['value']['deadline'])
    decision = reviewer_decision(rows, subject_digest, deadline)
    if decision is not None:
        return APPROVED if decision['value']['decision'] == 'APPROVE' else CANCELLED
    return CANCELLED if (time.time() if now is None else now) >= deadline else WAITING


def approve(workflow_id, subject_digest, scope, decision='APPROVE', database='durai_local', role=REVIEWER):
    # Sample: Approve D-1 of W-1 as durai_reviewer. Return an approval record. Approve as durai_runtime. Refuse with PermissionError.
    return ContextStore(database, role).append(workflow_id, 'approval',
        {'subjectDigest': subject_digest, 'decision': decision, 'scope': scope}, 'approval:' + decision + ':' + subject_digest)
