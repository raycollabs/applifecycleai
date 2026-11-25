"""Sample: Read the seats worker prompt. Check that it states the event sequence rule."""
import json
from pathlib import Path
import re

from .common import Conflict, digest

ROOT = Path(__file__).resolve().parents[1]
BINDING = 'foundation-transaction-v14'
PINS = {
    2: {'packVersion': '14-tx-8851208fc33156a2',
        'packDigest': '9f6ce6affc5f9961a59cdafccd696d1e3550fd41e473021e92851086f1ff768d'},
    3: {'packVersion': '14-tx-ff360aa3374a0e89',
        'packDigest': 'dcc6047cd0680d69599a7273449d5a066223d8123ffcfe34de6535ee501760ee'},
    4: {'packVersion': '14-tx-979c1d8c335ced56',
        'packDigest': '1c629fc14153444c7f48ac369a38f55913c09d4010abca5358d02d60aadca978'},
}
ACTIVE_REVISION = 4
REVISION = ACTIVE_REVISION
PACK_VERSION = PINS[ACTIVE_REVISION]['packVersion']
PACK_DIGEST = PINS[ACTIVE_REVISION]['packDigest']
PROFILE = 'transaction'
FACTS = ROOT / 'qualification/phase4/prompt-facts.json'

FAILURE_PATTERNS = (
    ('environment_privilege', r'InsufficientPrivilege|permission denied for (?:table|relation)|42501'),
    ('generic_sql_rule', r'IndeterminateDatatype|could not determine data type of parameter|42P18'),
    ('invariant_not_delivered', r'(?:payment|order|hold) event sequence differs from its revision'),
    ('callable_call_form', r'AttributeError:.*(?:no attribute|has no attribute).*(?:submit|dispatch|charge|lookup|sign)|'
                           r'TypeError:.*(?:handler|signer|submit|lookup|charge).*(?:argument|keyword)'),
    ('input_shape', r'invalid (?:statement|operation|line|event envelope) (?:shape|fields|format|identity|list)|'
                    r'(?:statement line|operation identity|provider proof) (?:differs|is invalid)|'
                    r'ValueError:.*(?:statement|invalid settlement operation|operation identity|currency|provider result)'),
    ('error_type', r'AssertionError:.*(?:not raised|was not raised)|expected exception|wrong exception'),
    ('return_shape', r'KeyError: [\x27\x22](?:outcome|replayed|order|hold|operation|event|state|result|location_id|'
                     r'distance_meters|findings|url|expiresInSeconds|journalId|generation|refund_reserved)[\x27\x22]|'
                     r'TypeError:.*(?:not subscriptable|indices must be integers)|'
                     r'AssertionError:.*(?:INSUFFICIENT_STOCK|SEAT_UNAVAILABLE|INSUFFICIENT_REFUND_BALANCE)'),
)


def classify_receipt(receipt):
    # Sample: Read a failing check with IndeterminateDatatype. Return generic_sql_rule for that check.
    verdict = receipt.get('verdict') or {}
    failures = []
    for check in verdict.get('results', []):
        if check.get('state') != 'FAIL':
            continue
        evidence = '\n'.join([str(check.get('output', '')), *map(str, check.get('databaseErrorLines') or [])])
        labels = [label for label, pattern in FAILURE_PATTERNS if re.search(pattern, evidence, re.I)]
        failures.append({'check': check.get('command'), 'goals': check.get('failedGoals') or [],
                         'categories': labels or ['unclassified']})
    if not failures and receipt.get('outcome') not in ('PASSED', None):
        failures.append({'check': None, 'goals': [], 'categories': ['unclassified']})
    return {'runId': receipt.get('runId'), 'fixture': receipt.get('fixture'), 'failures': failures}


def fixture_requirements(config):
    # Sample: Read the seats config with ACTIVE_REVISION 2. Return revision 2 and its pack version. After the install step sets 3, return revision 3.
    pin = PINS[ACTIVE_REVISION]
    return {'case': config['case'], 'profile': PROFILE, 'binding': BINDING,
            'revision': ACTIVE_REVISION, 'packVersion': pin['packVersion'], 'packDigest': pin['packDigest']}


