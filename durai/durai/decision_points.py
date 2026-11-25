"""Sample: Match 200 shares to KEEP. Record one decision with stable rule and feature digests."""
from copy import deepcopy
from .common import digest

EDIT_RULES = [
    {'id': 'third-refusal', 'when': {'hasFindings': True, 'repeatLimitReached': True}, 'choice': 'STALLED'},
    {'id': 'invalid-proposal', 'when': {'hasFindings': True}, 'choice': 'REFUSED'},
    {'id': 'valid-proposal', 'choice': 'APPLIED'},
]


class DecisionPoint:
    def __init__(self, point, rules, emit):
        # Sample: Load R-1 and one final default. Refuse duplicate rule IDs or an earlier default.
        if not isinstance(point, str) or not point or not isinstance(rules, list) or not rules:
            raise ValueError('A point and ordered rules are required')
        ids = set()
        for index, rule in enumerate(rules):
            if not isinstance(rule, dict) or not isinstance(rule.get('id'), str) or not rule['id'] or rule['id'] in ids:
                raise ValueError('Each rule requires a unique ID')
            ids.add(rule['id'])
            if not isinstance(rule.get('choice'), str) or not rule['choice']:
                raise ValueError('Each rule requires a choice')
            if ('when' not in rule) != (index == len(rules) - 1) or ('when' in rule and not isinstance(rule['when'], dict)):
                raise ValueError('One final default rule is required')
        self.point, self.rules, self.emit = point, deepcopy(rules), emit
        self.rule_digest = digest(self.rules)

    def decide(self, features):
        # Sample: Match shares 200 to R-1. Emit DECISION_RECORDED and return KEEP without changing the input.
        snapshot = deepcopy(features)
        for rule in self.rules:
            if 'when' not in rule or all(key in snapshot and type(snapshot[key]) is type(value) and snapshot[key] == value
                                         for key, value in rule['when'].items()):
                result = {'point': self.point, 'choice': rule['choice'], 'reasons': [rule['id']],
                          'ruleDigest': self.rule_digest, 'featuresDigest': digest(snapshot)}
                self.emit('DECISION_RECORDED', deepcopy(dict(result, features=snapshot)))
                return result


def pinned_procedure(task, database, role, record_id, since):
    # Sample: Resolve a v10 task binding. Return the pinned decision.failure-triage body. Return None for a v7 binding.
    if not database or not task.get('binding', {}).get('bindingId'):
        return None
    from .packs import PackStore
    from .worker_contract import resolve_contract
    from .common import Conflict
    binding = task['binding']['bindingId']
    store = PackStore(database, role)
    for reference in store.resolve(binding)['document']['packs']:
        document = store.get(reference)
        if document['kind'] == 'GENERIC':
            for record in document['records']:
                if record['id'] == record_id and record['type'] == 'PROCEDURE':
                    if resolve_contract(binding, database=database)['digest'] != task['pins']['contractDigest']:
                        raise Conflict('Decision procedure contract differs from the task pin')
                    return deepcopy(record['body'])
            if int(str(document['version']).split('-')[0]) >= since:
                raise Conflict('Pinned generic pack has no ' + record_id + ' procedure')
    return None


def edit_rules(task, database, role):
    # Sample: Resolve a v9 task binding. Read its pinned edit-guard PROCEDURE rules. Use local rules for a v7 binding.
    body = pinned_procedure(task, database, role, 'decision.edit-guard', 9)
    return deepcopy(EDIT_RULES) if body is None else body['rules']
