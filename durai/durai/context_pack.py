"""Sample: Build the pack for core_0012.sql with 18 workspace files. Return edit rules, a map, a schema index, and file text under 60000 characters."""
import json
from pathlib import PurePosixPath
import re
from .common import digest

SECTIONS = ('edit-rules', 'workspace-map', 'schema-index', 'file-text')
COMMENT_FORMATS = {'.sql': '-- Sample: <concrete inputs and expected results>',
                   '.py': '# Sample: <concrete inputs and expected results>'}
TABLE = re.compile(r'CREATE\s+(?:UNLOGGED\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][\w.]*)\s*\(', re.I)
FUNCTION = re.compile(r'CREATE\s+(?:OR\s+REPLACE\s+)?(FUNCTION|PROCEDURE)\s+([A-Za-z_][\w.]*)\s*\(', re.I)
RETURNS = re.compile(r'\s*RETURNS\s+(.+?)\s+(?:LANGUAGE|AS|SECURITY|SET|STABLE|IMMUTABLE|VOLATILE|STRICT)\b', re.I | re.S)
TABLE_PRIVILEGE = re.compile(
    r'^\s*(GRANT|REVOKE)\s+((?:SELECT|INSERT|UPDATE|DELETE|TRUNCATE|REFERENCES|TRIGGER|ALL)\b[^;]*?)'
    r'\s+ON\s+(?:TABLE\s+)?([\w.]+(?:\s*,\s*[\w.]+)*)\s+(TO|FROM)\s+([^;]+);', re.I | re.M)
SQL_RULES = (
    'Cast a nullable PostgreSQL parameter to its intended type when the expression does not infer it. '
    'Use %s::text IS NULL for a nullable text parameter.',
    'PostgreSQL row locks such as FOR SHARE require SELECT and UPDATE privilege on at least one column. '
    'Check the runtime role grants before you use a row lock.',
)


def text_of(content):
    # Sample: Read b"x = 1\n". Return "x = 1\n". Read 3 undecodable bytes. Return None.
    try:
        return content.decode('utf-8') if isinstance(content, bytes) else content
    except UnicodeDecodeError:
        return None


def balanced(text, start):
    # Sample: Read "f(a int, b numeric(10,2)) RETURNS" from the first "(". Return the index after the matching ")".
    depth = 0
    for index in range(start, len(text)):
        if text[index] == '(':
            depth += 1
        elif text[index] == ')':
            depth -= 1
            if depth == 0:
                return index + 1
    return len(text)


def line_of(text, index):
    # Sample: Read "a\nb\nCREATE" at the index of CREATE. Return line 3.
    return text.count('\n', 0, index) + 1


def sql_index(files):
    # Sample: Read core_0006.sql with CREATE TABLE durai_ontology.policy(...) and CREATE FUNCTION durai_ontology.project(text). Return both with file and line.
    tables, functions, privileges = {}, [], {}
    for path in sorted(files):
        text = text_of(files[path]) if path.endswith('.sql') else None
        if not text:
            continue
        for match in TABLE.finditer(text):
            end = balanced(text, match.end() - 1)
            tables.setdefault(match.group(1).lower(), []).append(
                {'path': path, 'line': line_of(text, match.start()), 'statement': text[match.start():end].strip() + ';'})
        for match in FUNCTION.finditer(text):
            end = balanced(text, match.end() - 1)
            returns = RETURNS.match(text, end)
            functions.append({'name': match.group(2).lower(), 'kind': match.group(1).upper(),
                              'signature': match.group(2) + text[match.end() - 1:end],
                              'returns': ' '.join(returns.group(1).split()) if returns else None,
                              'path': path, 'line': line_of(text, match.start())})
        for match in TABLE_PRIVILEGE.finditer(text):
            for table in match.group(3).lower().split(','):
                privileges.setdefault(table.strip(), []).append({
                    'path': path, 'line': line_of(text, match.start(1)),
                    'statement': ' '.join(match.group().split())})
    return {'tables': tables, 'functions': functions, 'privileges': privileges}


IMPORT = re.compile(r'^[ \t]*(?:from[ \t]+(\.*[A-Za-z_][\w.]*|\.+)[ \t]+import[ \t]+([\w, \t]+)|import[ \t]+([A-Za-z_][\w.]*))', re.M)


