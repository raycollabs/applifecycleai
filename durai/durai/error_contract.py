"""Sample: Read a test that expects InvalidParameterValue and objective "Reject binding B-9". Return finding InvalidParameterValue."""
import ast
import re
from .database_feedback import ERROR_CODES

EXEMPT = ('Exception', 'BaseException')
RAISES = ('assertRaises', 'assertRaisesRegex', 'raises')
STATE_ATTRIBUTES = ('sqlstate', 'pgcode')
SQLSTATE = re.compile(r'^[0-9][0-9A-Z]{4}$')
MINIMUM_VERSION = 10


def error_name(node):
    # Sample: Read psycopg.errors.InvalidParameterValue. Return InvalidParameterValue. Read a call. Return None.
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def expected_errors(suite):
    # Sample: Read a suite with assertRaises((Conflict, KeyError)) and sqlstate '22023'. Return [('22023',), ('Conflict', 'KeyError')].
    found = set()
    for path, content in suite.items():
        if not path.endswith('.py'):
            continue
        tree = ast.parse(content.decode('utf-8') if isinstance(content, bytes) else content)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and error_name(node.func) in RAISES and node.args:
                first = node.args[0]
                names = tuple(sorted({error_name(item) for item in (first.elts if isinstance(first, ast.Tuple) else [first])} - {None}))
                if names and not set(names) & set(EXEMPT):
                    found.add(names)
            if isinstance(node, ast.stmt) and not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                inner = list(ast.walk(node))
                if any(isinstance(item, ast.Attribute) and item.attr in STATE_ATTRIBUTES for item in inner):
                    found.update((item.value,) for item in inner if isinstance(item, ast.Constant) and
                                 isinstance(item.value, str) and SQLSTATE.match(item.value))
    return sorted(found)


def named(objective, error):
    # Sample: Read objective "Raise SQLSTATE 22023" and error InvalidParameterValue. Return True.
    words = {error}
    if error in ERROR_CODES:
        words.add(ERROR_CODES[error])
    words.update(name for name, code in ERROR_CODES.items() if code == error)
    return any(re.search(r'(?<![A-Za-z0-9_])' + re.escape(word) + r'(?![A-Za-z0-9_])', objective) for word in words)


def unnamed_errors(objective, suite):
    # Sample: Read objective "Reject unknown keys" and a suite that expects KeyError. Return ['KeyError']. Name Conflict for (Conflict, KeyError). Return [].
    return [' or '.join(group) for group in expected_errors(suite)
            if not any(named(objective or '', error) for error in group)]


def enforced(task, database, role):
    # Sample: Read a task bound to pack v10. Return True. Read a task bound to pack v9. Return False.
    binding = task.get('binding', {}).get('bindingId')
    if not database or not binding:
        return True
    from .packs import PackStore
    store = PackStore(database, role)
    try:
        for reference in store.resolve(binding)['document']['packs']:
            document = store.get(reference)
            if document['kind'] == 'GENERIC':
                return int(document['version']) >= MINIMUM_VERSION
    except (KeyError, ValueError):
        return True
    return True
