"""Sample: Bind pack v14. Run attempt 1 with one describe_table call. Check the pack digest, one CONTEXT_QUERY, and WORKER_EFFICIENCY."""
import json
import tempfile
import unittest
from pathlib import Path
from durai.context_pack import build_pack, context_procedure, sql_index
from durai.context_tools import describe_table, find_function, query_tools, search_context, show_lines
from durai.efficiency_metrics import pack_cost, trace_metrics
from test_failure_triage import bound
from test_resume_retry import FIX_FROM_198

MIGRATION = {'/workspace/migrations/core/core_0001.sql':
             b'CREATE SCHEMA ledger;\nCREATE TABLE ledger.lot(\n id text PRIMARY KEY,\n shares numeric(12,2) NOT NULL\n);\n'
             b'ALTER TABLE ledger.lot ADD COLUMN owner text;\n'
             b'CREATE FUNCTION ledger.book_lot(lot_id text, lots integer) RETURNS numeric LANGUAGE sql AS $$\n'
             b' -- Sample: Book 2 lots of 100. Return 200 shares.\n SELECT lots * 100\n$$;\n',
             '/workspace/migrations/core/core_0002.sql': b'-- Sample: Shell for the projection.\n',
             '/workspace/durai/ledger.py': b'def shares(lots):\n    # Sample: Book 2 lots. Return 200 shares.\n    return lots * 100\n'}
TASK = {'writePaths': ['/workspace/migrations/core/core_0002.sql'], 'objective': 'Create core_0002.sql. Use ledger.py.'}


def trace(*calls):
    # Sample: Read calls [("read_file","success"), ("edit_file","error")]. Return one AI message per call and one tool result each.
    messages = [{'role': 'human', 'content': 'go'}]
    for index, (name, status) in enumerate(calls):
        messages.append({'role': 'ai', 'content': '', 'tool_calls': [{'name': name, 'id': 'c' + str(index), 'args': {'file_path': '/workspace/f' + str(index)}}],
                         'completion': {'inputCount': 100, 'outputCount': 10}})
        messages.append({'role': 'tool', 'name': name, 'status': status, 'tool_call_id': 'c' + str(index), 'content': 'x'})
    messages.append({'role': 'ai', 'content': 'done', 'completion': {'inputCount': 100, 'outputCount': 5}})
    return {'messages': messages}


def run_bound(version, scripts, database_worker=False):
    # Sample: Run attempts with scripts [[WRONG], [FIX_FROM_198]] on pack v14. Return the final state, harness, and replayed events.
    from durai.execution_events import replay
    from durai.runtime_deepagents import DeepAgentsWorker
    from test_dev_loop import CAPABILITY, LoopHarness, loop_spec
    task = bound(version)
    harness = LoopHarness(scripts)
    if database_worker:
        from durai.providers import Providers
        harness.providers = Providers()
        harness.providers.register(CAPABILITY, 'deepagents@' + DeepAgentsWorker.version,
                                   lambda model, scratch, attempt_id: DeepAgentsWorker(model, scratch, attempt_id, database='durai_test'))
    state = harness.run(loop_spec(binding={'bindingId': task['binding']['bindingId'],
                                           'contractDigest': task['pins']['contractDigest']}))
    return state, harness, replay(harness.run_id, database='durai_test')


