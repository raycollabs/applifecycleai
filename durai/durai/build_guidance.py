"""Sample: Call guidance_text('process'). Check the numbered work order. Call guidance_text() and check all topics."""

ENVIRONMENT_VARIABLE = 'DURAI_BUILD_DATABASE_URL'

TOPICS = {
    'process': (
        'Steps for each component.\n'
        '1. Call get_sample. Read the request in the words of the user.\n'
        '2. Write docs/design.md. Say what the component must do, its data, its terminal states and its failure cases. '
        'Call submit_stage with stage DESIGN and the path.\n'
        '3. Write docs/plan.md. Give phases, small modules, and a test table with columns class, function and goal. '
        'Call submit_stage with stage PLAN.\n'
        '4. Write the code in small modules. Write the tests. One test function for each goal.\n'
        '5. Call run_tests. Read the failures. Fix the code. Call run_tests again until all tests pass.\n'
        '6. Call submit_stage with stage IMPLEMENTATION and the code paths. Call submit_stage with stage TESTS and the test paths.\n'
        '7. Call finish.\n'
        'The stages must go in this order: DESIGN, PLAN, IMPLEMENTATION, TESTS. A component with screens adds a fifth stage, DEPLOYED (see topic app). Code files are refused before the PLAN is accepted. '
        'The workspace stays the same between components. Extend the existing code. Do not delete files from earlier components.'),
    'environment': (
        'Runtime: Python 3.13 with the standard library and psycopg 3. Database: PostgreSQL 17. There is no network access. '
        'Use only the workspace folder. Put code in src/ and tests in tests/. Tests use the unittest module. '
        'run_tests runs python -m unittest discover -s tests with the workspace as the current folder and on the import path. The workspace is read-only during the run: write temporary files under /tmp. '
        'The environment variable ' + ENVIRONMENT_VARIABLE + ' holds a PostgreSQL URL for a disposable empty database. '
        'Read it in the tests and in the code. Create your own schema and tables with SQL that you write. '
        'Run the schema script again safely: use CREATE ... IF NOT EXISTS. Every test must clean its own rows or use its own unique keys. '
        'Do not write a password or a host name in any file.'),
    'comments': (
        'Comments in code: write only sample walkthroughs in ASD-STE100 style. '
        'Form: # Sample: Start with 200 shares. Remove 1 lot of 100 shares. Check 100 shares. '
        'Use concrete input values, one action and one result you can observe. '
        'Do not write comments that explain why code exists, its purpose, or how you chose a design. Put such text in docs/design.md.'),
    'tests': (
        'Tests: one test function for each goal. Name the function after the goal. '
        'Write the test table in docs/plan.md first: class, function, goal. '
        'Use sample data close to the real business case, with real amounts and real names of things. '
        'Check observable results: stored rows, returned values, refusals. Add a sample walkthrough comment to every test function. '
        'A test that proves a refusal must also prove that nothing was written.'),
    'data': (
        'Money and quantities: use exact decimal values (Python Decimal, PostgreSQL NUMERIC). Never use binary floating point for money. '
        'Keep the unit that gives the amount meaning, for example the size of one contract, next to the amount. '
        'Keep every fee, cost and payment as its own record. Derive totals from records. Do not overwrite a stored fact. '
        'Record a correction as a new record that points to the old one. '
        'Keep two times for a fact that can be corrected: when it was true and when you learned it. '
        'Check input at the edge: reject missing fields, negative values where they make no sense, and unknown codes, with a clear error. '
        'Keep an audit trail: who did what, when, with the key of the request.'),
    'concurrency': (
        'Mutating requests: every request that changes data carries an idempotency key from the caller. '
        'Save the key with a fingerprint of the request body in the same transaction as the change. '
        'Same key and same fingerprint: return the first result and change nothing. '
        'Same key and different fingerprint: refuse with a clear conflict error and change nothing. '
        'Put a unique constraint in the database. Do not rely on a check in application code alone. '
        'When one transaction locks more than one row, lock the rows in one fixed order, for example sorted by key. '
        'Use one transaction for each request. Roll back on any error. '
        'Give long-running jobs explicit terminal states: succeeded, failed, cancelled, lost. Do not infer completion from missing rows.'),
    'access': (
        'Access control: check the permission in the SQL statement itself, as part of the WHERE clause or a join, '
        'so that a row outside the permission is never read. '
        'Check that the permission is valid at the time of the request. '
        'Every read path, including summaries and reports, uses the same check. '
        'A refusal must not tell the caller whether the hidden row exists.'),
    'app': (
        'Use py-fastapi-next for transaction apps. First list the workspace and find the prepared shell. '
        'When shell-manifest.json is present, leave its files unchanged, including shell_api/, package files and shared app session files. '
        'Write domain entities and rules under src/, numbered domain migrations under src/schema/, and domain tests under tests/. '
        'Add a FastAPI APIRouter named router in src/routes.py when the component needs API routes. The shell mounts it under /api. '
        'In a database transaction call shell_api.app.actor_for(connection, request). It returns the actor ID. '
        'Call shell_api.app.write_once(connection, actor, operation, request.state.idempotency_key, payload, create). '
        'Give create no arguments and return a JSON-compatible receipt. '
        'For an INSERT with ON CONFLICT, use unqualified column names in RETURNING or declare the target alias in that INSERT. '
        'Test the new row and conflict paths in PostgreSQL before submitting IMPLEMENTATION. '
        'Write domain audit and events in that transaction. Keep session handling, health and error responses in the shell. '
        'If the component asks for screens, add domain routes under app/ and use the shell menu and session functions. '
        'Keep the shell menu and signed-in actor visible on every signed-in screen, including empty and error states. '
        'Read the API from server code. Show loading, empty, error and ok states. Keep secrets on the server. '
        'Use a POST action for sign-in and sign-out. A prefetched GET must leave the cookie alone. '
        'Keep redirects local. Call check_app to verify each signed-in screen and the prefetch check. '
        'Submit DEPLOYED only after check_app passes. For a domain-only request, submit the domain code and its tests. '
        'For an older workspace without a shell manifest, keep the existing Next.js App Router and JSON API. '
        'Use the pinned packages and write new code only for the requested domain.'),
    'enhanced': (
        'Enhanced request. Call get_enhanced_request once, after get_sample. It returns the words of the user first. '
        'Then it lists details the request does not give, concepts to apply, and an answer format for each stage. '
        'Keep the words of the user. Answer each open question in docs/design.md: ask the user, or write your assumption and mark it. '
        'Use the concepts by name and cite the words of the user that need them. '
        'Fill every section of the answer format of a stage in the document of that stage. '
        'Do not claim a test that you did not run.'),
    'structure': (
        'Structure: small modules. Each module has one job and a clear interface of plain functions. '
        'Modules do not reach into each other tables or private names. '
        'Put SQL schema in .sql files under src/schema/ with numbered names. '
        'Write the plan so that each module can be built, tested and committed on its own.'),
}


def topic_names(enhanced=True):
    # Sample: List 10 topic names. With enhanced False list 9 and leave out enhanced.
    return [name for name in TOPICS if enhanced or name != 'enhanced']


def guidance_text(topic=None, enhanced=True):
    # Sample: Call guidance_text('tests'). Return the tests text. Call guidance_text('x'). Raise KeyError. Call guidance_text(). Return all topics. Call guidance_text('enhanced', enhanced=False). Raise KeyError.
    names = topic_names(enhanced)
    if topic is None:
        return '\n\n'.join(name.upper() + '\n' + TOPICS[name] for name in names)
    if topic not in names:
        raise KeyError('Unknown topic ' + str(topic) + '. Known topics: ' + ', '.join(names))
    return TOPICS[topic]
