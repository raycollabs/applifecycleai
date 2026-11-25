"""Sample: Read package P-1 with a candidate assertion failure and 1 attempt left. Return RETRY_CANDIDATE with rule and feature digests."""
from copy import deepcopy
from .decision_points import DecisionPoint

POINT = 'failure-triage'
CHOICES = ('SUBMIT_FOR_REVIEW', 'RETRY_CANDIDATE', 'REPEAT_EVALUATION', 'STOP_STALE', 'STOP_FIXTURE', 'STOP_PROVIDER',
           'STOP_STALLED', 'STOP_BUDGET', 'STOP_INFRASTRUCTURE', 'STOP_NEEDS_REVIEW')
ORIGINS = ('candidate', 'fixture', 'evaluator', 'provider', 'guard', 'budget', 'stale', 'unknown')
REASONS = ('PASSED', 'CANDIDATE_ASSERTION', 'CANDIDATE_SETUP_SQL', 'CANDIDATE_COMPILE', 'CANDIDATE_COMMENT', 'CANDIDATE_SCOPE',
           'CANDIDATE_TIMEOUT', 'CANDIDATE_RESOURCE_LIMIT', 'FIXTURE_SETUP', 'FIXTURE_SEED', 'EVALUATOR_START',
           'EVALUATOR_DATABASE', 'PIN_CHANGED', 'PROVIDER_FAULT', 'EDIT_GUARD_STALLED', 'BUDGET_EXHAUSTED',
           'OWNERSHIP_LOST', 'CANDIDATE_WORKER_OUTPUT', 'UNCLASSIFIED')
PHASES = {'database-setup': 'setup', 'python-compile': 'compile', 'python-unittest': 'test', 'comment-policy': 'comment',
          'scope-guard': 'scope'}
COMMAND_ORIGIN = {'scope-guard': ('candidate', 'CANDIDATE_SCOPE'), 'comment-policy': ('candidate', 'CANDIDATE_COMMENT')}
CONTROL_FAULTS = {'PROVIDER': ('worker', 'provider', 'PROVIDER_FAULT'), 'STALLED': ('worker', 'guard', 'EDIT_GUARD_STALLED'),
                  'BUDGET': ('worker', 'budget', 'BUDGET_EXHAUSTED'), 'OWNERSHIP': ('worker', 'budget', 'OWNERSHIP_LOST'),
                  'STALE': ('readiness', 'stale', 'PIN_CHANGED'),
                  'CANDIDATE': ('worker', 'candidate', 'CANDIDATE_WORKER_OUTPUT')}
RANK = ('stale', 'fixture', 'evaluator', 'unknown', 'candidate')
EVALUATOR_RETRY_LIMIT = 1
MINIMUM_RETRY_CALLS = 5
RESUME_REASONS = ['CANDIDATE_ASSERTION', 'CANDIDATE_COMMENT']
MINIMUM_SECONDS = 30
TRIAGE_RULES = [
    {'id': 'passed', 'when': {'verdict': 'PASS'}, 'choice': 'SUBMIT_FOR_REVIEW'},
    {'id': 'stale-pin', 'when': {'origin': 'stale'}, 'choice': 'STOP_STALE'},
    {'id': 'trusted-fixture', 'when': {'origin': 'fixture'}, 'choice': 'STOP_FIXTURE'},
    {'id': 'provider-fault', 'when': {'origin': 'provider'}, 'choice': 'STOP_PROVIDER'},
    {'id': 'stalled-worker', 'when': {'origin': 'guard'}, 'choice': 'STOP_STALLED'},
    {'id': 'budget-stop', 'when': {'origin': 'budget'}, 'choice': 'STOP_BUDGET'},
    {'id': 'infrastructure-repeat', 'when': {'origin': 'evaluator', 'evaluatorRetryAvailable': True, 'timeLeft': True},
     'choice': 'REPEAT_EVALUATION'},
    {'id': 'infrastructure-stop', 'when': {'origin': 'evaluator'}, 'choice': 'STOP_INFRASTRUCTURE'},
    {'id': 'candidate-retry', 'when': {'origin': 'candidate', 'attemptsLeft': True, 'callsLeft': True, 'timeLeft': True},
     'choice': 'RETRY_CANDIDATE'},
    {'id': 'candidate-exhausted', 'when': {'origin': 'candidate'}, 'choice': 'STOP_BUDGET'},
    {'id': 'needs-review', 'choice': 'STOP_NEEDS_REVIEW'},
]
TRIAGE_RULES_V11 = [dict(rule, when=dict(origin='candidate', attemptsLeft=True, callsForRetry=True, timeLeft=True))
                    if rule['id'] == 'candidate-retry' else dict(rule) for rule in TRIAGE_RULES]