class MetricsTests(unittest.TestCase):
    def test_calls_before_first_write(self):
        # Sample: Record 3 read calls, then a successful edit. Check callsBeforeFirstWrite 3 and modelCalls 5.
        found = trace_metrics(trace(('read_file', 'success'), ('read_file', 'success'), ('read_file', 'success'), ('edit_file', 'success')))
        self.assertEqual((found['callsBeforeFirstWrite'], found['modelCalls'], found['readCalls'], found['distinctFilesRead']), (3, 5, 3, 3))

    def test_refused_write_is_counted(self):
        # Sample: Record a refused write_file, then a successful edit_file. Check refusedWrites 1, editCalls 1, first attempt 0, first write 1.
        found = trace_metrics(trace(('write_file', 'error'), ('edit_file', 'success')))
        self.assertEqual((found['refusedWrites'], found['editCalls'], found['callsBeforeFirstWriteAttempt'], found['callsBeforeFirstWrite']),
                         (1, 1, 0, 1))

    def test_query_calls_and_tokens_are_counted(self):
        # Sample: Record one describe_table call. Check queryCalls 1, readCalls 0, input count 200, first input count 100.
        found = trace_metrics(trace(('describe_table', 'success')))
        self.assertEqual((found['queryCalls'], found['readCalls'], found['inputCount'], found['firstCallInputCount']), (1, 0, 200, 100))


    def test_pack_cost_is_recorded(self):
        # Sample: Record a trace with 4 model calls and a 1000-character pack. Check packCharacters 1000 and packCallCharacters 4000.
        found = pack_cost(trace_metrics(trace(('read_file', 'success'), ('read_file', 'success'), ('edit_file', 'success'))), 1000)
        self.assertEqual(found, {'packCharacters': 1000, 'packCallCharacters': 4000})


ORDER_TASK = {'writePaths': ['/workspace/durai/payment_funds.py'], 'objective': 'Capture funds.'}
ORDER_FILES = {'/workspace/durai/payment_funds.py': b'from .db import transaction\nfrom .outbox import enqueue\n',
               '/workspace/blueprints/a.sql': b'CREATE TABLE payment (id int);\n',
               '/workspace/durai/db.py': b'def transaction():\n    return 1\n',
               '/workspace/durai/outbox.py': b'def enqueue():\n    return 2\n',
               '/workspace/durai/zeta.py': b'Z = 3\n'}


