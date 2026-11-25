"""Sample: Decide for a task that writes journal.py and location_search.py with no import between them. Record SUBAGENTS."""
from copy import deepcopy
import re
from .context_pack import IMPORT, module_path, text_of
from .decision_points import DecisionPoint, pinned_procedure

POINT = 'decision.execution-strategy'
MEDIAN_CALLS = 8
MONEY = re.compile(r'payment|journal|settlement|funds|ledger', re.I)
DEFAULT_RULES = [
    {'id': 'budget-low', 'when': {'budgetLow': True}, 'choice': 'SINGLE'},
    {'id': 'role-split', 'when': {'roleSplit': True}, 'choice': 'TEAM'},
    {'id': 'loose-modules', 'when': {'moduleShape': 'LOOSE'}, 'choice': 'SUBAGENTS'},
    {'id': 'default', 'choice': 'SINGLE'},
]


def coupled(paths, files):
    # Sample: Read journal.py with "from .outbox import enqueue" and write paths journal.py and outbox.py. Return True.
    for target in paths:
        text = text_of(files.get(target, b'')) or ''
        base = target.rsplit('/', 1)[0] if '/' in target else ''
        for source, names, plain in IMPORT.findall(text):
            if plain:
                modules = [plain]
            elif source.strip('.'):
                modules = [source.strip('.')]
            else:
                modules = [name.strip() for name in names.split(',') if name.strip()]
            for name in modules:
                found = module_path(base, name, files)
                if found and found != target and found in paths:
                    return True
    return False


def features(task, step, pack_files, budget, median=MEDIAN_CALLS):
    # Sample: Task writes journal.py and location_search.py with no import and 40 calls left. Return LOOSE, STANDARD, budgetLow False.
    paths = list(task.get('writePaths', []))
    if len(paths) <= 1:
        shape = 'ONE'
    else:
        shape = 'COUPLED' if coupled(paths, pack_files or {}) else 'LOOSE'
    remaining = (budget or {}).get('modelCalls')
    roles = task.get('roles') or (step or {}).get('roles')
    return {'writePathCount': len(paths), 'moduleShape': shape, 'roleSplit': bool(roles),
            'budgetLow': isinstance(remaining, (int, float)) and not isinstance(remaining, bool) and remaining < 2 * median,
            'riskClass': 'MONEY' if any(MONEY.search(path) for path in paths) else 'STANDARD'}


def rules_for(task, database, role):
    # Sample: Resolve a v15 binding. Return its pinned rules and median 8. Resolve a v14 binding. Return DEFAULT_RULES.
    body = pinned_procedure(task, database, role, POINT, 15)
    if body is None:
        return {'rules': deepcopy(DEFAULT_RULES), 'singleRunMedianModelCalls': MEDIAN_CALLS}
    return {'rules': body['rules'], 'singleRunMedianModelCalls': int(body.get('singleRunMedianModelCalls', MEDIAN_CALLS))}


def choose(task, step, pack_files, budget, emit, database=None, role='durai_runtime'):
    # Sample: Choose for T-1 with 2 loose write paths. Emit DECISION_RECORDED. Return choice SUBAGENTS.
    procedure = rules_for(task, database, role)
    found = features(task, step, pack_files, budget, procedure['singleRunMedianModelCalls'])
    return DecisionPoint(POINT, procedure['rules'], emit).decide(found)