TRIAGE_RULES_V14 = [dict(rule) for item in TRIAGE_RULES_V11 for rule in (
    [{'id': 'stalled-retry', 'when': {'origin': 'guard', 'attemptsLeft': True, 'callsForRetry': True, 'timeLeft': True},
      'choice': 'RETRY_CANDIDATE'}, item] if item['id'] == 'stalled-worker' else [item])]


def fault(origin, reason):
    # Sample: Make a fault for candidate SQL. Return origin candidate and reason CANDIDATE_SETUP_SQL.
    if origin not in ORIGINS or reason not in REASONS:
        raise ValueError('Unknown fault origin or reason')
    return {'origin': origin, 'reasonCode': reason}


def classify(facts):
    # Sample: Read verdict FAIL with setup fault fixture and unittest fault candidate. Return phase setup, origin fixture, FIXTURE_SETUP.
    if facts.get('controlFault') is not None:
        phase, origin, reason = CONTROL_FAULTS.get(facts['controlFault'], ('unknown', 'unknown', 'UNCLASSIFIED'))
        return {'phase': phase, 'origin': origin, 'reasonCode': reason}
    verdict = facts.get('verdict')
    if not isinstance(verdict, dict) or verdict.get('verdict') not in ('PASS', 'FAIL'):
        return {'phase': 'evaluation', 'origin': 'unknown', 'reasonCode': 'UNCLASSIFIED'}
    if verdict['verdict'] == 'PASS':
        return {'phase': 'evaluation', 'origin': 'candidate', 'reasonCode': 'PASSED'}
    found = []
    for result in verdict.get('results', []):
        if result.get('state') != 'FAIL':
            continue
        phase = PHASES.get(result.get('command'), 'unknown')
        trusted = result.get('fault')
        if isinstance(trusted, dict) and trusted.get('origin') in ORIGINS and trusted.get('reasonCode') in REASONS:
            found.append({'phase': phase, 'origin': trusted['origin'], 'reasonCode': trusted['reasonCode']})
        elif result.get('command') in COMMAND_ORIGIN:
            origin, reason = COMMAND_ORIGIN[result['command']]
            found.append({'phase': phase, 'origin': origin, 'reasonCode': reason})
        else:
            found.append({'phase': phase, 'origin': 'unknown', 'reasonCode': 'UNCLASSIFIED'})
    if not found:
        return {'phase': 'evaluation', 'origin': 'unknown', 'reasonCode': 'UNCLASSIFIED'}
    return min(found, key=lambda item: RANK.index(item['origin']))


def triage_procedure(task, database, role):
    # Sample: Resolve a v10 binding. Return its pinned rules and retry limit 1. Resolve a v9 binding. Return the local rules.
    from .decision_points import pinned_procedure
    body = pinned_procedure(task, database, role, 'decision.failure-triage', 10)
    if body is None:
        return {'rules': deepcopy(TRIAGE_RULES), 'evaluatorRetryLimit': EVALUATOR_RETRY_LIMIT,
                'minimumRetryCalls': MINIMUM_RETRY_CALLS, 'resumeReasons': [], 'feedbackGoalNames': False, 'source': 'local'}
    return {'rules': body['rules'], 'evaluatorRetryLimit': body['evaluatorRetryLimit'],
            'minimumRetryCalls': body.get('minimumRetryCalls', MINIMUM_RETRY_CALLS),
            'resumeReasons': list(body.get('resumeReasons', [])),
            'feedbackGoalNames': body.get('feedbackGoalNames') is True, 'source': 'pinned'}