class PackTests(unittest.TestCase):
    def test_pack_states_protected_tests_unreadable(self):
        # Sample: Build the pack for a task with goal test_two_lots. Check the notice line, the goal note, and no test code.
        pack = build_pack(TASK, MIGRATION, goals=['test_two_lots'])
        self.assertIn('Protected tests are not readable', pack['text'])
        self.assertIn(('task:goal', 'test_two_lots'), pack['notes'])
        self.assertNotIn('def test_', pack['text'])
        self.assertEqual(pack['format'], 'durai-context-pack/v2')

    def test_pack_has_schema_index(self):
        # Sample: Index core_0001.sql. Check table ledger.lot at line 2 and function ledger.book_lot(lot_id text, lots integer) RETURNS numeric.
        index = sql_index(MIGRATION)
        self.assertEqual(index['tables']['ledger.lot'][0]['line'], 2)
        self.assertEqual([(item['name'], item['returns']) for item in index['functions']], [('ledger.book_lot', 'numeric')])
        self.assertIn('FUNCTION ledger.book_lot(lot_id text, lots integer) RETURNS numeric', build_pack(TASK, MIGRATION)['text'])

    def test_pack_ranks_target_then_named_files(self):
        # Sample: Write core_0002.sql. The objective names ledger.py. Check file text order core_0002, ledger.py, core_0001.
        text = build_pack(TASK, MIGRATION)['text']
        order = [text.index('=== /workspace/' + path) for path in ('migrations/core/core_0002.sql', 'durai/ledger.py', 'migrations/core/core_0001.sql')]
        self.assertEqual(order, sorted(order))

    def test_imported_modules_follow_named_files(self):
        # Sample: Write payment_funds.py that imports db and outbox. Files are a.sql, db.py, outbox.py, zeta.py. Check the order payment_funds.py, db.py, outbox.py, a.sql, zeta.py.
        pack = build_pack(ORDER_TASK, ORDER_FILES)
        self.assertEqual(pack['included'], ['/workspace/durai/payment_funds.py', '/workspace/durai/db.py', '/workspace/durai/outbox.py',
                                            '/workspace/blueprints/a.sql', '/workspace/durai/zeta.py'])

    def test_table_schema_follows_imports(self):
        # Sample: Write journal.py that names journal_line. transactions/v1.sql defines journal_line. Check v1.sql comes before events/v1.sql.
        task = {'writePaths': ['/workspace/durai/journal.py'], 'objective': 'Post a journal.'}
        files = {'/workspace/durai/journal.py': b"SQL = 'INSERT INTO journal_line VALUES (1)'\n",
                 '/workspace/blueprints/events/v1.sql': b'CREATE TABLE event_inbox (id int);\n',
                 '/workspace/blueprints/transactions/v1.sql': b'CREATE TABLE journal_line (id int);\n'}
        pack = build_pack(task, files)
        self.assertEqual(pack['included'][1], '/workspace/blueprints/transactions/v1.sql')
        self.assertEqual(pack['reasons']['/workspace/blueprints/transactions/v1.sql'], 'table')

    def test_unknown_import_is_ignored(self):
        # Sample: Write a module that imports durai.missing. Check the pack builds and has no missing.py.
        task = {'writePaths': ['/workspace/durai/a.py'], 'objective': 'Change a.'}
        pack = build_pack(task, {'/workspace/durai/a.py': b'import durai.missing\n', '/workspace/durai/b.py': b'X = 1\n'})
        self.assertEqual(pack['included'], ['/workspace/durai/a.py', '/workspace/durai/b.py'])

    def test_pack_records_order_reasons(self):
        # Sample: Build the funds pack. Check reasons payment_funds.py target, db.py import, and zeta.py rest. Check the pack record has them.
        pack = build_pack(ORDER_TASK, ORDER_FILES)
        self.assertEqual({path: pack['reasons'][path] for path in ('/workspace/durai/payment_funds.py', '/workspace/durai/db.py', '/workspace/durai/zeta.py')},
                         {'/workspace/durai/payment_funds.py': 'target', '/workspace/durai/db.py': 'import', '/workspace/durai/zeta.py': 'rest'})
        from durai.context_pack import pack_record
        self.assertEqual(json.loads(pack_record(pack)['reasons'])['/workspace/durai/outbox.py'], 'import')

    def test_existing_target_gets_edit_rule(self):
        # Sample: Target core_0002.sql exists. Check "write_file is refused" and the "-- Sample:" comment format.
        rules = build_pack(TASK, MIGRATION)['text'].split('## workspace-map')[0]
        self.assertIn('core_0002.sql: exists (1 lines). write_file is refused for it. Use edit_file.', rules)
        self.assertIn('"-- Sample: <concrete inputs and expected results>"', rules)

    def test_new_target_gets_write_rule(self):
        # Sample: Target core_0003.sql is new. Check "Create it once with write_file".
        task = dict(TASK, writePaths=['/workspace/migrations/core/core_0003.sql'])
        self.assertIn('core_0003.sql: new file. Create it once with write_file.', build_pack(task, MIGRATION)['text'])

    def test_pack_is_deterministic(self):
        # Sample: Build the pack twice. Check one digest. Change ledger.py. Check a new digest.
        first, second = build_pack(TASK, MIGRATION), build_pack(TASK, dict(MIGRATION))
        changed = build_pack(TASK, dict(MIGRATION, **{'/workspace/durai/ledger.py': b'def shares(lots):\n    return 0\n'}))
        self.assertEqual(first['digest'], second['digest'])
        self.assertNotEqual(first['digest'], changed['digest'])

    def test_pack_respects_character_limit(self):
        # Sample: Set the limit to 1500 characters. Check the target text stays, core_0001.sql is cut, and the map says "not in pack".
        pack = build_pack(TASK, MIGRATION, 1500)
        self.assertLessEqual(pack['characters'], 1500)
        self.assertEqual(pack['included'][0], '/workspace/migrations/core/core_0002.sql')
        self.assertIn('/workspace/migrations/core/core_0001.sql', pack['cut'])
        self.assertIn('core_0001.sql | read | ', pack['text'])
        self.assertIn('not in pack; use show_lines or read_file', pack['text'])

    def test_pack_has_no_protected_content(self):
        # Sample: Build the migration v14 pack from the fixture spec. Check no protected test name and no reference-only line.
        from durai.qualification import TEMPLATES, fixture_config, fixture_files, load
        from durai.workspace_manifest import visible
        folder = TEMPLATES / 'task-graph-tenancy-migration-v14'
        config = fixture_config(folder)
        pack = build_pack({'writePaths': config['writePaths'], 'objective': config['objective'],
                           'readPaths': ['/workspace/**'], 'deniedPaths': []}, fixture_files(folder))
        protected = b''.join(load(folder / 'protected').values()).decode('utf-8')
        names = [line.split('def ')[1].split('(')[0] for line in protected.splitlines() if line.strip().startswith('def test_')]
        self.assertTrue(names)
        self.assertFalse([name for name in names if name in pack['text']])
        shell = b''.join(fixture_files(folder).values()).decode('utf-8')
        reference = [line for line in load(folder / 'reference').popitem()[1].decode('utf-8').splitlines()
                     if len(line.strip()) > 40 and line not in shell]
        self.assertTrue(reference)
        self.assertFalse([line for line in reference if line in pack['text']])
        self.assertEqual(pack['cut'], [])

    def test_v14_binding_pins_procedure(self):
        # Sample: Bind pack v14. Check maxCharacters 60000 and four tools. Bind v13. Check None.
        procedure = context_procedure(bound('14'), 'durai_test', 'durai_runtime')
        self.assertEqual((procedure['maxCharacters'], procedure['tools']),
                         (60000, ['describe_table', 'find_function', 'show_lines', 'search_context']))
        self.assertIsNone(context_procedure(bound('13'), 'durai_test', 'durai_runtime'))

    def test_suffixed_pack_version_is_read(self):
        # Sample: Install generic pack version "10-abc" without an edit-guard record. Check a Conflict, not a ValueError.
        from durai.common import Conflict, uid
        from durai.decision_points import pinned_procedure
        from durai.packs import PackStore, generic_pack
        from durai.policy_store import record_policies
        from durai.worker_contract import resolve_contract
        store = PackStore('durai_test')
        pack = generic_pack('10')
        pack = dict(pack, version='10-' + uid()[:8], records=[item for item in pack['records'] if item['id'] != 'decision.edit-guard'])
        reference = store.install(pack)
        record_policies(reference, 'durai_test')
        identity = 'suffix-' + uid()[:8]
        store.bind(identity, [reference], 'SYNTHETIC_LOCAL')
        task = {'binding': {'bindingId': identity}, 'pins': {'contractDigest': resolve_contract(identity, database='durai_test')['digest']}}
        with self.assertRaises(Conflict):
            pinned_procedure(task, 'durai_test', 'durai_runtime', 'decision.edit-guard', 9)


class LoopPackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply migrations and checkpoint tables to durai_test.
        from durai.db import prepare
        from durai.harness import prepare_checkpoints
        prepare('durai_test')
        prepare_checkpoints('durai_test')

    def test_delivery_records_pack_key(self):
        # Sample: Run v14 attempts WRONG then FIX. Check each CONTEXT_VIEW_DELIVERED has one record key and a pack digest.
        from test_dev_loop import WRONG
        state, harness, events = run_bound('14', [[WRONG], [FIX_FROM_198]])
        self.assertEqual(state['outcome'], 'PASSED', state.get('verdict'))
        delivered = [row['payload'] for row in events if row['kind'] == 'CONTEXT_VIEW_DELIVERED']
        self.assertEqual(len(delivered), 2)
        self.assertTrue(all(len(row['records']) == 1 and len(row['packDigest']) == 64 for row in delivered))

    def test_resumed_attempt_pack_shows_start_files(self):
        # Sample: Attempt 1 books 198 shares. Attempt 2 resumes from it. Check a new pack digest for attempt 2.
        from test_dev_loop import WRONG
        state, harness, events = run_bound('14', [[WRONG], [FIX_FROM_198]])
        digests = [row['payload']['packDigest'] for row in events if row['kind'] == 'CONTEXT_VIEW_DELIVERED']
        self.assertTrue(any(row['kind'] == 'ATTEMPT_RESUMED' for row in events))
        self.assertNotEqual(digests[0], digests[1])

    def test_efficiency_event_recorded(self):
        # Sample: Run v14 attempts WRONG then FIX. Check WORKER_EFFICIENCY for each attempt with editCalls 1 and the pack digest.
        from test_dev_loop import WRONG
        state, harness, events = run_bound('14', [[WRONG], [FIX_FROM_198]])
        delivered = [row['payload']['packDigest'] for row in events if row['kind'] == 'CONTEXT_VIEW_DELIVERED']
        found = [row['payload'] for row in events if row['kind'] == 'WORKER_EFFICIENCY']
        self.assertEqual([row['packDigest'] for row in found], delivered)
        self.assertEqual([row['metrics']['editCalls'] for row in found], [1, 1])

    def test_v13_binding_has_no_pack(self):
        # Sample: Run the same attempts on v13. Check empty delivered records and no pack digest.
        from test_dev_loop import WRONG
        state, harness, events = run_bound('13', [[WRONG], [FIX_FROM_198]])
        delivered = [row['payload'] for row in events if row['kind'] == 'CONTEXT_VIEW_DELIVERED']
        self.assertTrue(delivered)
        self.assertTrue(all(row['records'] == [] and row['packDigest'] is None for row in delivered))

    def test_query_is_audited(self):
        # Sample: Attempt 1 calls describe_table("ledger.lot") and then FIX. Check one CONTEXT_QUERY with tool describe_table.
        from test_dev_loop import FIX
        state, harness, events = run_bound('14', [[('find_function', {'pattern': 'shares'}), FIX]], database_worker=True)
        queries = [row['payload'] for row in events if row['kind'] == 'CONTEXT_QUERY']
        self.assertEqual([row['tool'] for row in queries], ['find_function'])
        self.assertEqual(len(queries[0]['answerDigest']), 64)
        self.assertIn('No function matches shares.', harness.models[0].replies[0])
        found = [row['payload']['metrics'] for row in events if row['kind'] == 'WORKER_EFFICIENCY']
        self.assertEqual(found[0]['queryCalls'], 1)