def module_path(base, name, files):
    # Sample: Read base /workspace/durai and name outbox. Return /workspace/durai/outbox.py when that file exists.
    for candidate in (base + '/' + name.replace('.', '/') + '.py', base + '/' + name.replace('.', '/') + '/__init__.py'):
        if candidate in files:
            return candidate
    suffix = '/' + name.replace('.', '/') + '.py'
    return next((path for path in sorted(files) if path.endswith(suffix)), None)


def imported_paths(task, files):
    # Sample: Read payment_funds.py with "from .db import transaction" and "from .outbox import enqueue". Return db.py and outbox.py in that order.
    found = []
    for target in task['writePaths']:
        text = text_of(files.get(target, b'')) or ''
        base = str(PurePosixPath(target).parent)
        for source, names, plain in IMPORT.findall(text):
            if plain:
                modules = [(base, plain)]
            elif source.strip('.'):
                modules = [(base, source.strip('.'))]
            else:
                modules = [(base, name.strip()) for name in names.split(',') if name.strip()]
            for folder, name in modules:
                path = module_path(folder, name, files)
                if path and path not in found and path not in task['writePaths']:
                    found.append(path)
    return found


def table_paths(task, files, index):
    # Sample: Read journal.py that names journal_line. transactions/v1.sql defines journal_line. Return transactions/v1.sql.
    text = '\n'.join(text_of(files.get(target, b'')) or '' for target in task['writePaths'])
    found = []
    for name, places in sorted(index['tables'].items()):
        short = name.split('.')[-1]
        if re.search(r'\b' + re.escape(short) + r'\b', text, re.I):
            for place in places:
                if place['path'] not in found and place['path'] not in task['writePaths']:
                    found.append(place['path'])
    return found


def ranked_paths(task, files, index=None):
    # Sample: Read write path core_0012.sql, an objective that names application_policy_graph.py, and db.py. Return them in that order with reasons target, named, rest.
    objective = task.get('objective', '')
    order = [(path, 'target') for path in task['writePaths'] if path in files]
    seen = {path for path, _ in order}
    groups = (('named', [path for path in sorted(files) if PurePosixPath(path).name in objective]),
              ('import', imported_paths(task, files)),
              ('table', table_paths(task, files, index if index is not None else sql_index(files))),
              ('rest', sorted(files)))
    for reason, paths in groups:
        for path in paths:
            if path not in seen:
                order.append((path, reason))
                seen.add(path)
    return order


PROTECTED_NOTICE = ('Protected tests are not readable and are not in this pack. No tool returns their code. '
                    'The objective and the goal names are the only test data. Do not search for test code.')


def task_notes(task, goals=()):
    # Sample: Read objective "Keep forkOf. Audit once." and goal test_two_lots. Return objective lines "Keep forkOf." and "Audit once." and goal test_two_lots.
    sentences = re.split(r'(?<=[.!?])\s+', ' '.join(task.get('objective', '').split()))
    notes = [('task:objective', '\n'.join(item for item in sentences if item))] if task.get('objective') else []
    return notes + [('task:goal', name) for name in dict.fromkeys(goals)]


def edit_rules(task, files):
    # Sample: Read write path core_0012.sql with 7 existing lines. Return "exists, 7 lines, use edit_file" and the "-- Sample:" format.
    lines = []
    for path in task['writePaths']:
        suffix = PurePosixPath(path).suffix
        if path in files:
            text = text_of(files[path]) or ''
            lines.append(path + ': exists (' + str(text.count('\n') + (0 if text.endswith('\n') else 1)) + ' lines). '
                         'write_file is refused for it. Use edit_file. To replace the whole file, set old_string to its '
                         'full current text from the file-text section and new_string to the new text.')
        else:
            lines.append(path + ': new file. Create it once with write_file. Then use edit_file.')
        if suffix in COMMENT_FORMATS:
            lines.append(path + ': each added or changed function has exactly one comment in the form "' +
                         COMMENT_FORMATS[suffix] + '". No other comment form is accepted.')
    lines.append(PROTECTED_NOTICE)
    lines.append('The text below is the current workspace. Do not read a file again when its full text is in this pack.')
    return '\n'.join(lines)


def workspace_map(task, files, included):
    # Sample: Read core_0012.sql (382 bytes) and db.py (5063 bytes, text omitted). Return two rows with their state.
    rows = []
    for path in sorted(files):
        text = text_of(files[path])
        role = 'write' if path in task['writePaths'] else 'read'
        state = 'text in pack' if path in included else 'not in pack; use show_lines or read_file'
        rows.append(path + ' | ' + role + ' | ' + str(len(files[path])) + ' bytes | ' +
                    (str(text.count('\n') + 1) + ' lines' if text is not None else 'binary') + ' | ' + state)
    return '\n'.join(rows)