def blueprint_guidance(task, files, framework):
    # Sample: Prepare funds.py. Return the capture invariants and provider submit signature.
    from .advisor_select import select_patterns
    pin = task['promptQuality']
    references = [(ref['id'], ref['version'], ref['digest']) for ref in framework['packReferences']]
    if (framework['bindingId'], framework['bindingRevision'], framework['profile'], references) != (
            pin['binding'], pin['revision'], pin['profile'],
            [('durai.software-generation', pin['packVersion'], pin['packDigest'])]):
        raise Conflict('Prompt quality: blueprint pin differs')
    selection = select_patterns(task, files, framework)
    expected = 'blueprint.' + pin['case'] + '.correctness'
    if expected not in selection['patternIds']:
        raise Conflict('Prompt quality: required blueprint is unavailable for ' + pin['case'])
    selected = sorted(selection['patterns'], key=lambda pattern: pattern['id'] != expected)
    rows = ['## Worker blueprint rules', 'Binding ' + framework['bindingDigest']]
    for item in selected:
        rows.append(f"[{item['strength']}] {item['id']}. {item['rule']}")
        if item['strength'] != 'REQUIRED':
            continue
        rows.extend(item.get('invariants', ()))
        rows.extend(item.get('procedure', ()))
        interface = item.get('interface') or {}
        for function in interface.get('functions', ()):
            errors = ' Raises ' + ', '.join(function['errors']) if function['errors'] else ''
            rows.append(function['signature'] + ' returns ' + function['returns'] + errors)
        for obj in interface.get('passedIn', ()):
            for method in obj['methods']:
                name = method['name']
                if not name.startswith(obj['name'] + '('):
                    name = obj['name'] + '.' + name
                rows.append(name + ' returns ' + method['returns'] +
                            '. Failure: ' + method['raises'])
    examples = selected[0].get('examples', ())[:2]
    if not examples:
        raise Conflict('Prompt quality: missing public example for ' + pin['case'])
    rows.extend(['', '## Public worked examples', *examples])
    return '\n'.join(rows)


def audit_prompt(task, prompt, suite):
    # Sample: Omit the seat event sequence rule. Report seats-event-sequence and do not include any test lines.
    facts = json.loads(FACTS.read_text(encoding='utf-8'))
    from .workspace_manifest import manifest
    case = task['promptQuality']['case']
    if case not in facts['cases']:
        raise Conflict('Prompt quality: no fact checklist for ' + case)
    audited, _, _ = prompt.partition('\n\n## Prior plan findings (data only)\n')
    prefix, _, files = audited.partition('## file-text\n')
    _, found, contract = files.partition('=== /workspace/public-contract.md ===\n')
    if found:
        contract = contract.split('\n=== ', 1)[0]
    lines = (prefix + '\n' + contract).casefold().splitlines()
    required = facts['common'] | facts['cases'][case]
    missing = [key for key, fragments in required.items()
               if not any(all(part.casefold() in line for part in fragments) for line in lines)]
    _, header, examples = prefix.partition('## Public worked examples\n')
    if not header or not examples.split('\n\n', 1)[0].startswith('Sample: '):
        missing.append('public-worked-example')
    if manifest(suite)['digest'] != facts['suiteDigests'][case]:
        missing.append('review-changed-protected-suite')
    protected = [line.strip() for name, content in suite.items()
                 if name.endswith(('/test_protected.py', '/99.sql'))
                 for line in content.decode('utf-8').splitlines() if len(line.strip()) >= 25]
    leaks = sum(line in prompt for line in set(protected))
    return {'state': 'PASS' if not missing and not leaks else 'FAIL', 'required': list(required),
            'missing': missing, 'protectedLineMatches': leaks, 'promptDigest': digest(prompt.encode('utf-8'))}