class ToolTests(unittest.TestCase):
    def setUp(self):
        # Sample: Decode the migration files and index them.
        self.files = {path: content.decode('utf-8') for path, content in MIGRATION.items()}
        self.index = sql_index(MIGRATION)

    def test_describe_table(self):
        # Sample: Ask describe_table("lot"). Check the CREATE TABLE at core_0001.sql:2 and the ALTER TABLE at line 6.
        answer = describe_table(self.index, self.files, 'lot')
        self.assertIn('core_0001.sql:2\nCREATE TABLE ledger.lot(', answer)
        self.assertIn('core_0001.sql:6 ALTER TABLE ledger.lot ADD COLUMN owner text;', answer)

    def test_find_function(self):
        # Sample: Ask find_function("book_%"). Check ledger.book_lot(lot_id text, lots integer) RETURNS numeric at line 7.
        self.assertEqual(find_function(self.index, 'book_%'),
                         'FUNCTION ledger.book_lot(lot_id text, lots integer) RETURNS numeric /workspace/migrations/core/core_0001.sql:7')

    def test_show_lines_refuses_outside_path(self):
        # Sample: Ask show_lines("../private/deepseek.json", 1, 5). Check a refusal. Ask lines 2 to 3 of ledger.py. Check 2 numbered lines.
        self.assertTrue(show_lines(self.files, '../private/deepseek.json', 1, 5).startswith('Refused:'))
        self.assertEqual(show_lines(self.files, '/workspace/durai/ledger.py', 2, 3),
                         '2:     # Sample: Book 2 lots. Return 200 shares.\n3:     return lots * 100')

    def test_search_context_finds_lines(self):
        # Sample: Search "lots 100". Check ledger.py:3 and core_0001.sql:9. Search a missing word. Check "No line".
        answer = search_context(self.files, [{'id': 'P-1', 'rule': 'Keep lots whole.'}], 'lots 100')
        self.assertIn('/workspace/durai/ledger.py:3 [2 of 2 words] return lots * 100', answer)
        self.assertTrue(search_context(self.files, [], 'zebra').startswith('No line'))

    def test_search_ranks_partial_matches(self):
        # Sample: Search "graph owner role" over "graph_owner role x" and "role y". Check line 1 (3 of 3 words) before line 2 (1 of 3 words).
        answer = search_context({'/workspace/a.sql': 'role y\ngraph_owner role x\n'}, [], 'graph owner role')
        self.assertEqual(answer.splitlines(), ['/workspace/a.sql:2 [3 of 3 words] graph_owner role x',
                                               '/workspace/a.sql:1 [1 of 3 words] role y'])

    def test_search_reads_objective_and_goals(self):
        # Sample: Search "forkOf" with objective "Keep forkOf.". Check task:objective. Search "idempotent". Check task:goal.
        notes = [('task:objective', 'Keep forkOf.'), ('task:goal', 'test_projection_is_idempotent')]
        self.assertIn('task:objective:1 [1 of 1 words] Keep forkOf.', search_context(self.files, [], 'forkOf', notes=notes))
        self.assertIn('task:goal:1 [1 of 1 words] test_projection_is_idempotent', search_context(self.files, [], 'idempotent', notes=notes))

    def test_search_returns_objective_sentence(self):
        # Sample: Objective "Keep forkOf. Audit each projection once." Search "audit projection". Check objective line 2 only, after an equal workspace line.
        from durai.context_pack import task_notes
        notes = task_notes({'objective': 'Keep forkOf. Audit each projection once.'})
        answer = search_context({'/workspace/a.sql': 'audit projection\n'}, [], 'audit projection', notes=notes)
        self.assertEqual(answer.splitlines(), ['/workspace/a.sql:1 [2 of 2 words] audit projection',
                                               'task:objective:2 [2 of 2 words] Audit each projection once.'])

    def test_search_miss_names_sources(self):
        # Sample: Search "zzz". Check that the answer names the workspace, objective, goals, policies, and the protected-test notice.
        answer = search_context(self.files, [], 'zzz')
        for part in ('workspace files', 'policies', 'task objective', 'goal names', 'Protected tests are not readable'):
            self.assertIn(part, answer)

    def test_unpinned_tool_is_not_offered(self):
        # Sample: Pin describe_table only. Check one tool named describe_table.
        tools = query_tools(['describe_table'], self.index, Path(tempfile.gettempdir()), set(), [], lambda kind, payload: None)
        self.assertEqual([item.name for item in tools], ['describe_table'])


class FixtureTests(unittest.TestCase):
    def test_v14_fixture_changes_only_binding(self):
        # Sample: Compare migration v13 and v14. Check equal files and binding foundation-generic-v14.
        from durai.qualification import TEMPLATES, load
        old, new = TEMPLATES / 'task-graph-tenancy-migration-v13', TEMPLATES / 'task-graph-tenancy-migration-v14'
        for part in ('protected', 'workspace', 'reference'):
            self.assertEqual(load(old / part), load(new / part), part)
        before, after = (json.loads((folder / 'task.json').read_text(encoding='utf-8')) for folder in (old, new))
        self.assertEqual(after['binding'], {'id': 'foundation-generic-v14', 'version': '14'})
        self.assertEqual({key: value for key, value in before.items() if key not in ('binding', 'taskPrefix')},
                         {key: value for key, value in after.items() if key not in ('binding', 'taskPrefix')})


if __name__ == '__main__':
    unittest.main()
