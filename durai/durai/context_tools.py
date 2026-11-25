"""Sample: Ask describe_table("durai_ontology.policy") from the pack index. Return its CREATE TABLE statement and its file and line."""
from pathlib import PurePosixPath
import re
from .common import digest

TOOL_NAMES = ('describe_table', 'find_function', 'show_lines', 'search_context')
MAX_ANSWER = 6000
MAX_LINES = 200


def clip(text):
    # Sample: Read an 8000-character answer. Return 6000 characters and a cut notice.
    return text if len(text) <= MAX_ANSWER else text[:MAX_ANSWER] + '\n[answer cut at ' + str(MAX_ANSWER) + ' characters]'


def describe_table(index, files, name):
    # Sample: Ask "durai_ontology.policy". Return the CREATE TABLE at core_0010.sql:1 and each ALTER TABLE line for it.
    key = name.strip().lower()
    places = index['tables'].get(key) or [place for table, items in index['tables'].items()
                                          if table.split('.')[-1] == key for place in items]
    if not places:
        return 'No table named ' + name + ' in the trusted migrations. Tables: ' + ', '.join(sorted(index['tables']))
    from .context_pack import SQL_RULES
    rows = [*SQL_RULES, 'Declared table privileges in visible SQL. Check effective grants and RLS in the target database.']
    for table, changes in index.get('privileges', {}).items():
        if table == key or table.split('.')[-1] == key:
            rows.extend(place['path'] + ':' + str(place['line']) + ' ' + place['statement'] for place in changes)
    rows += [place['path'] + ':' + str(place['line']) + '\n' + place['statement'] for place in places]
    pattern = re.compile(r'ALTER\s+TABLE\s+(?:ONLY\s+)?' + re.escape(places[0]['statement'].split('(')[0].split()[-1]) + r'\b', re.I)
    for path in sorted(files):
        if path.endswith('.sql'):
            for number, line in enumerate(files[path].splitlines(), 1):
                if pattern.search(line):
                    rows.append(path + ':' + str(number) + ' ' + line.strip())
    return clip('\n'.join(rows))


def find_function(index, pattern):
    # Sample: Ask "project_%". Return FUNCTION durai_ontology.project_application_binding(binding_id text) RETURNS integer at core_0012.sql:1.
    expression = re.compile('^' + '.*'.join(re.escape(part) for part in pattern.strip().lower().split('%')) + '$')
    found = [item for item in index['functions']
             if expression.match(item['name']) or expression.match(item['name'].split('.')[-1]) or
             pattern.strip().lower() in item['name']]
    if not found:
        return 'No function matches ' + pattern + '.'
    return clip('\n'.join(item['kind'] + ' ' + ' '.join(item['signature'].split()) +
                          (' RETURNS ' + item['returns'] if item['returns'] else '') + ' ' + item['path'] + ':' +
                          str(item['line']) for item in found))


def show_lines(files, path, start, end):
    # Sample: Ask lines 2 to 3 of core_0002.sql. Return lines 2 and 3 with numbers. Ask ../private/a.json. Return a refusal.
    if path not in files:
        return 'Refused: ' + path + ' is not a readable workspace file.'
    if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
        return 'Refused: give 1 <= start <= end.'
    lines = files[path].splitlines()
    end = min(end, len(lines), start + MAX_LINES - 1)
    return clip('\n'.join(str(number) + ': ' + lines[number - 1] for number in range(start, end + 1)) or
                '(no lines; the file has ' + str(len(lines)) + ' lines)')


def search_context(files, policies, query, limit=5, notes=()):
    # Sample: Search "graph owner role" over "graph_owner role x" and "role y". Return line 1 (3 of 3 words) before line 2 (1 of 3 words).
    terms = list(dict.fromkeys(term.lower() for term in re.findall(r'[A-Za-z0-9_.]+', query)))[:8]
    if not terms:
        return 'Refused: give at least one word.'
    sources = [(path, files[path]) for path in sorted(files)] + \
              [('policy:' + item.get('id', item.get('policyId', '?')), item.get('rule', '')) for item in policies or []] + \
              [(name, text) for name, text in notes]
    found = []
    for order, (path, text) in enumerate(sources):
        for number, line in enumerate(text.splitlines(), 1):
            lowered = line.lower()
            score = sum(term in lowered for term in terms)
            if score:
                found.append((-score, order, number, path + ':' + str(number) + ' [' + str(score) + ' of ' + str(len(terms)) +
                              ' words] ' + line.strip()[:400]))
    if not found:
        return ('No line contains any of: ' + ', '.join(terms) + '. Searched the readable workspace files, the policies, '
                'the task objective, and the goal names. Protected tests are not readable.')
    return clip('\n'.join(item[3] for item in sorted(found)[:limit]))


def readable_text(root, seen):
    # Sample: Read /workspace/a.py from attempt folder F after an edit. Return the edited text. Skip binary files.
    result = {}
    for path in seen:
        target = root.joinpath(*PurePosixPath(path).parts[1:])
        if target.is_file():
            try:
                result[path] = target.read_bytes().decode('utf-8')
            except UnicodeDecodeError:
                continue
    return result


def query_tools(names, index, root, seen, policies, emit, notes=()):
    # Sample: Pin describe_table only. Return one tool. Call it. Emit CONTEXT_QUERY with the answer digest.
    from langchain_core.tools import tool

    def audited(name, arguments, answer):
        # Sample: Answer describe_table("x") with text T. Emit tool describe_table, its arguments, and digest(T).
        emit('CONTEXT_QUERY', {'tool': name, 'arguments': arguments, 'answerDigest': digest(answer.encode('utf-8')),
                               'answerCharacters': len(answer)})
        return answer

    @tool('describe_table', description='Return the CREATE TABLE statement and ALTER TABLE lines of one table from the trusted migrations, with file and line.')
    def describe_table_tool(name: str) -> str:
        # Sample: Call describe_table("policy"). Return the policy table statement and emit one CONTEXT_QUERY.
        return audited('describe_table', {'name': name}, describe_table(index, readable_text(root, seen), name))

    @tool('find_function', description='Return SQL function signatures, return types, and file and line. Use % as a wildcard, for example project_%.')
    def find_function_tool(pattern: str) -> str:
        # Sample: Call find_function("project_%"). Return one signature and emit one CONTEXT_QUERY.
        return audited('find_function', {'pattern': pattern}, find_function(index, pattern))

    @tool('show_lines', description='Return numbered lines start to end (at most 200) of one readable workspace file in its current state.')
    def show_lines_tool(path: str, start: int, end: int) -> str:
        # Sample: Call show_lines("/workspace/a.py", 1, 2). Return 2 numbered lines and emit one CONTEXT_QUERY.
        return audited('show_lines', {'path': path, 'start': start, 'end': end},
                       show_lines(readable_text(root, seen), path, start, end))

    @tool('search_context', description='Return the 5 workspace, policy, objective, or goal-name lines that match the most query words, with file and line.')
    def search_context_tool(query: str) -> str:
        # Sample: Call search_context("owner role"). Return ranked lines and emit one CONTEXT_QUERY.
        return audited('search_context', {'query': query}, search_context(readable_text(root, seen), policies, query, notes=notes))

    tools = {'describe_table': describe_table_tool, 'find_function': find_function_tool,
             'show_lines': show_lines_tool, 'search_context': search_context_tool}
    return [tools[name] for name in names if name in tools]
