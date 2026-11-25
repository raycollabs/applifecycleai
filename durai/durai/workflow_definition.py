"""Sample: Validate workflow W-1 with steps S-1 and S-2. Return one normalized definition and one stable digest."""
import re
from .common import Conflict, digest as content_digest

FORMAT = 'durai-workflow/v1'
STRATEGIES = ('SINGLE', 'AUTO', 'SUBAGENTS', 'TEAM')
RESERVED = ()
GATE_WHEN = ('before', 'after', 'none')
MAX_DEADLINE_SECONDS = 604800
IDENTITY = re.compile(r'[A-Za-z0-9_.-]{1,160}')


def identity(value, label):
    # Sample: Read id W-1. Return W-1. Read id "a b". Refuse it with the label.
    if not isinstance(value, str) or not IDENTITY.fullmatch(value):
        raise Conflict(label + ' requires 1 to 160 letters, digits, dot, dash, or underscore')
    return value


def gate_of(value, step_id):
    # Sample: Read gate before with question Q-1 and deadline 3600. Return it. Read no gate. Return when none.
    if value is None:
        return {'when': 'none'}
    if not isinstance(value, dict) or set(value) - {'when', 'question', 'deadlineSeconds'}:
        raise Conflict('Step ' + step_id + ' gate has unknown fields')
    when = value.get('when', 'none')
    if when not in GATE_WHEN:
        raise Conflict('Step ' + step_id + ' gate when must be before, after, or none')
    if when == 'none':
        return {'when': 'none'}
    question, seconds = value.get('question'), value.get('deadlineSeconds')
    if not isinstance(question, str) or not question.strip():
        raise Conflict('Step ' + step_id + ' gate requires a question')
    if type(seconds) is not int or not 1 <= seconds <= MAX_DEADLINE_SECONDS:
        raise Conflict('Step ' + step_id + ' gate deadlineSeconds must be 1 to 604800')
    return {'when': when, 'question': question, 'deadlineSeconds': seconds}


def strategy_of(value, step_id):
    # Sample: Read SINGLE. Return SINGLE. Read AUTO, SUBAGENTS, or TEAM. Return it. Read X. Refuse it as unknown.
    if value not in STRATEGIES:
        raise Conflict('Step ' + step_id + ' strategy must be SINGLE, AUTO, SUBAGENTS, or TEAM')
    return value


def step_of(value, seen):
    # Sample: Read step S-1 with goal G-1 and no gate. Return it with strategy SINGLE and gate none.
    if not isinstance(value, dict) or set(value) - {'id', 'goal', 'strategy', 'gate'}:
        raise Conflict('Step has unknown fields')
    step_id = identity(value.get('id'), 'Step id')
    if step_id in seen:
        raise Conflict('Step id repeats: ' + step_id)
    goal = value.get('goal')
    if not (isinstance(goal, str) and goal.strip()) and not (isinstance(goal, dict) and goal):
        raise Conflict('Step ' + step_id + ' requires a goal')
    return {'id': step_id, 'goal': goal, 'strategy': strategy_of(value.get('strategy', 'SINGLE'), step_id),
            'gate': gate_of(value.get('gate'), step_id)}


def validate(definition):
    # Sample: Validate W-1 with S-1 and S-2. Return it. Validate a step with strategy AUTO. Return it. Validate a step with strategy TEAM. Return it. Validate strategy X. Refuse it.
    if not isinstance(definition, dict) or set(definition) - {'format', 'id', 'steps'}:
        raise Conflict('Workflow definition has unknown fields')
    if definition.get('format') != FORMAT:
        raise Conflict('Workflow format must be ' + FORMAT)
    steps, seen = definition.get('steps'), set()
    if not isinstance(steps, list) or not steps:
        raise Conflict('Workflow requires at least one step')
    normalized = []
    for item in steps:
        step = step_of(item, seen)
        seen.add(step['id'])
        normalized.append(step)
    return {'format': FORMAT, 'id': identity(definition.get('id'), 'Workflow id'), 'steps': normalized}


def digest(definition):
    # Sample: Digest W-1 with the gate omitted and with gate none. Return the same digest both times.
    return content_digest(validate(definition))