def resume_package(decision, procedure, package):
    # Sample: Read RETRY_CANDIDATE for CANDIDATE_ASSERTION, resume reasons [CANDIDATE_ASSERTION], and package P-1. Return P-1. Read CANDIDATE_SETUP_SQL. Return None.
    if decision.get('choice') == 'RETRY_CANDIDATE' and package and decision.get('reasonCode') in procedure.get('resumeReasons', []):
        return dict(package)
    return None


def features(facts, retry_limit=EVALUATOR_RETRY_LIMIT, retry_calls=MINIMUM_RETRY_CALLS):
    # Sample: Read FAIL from candidate code, 1 attempt left, 4 calls left, 300 s left, 0 evaluator retries. Return booleans and origin.
    found = classify(facts)
    seconds = facts.get('remainingSeconds')
    retries = facts.get('evaluatorRetries')
    verdict = facts.get('verdict')
    return {'verdict': verdict['verdict'] if isinstance(verdict, dict) and facts.get('controlFault') is None and
                       verdict.get('verdict') in ('PASS', 'FAIL') else 'NONE',
            'phase': found['phase'], 'origin': found['origin'], 'reasonCode': found['reasonCode'],
            'attemptsLeft': isinstance(facts.get('remainingAttempts'), int) and facts['remainingAttempts'] > 0,
            'callsLeft': isinstance(facts.get('remainingCalls'), int) and facts['remainingCalls'] > 0,
            'callsForRetry': isinstance(facts.get('remainingCalls'), int) and not isinstance(facts['remainingCalls'], bool)
                             and facts['remainingCalls'] >= retry_calls,
            'timeLeft': isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and seconds >= MINIMUM_SECONDS,
            'evaluatorRetryAvailable': isinstance(retries, int) and not isinstance(retries, bool) and retries < retry_limit,
            'packageDigest': facts.get('packageDigest'), 'taskDigest': facts.get('taskDigest')}


def control_fault(error):
    # Sample: Read WorkerStalled with text "provider error". Return STALLED. Read a plain Conflict. Return None.
    from .common import Conflict
    state = getattr(type(error), 'state', None) if isinstance(error, Conflict) else None
    return state if state in CONTROL_FAULTS else None


def check_rules(rules):
    # Sample: Load a rule that selects APPROVE. Raise ValueError. Load TRIAGE_RULES. Return a copy.
    for rule in rules:
        if rule.get('choice') not in CHOICES:
            raise ValueError('Failure triage rule selects a forbidden action: ' + str(rule.get('choice')))
    if rules and rules[-1].get('choice') != 'STOP_NEEDS_REVIEW':
        raise ValueError('Failure triage default must stop for review')
    return deepcopy(rules)


class FailureTriage:
    def __init__(self, rules, emit, retry_limit=EVALUATOR_RETRY_LIMIT, retry_calls=MINIMUM_RETRY_CALLS):
        # Sample: Load TRIAGE_RULES_V11 with event sink E-1, retry limit 1, and 5 calls for a retry. Keep the rule digest of the pinned rules.
        if not isinstance(retry_limit, int) or isinstance(retry_limit, bool) or not 0 <= retry_limit <= 1:
            raise ValueError('Evaluator retry limit must be 0 or 1')
        self.point = DecisionPoint(POINT, check_rules(rules), emit)
        if not isinstance(retry_calls, int) or isinstance(retry_calls, bool) or retry_calls < 1:
            raise ValueError('Minimum retry calls must be a positive integer')
        self.rule_digest, self.retry_limit, self.retry_calls = self.point.rule_digest, retry_limit, retry_calls

    def decide(self, facts):
        # Sample: Read a trusted fixture fault for package P-1. Return STOP_FIXTURE with reason trusted-fixture.
        found = features(facts, self.retry_limit, self.retry_calls)
        result = self.point.decide(found)
        if result['choice'] not in CHOICES:
            raise ValueError('Failure triage selected a forbidden action')
        return dict(result, phase=found['phase'], origin=found['origin'], reasonCode=found['reasonCode'])