def index_text(index, include_privileges=True):
    # Sample: Read table durai_ontology.policy at core_0006.sql line 4. Return "TABLE durai_ontology.policy core_0006.sql:4".
    rows = ['TABLE ' + name + ' ' + ', '.join(place['path'] + ':' + str(place['line']) for place in places)
            for name, places in sorted(index['tables'].items())]
    rows += [item['kind'] + ' ' + ' '.join(item['signature'].split()) +
             (' RETURNS ' + item['returns'] if item['returns'] else '') + ' ' + item['path'] + ':' + str(item['line'])
             for item in index['functions']]
    if rows and include_privileges:
        rows += list(SQL_RULES)
        rows += ['Declared table privileges in visible SQL. Check effective grants and RLS in the target database.']
        statements = (place['statement'] + ' ' + place['path'] + ':' + str(place['line'])
                      for table in sorted(index.get('privileges', {})) for place in index['privileges'][table])
        rows += list(dict.fromkeys(statements))
    return '\n'.join(rows) or '(no SQL objects)'


def build_pack(task, files, max_characters=60000, goals=(), guidance='', memory='', session=''):
    # Sample: Build a pack with limit 60000 for 42619 bytes of files. Return all 18 files and no cut paths.
    index = sql_index(files)
    included, cut, body, reasons = [], [], [], {}
    rules = edit_rules(task, files) + ('\n\n' + guidance if guidance else '')
    if memory:
        from .context_delivery import MEMORY_TITLE
        memory = '\n\n' + MEMORY_TITLE + '\n' + memory
    if session:
        session = '\n\n' + session
    base = len(rules) + len(workspace_map(task, files, set())) + 100 + len(memory) + len(session)
    schema = index_text(index)
    first = next((path for path in task['writePaths'] if path in files), '')
    first_text = text_of(files[first]) if first else ''
    reserve = len(first) + len(first_text or '') + 12 if first else 0
    if base + len(schema) + reserve > max_characters:
        schema = index_text(index, include_privileges=False)
    used = base + len(schema)
    for path, reason in ranked_paths(task, files, index):
        text = text_of(files[path])
        block = '=== ' + path + ' ===\n' + (text or '') + '\n'
        if text is not None and used + len(block) <= max_characters:
            included.append(path)
            reasons[path] = reason
            body.append(block)
            used += len(block)
        else:
            cut.append(path)
    parts = {'edit-rules': rules, 'workspace-map': workspace_map(task, files, set(included)),
             'schema-index': schema, 'file-text': ''.join(body) or '(no file text)'}
    text = '\n\n'.join('## ' + name + '\n' + parts[name] for name in SECTIONS) + memory + session
    sections = {name: digest(parts[name].encode('utf-8')) for name in SECTIONS}
    if memory:
        sections['prior-plan-findings'] = digest(memory.encode('utf-8'))
    if session:
        sections['session-context'] = digest(session.encode('utf-8'))
    return {'format': 'durai-context-pack/v2', 'notes': task_notes(task, goals), 'text': text, 'characters': len(text),
            'included': included, 'cut': cut, 'reasons': reasons,
            'sections': sections, 'index': index, 'digest': digest(text.encode('utf-8'))}


def pack_record(pack):
    # Sample: Store pack D-1. Return an evidence value with references [], packDigest D-1, and its text.
    return {'references': [], 'contextPack': pack['format'], 'packDigest': pack['digest'], 'text': pack['text'],
            'index': json.dumps(pack['index'], sort_keys=True), 'cut': json.dumps(pack['cut']),
            'reasons': json.dumps(pack.get('reasons', {}), sort_keys=True), 'notes': json.dumps(pack['notes'])}


def context_procedure(task, database, role):
    # Sample: Resolve a v14 binding. Return maxCharacters 60000 and four tools. Resolve a v13 binding. Return None.
    from .decision_points import pinned_procedure
    body = pinned_procedure(task, database, role, 'decision.context-pack', 14)
    if not body or body.get('enabled') is not True:
        return None
    return {'maxCharacters': int(body['maxCharacters']), 'tools': list(body.get('tools', [])), 'sections': list(body['sections'])}
