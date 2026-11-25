"""Sample: Build the retry brief for funds with 2 selected patterns. Return 7 sections under 4000 characters and their digest."""
import ast
from .common import digest

FORMAT = 'durai-advisor-brief/v1'
LIMIT = 4000
INTERFACE_LIMIT = 1800
TURNS = ('PLAN', 'RETRY')
QUESTION = ('Give the next 1 to 3 steps. Name the pattern rule that each step applies. '
            'Name each preference you depart from and its evidence. Reply with JSON only.')
CUT_ORDER = ('Conversation', 'Preferences', 'Facts', 'Required rules', 'Last result', 'Problem', 'Interfaces')


def signatures(task, files):
    # Sample: Read payment_funds.py with scope create_operation. Return "def create_operation(kind, payment_id, amount_minor, request_key, ...)".
    found = []
    for path in task.get('writePaths', []):
        content = files.get(path)
        text = content.decode('utf-8', 'replace') if isinstance(content, bytes) else content or ''
        symbols = set((task.get('editScope') or {}).get(path) or [])
        try:
            tree = ast.parse(text)
        except SyntaxError:
            found.append(path + ': (not parsed)')
            continue
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and (not symbols or node.name in symbols):
                found.append(path + ': def ' + node.name + '(' + ast.unparse(node.args) + ')')
    return found


def rule_lines(patterns):
    # Sample: Read idempotency correctness with 2 invariants. Return one line with its id, rule, and invariants.
    return ['- [' + item['id'] + '] ' + item['rule'] + ''.join(' Invariant: ' + text for text in item.get('invariants', [])[:2])
            for item in patterns if item['strength'] == 'REQUIRED']


def interface_lines(patterns, limit=INTERFACE_LIMIT):
    # Sample: Read funds correctness and charge correctness with limit 1800. Return all funds lines and no charge line.
    lines = []
    for item in patterns:
        interface = item.get('interface') if item['strength'] == 'REQUIRED' else None
        if not interface:
            continue
        block = []
        for function in interface['functions']:
            block.append('- ' + function['signature'] + ' -> ' + function['returns'] +
                         ('; raises ' + '; '.join(function['errors']) if function['errors'] else ''))
        for passed in interface['passedIn']:
            block += ['- ' + passed['name'] + '.' + method['name'] + ' -> ' + method['returns'] + '; raises ' + method['raises']
                      for method in passed['methods']]
        if lines and len('\n'.join(lines + block)) > limit:
            continue
        lines += block
    return lines


def preference_lines(patterns):
    # Sample: Read idempotency default with alternative Redis. Return its rule, its alternative, and its exception evidence.
    lines = []
    for item in patterns:
        if item['strength'] == 'REQUIRED':
            continue
        preference = item.get('preference') or {}
        alternatives = '; '.join(alt['id'] + ' when ' + alt['when'] for alt in preference.get('alternatives', []))
        evidence = '; '.join(preference.get('exceptionEvidence', []))
        lines.append('- [' + item['id'] + '] ' + item['rule'] + (' Alternative: ' + alternatives + '.' if alternatives else '') +
                     (' Exception evidence: ' + evidence if evidence else ''))
    return lines


def last_result(history):
    # Sample: Read attempt 1 with feedback "A protected acceptance goal failed" and goal test_refund. Return both lines.
    if not history:
        return ''
    entry = history[-1]
    lines = ['Verdict: ' + str(entry.get('verdict'))]
    if entry.get('feedback'):
        lines.append('Feedback: ' + entry['feedback'])
    if entry.get('goals'):
        lines.append('Failing goals: ' + ', '.join(entry['goals']))
    return '\n'.join(lines)


def fit(sections, limit):
    # Sample: Give a 40000-character Conversation section and limit 4000. Cut Conversation first. Return text of 4000 characters or less.
    def render(parts):
        return '\n\n'.join('## ' + name + '\n' + body for name, body in parts if body)
    parts = list(sections)
    for name in CUT_ORDER:
        text = render(parts)
        if len(text) <= limit:
            return text
        index = next(i for i, (key, _) in enumerate(parts) if key == name)
        excess = len(text) - limit
        body = parts[index][1]
        keep = max(len(body) - excess - len(' [cut]'), 0)
        parts[index] = (name, body[:keep] + ' [cut]' if keep else '')
    return render(parts)[:limit]


def build_brief(task, files, selection, turn, history=None, head=None, limit=LIMIT):
    # Sample: Build the PLAN brief for funds. Return Problem, Interfaces, Facts, Required rules, Preferences, and Question sections.
    if turn not in TURNS:
        raise ValueError('Unknown advisor turn')
    goal = (task.get('readiness') or {}).get('failingAcceptanceGoal') or task.get('failingAcceptanceGoal')
    problem = 'Turn: ' + turn + '\nObjective: ' + task.get('objective', '') + ('\nAcceptance goal: ' + goal if goal else '')
    facts = '\n'.join(['Write paths: ' + ', '.join(task.get('writePaths', []))] + signatures(task, files))
    sections = [('Problem', problem), ('Interfaces', '\n'.join(interface_lines(selection['patterns']))), ('Last result', last_result(history) if turn == 'RETRY' else ''),
                ('Facts', facts), ('Required rules', '\n'.join(rule_lines(selection['patterns'])) or 'No selected rule.'),
                ('Preferences', '\n'.join(preference_lines(selection['patterns'])) or 'No selected preference.'),
                ('Conversation', head or ''), ('Question', QUESTION)]
    text = fit(sections, limit)
    return {'format': FORMAT, 'turn': turn, 'text': text, 'characters': len(text), 'patternIds': list(selection['patternIds']),
            'selectionDigest': selection['digest'], 'digest': digest(text.encode('utf-8'))}
