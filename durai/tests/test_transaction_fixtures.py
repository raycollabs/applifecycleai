"""Sample: Read 22 v14 transaction templates. Check source digests, shells, protected files, variants, suites, and packs."""
import ast
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

from build_transaction_fixtures import CONTRACT_NOTE, STUB, folder_name
from durai.common import digest
from durai.context_pack import build_pack
from durai.qualification import TEMPLATES, fixture_config, fixture_files, fixture_suite
from durai.workspace_manifest import visible

CASES = json.loads((ROOT / 'qualification/phase4/contracts.json').read_text(encoding='utf-8'))


def functions(source):
    # Sample: Read "def a():\n    return 1\n". Return {"a": "def a():\n    return 1"}.
    text = source.decode('utf-8')
    return {node.name: ast.get_source_segment(text, node) for node in ast.parse(text).body if isinstance(node, ast.FunctionDef)}


FREE_FORM = {'execute_request': {2}}
CALL_FORMS = {'handler': 'handler(db, body)', 'signer': 'signer(storage_key, ttl_seconds)', 'provider': 'lookup('}


def dict_keys(node, assigned, seen):
    # Sample: Read [{'account': 'a', 'signedMinor': 5}] or a name bound to it. Return {'account', 'signedMinor'}.
    if id(node) in seen:
        return set()
    seen.add(id(node))
    found = set()
    if isinstance(node, ast.Dict):
        found |= {key.value for key in node.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)}
        for value in node.values:
            found |= dict_keys(value, assigned, seen)
    elif isinstance(node, (ast.List, ast.Tuple)):
        for item in node.elts:
            found |= dict_keys(item, assigned, seen)
    elif isinstance(node, ast.Name) and node.id in assigned:
        found |= dict_keys(assigned[node.id], assigned, seen)
    elif isinstance(node, ast.Subscript):
        found |= dict_keys(node.value, assigned, seen)
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'dict':
        found |= {keyword.arg for keyword in node.keywords if keyword.arg}
        for item in node.args:
            found |= dict_keys(item, assigned, seen)
    return found


def task_view(config):
    # Sample: Read the journal config. Return a task with its write paths, objective, read paths, and denied paths.
    return {'writePaths': config['writePaths'], 'objective': config['objective'],
            'readPaths': ['/workspace/**'], 'deniedPaths': ['/workspace/tests/**']}


class TransactionFixtureTests(unittest.TestCase):
    def test_templates_match_sources(self):
        # Sample: Read task-tx-journal-v14. Hash durai/journal.py and protected/journal.py. Check both digests equal the recorded digests.
        for case in CASES:
            for suffix in ('', '-plain'):
                config = fixture_config(TEMPLATES / folder_name(case, suffix))
                for path, value in config['sources'].items():
                    self.assertEqual(digest((ROOT / path).read_bytes()), value,
                                     path + ' changed. Run tools/build_transaction_fixtures.py again.')

    def test_shell_replaces_only_scoped_bodies(self):
        # Sample: Read the journal shell. Check post_journal and reverse_journal raise NotImplementedError. Check every other function equals durai/journal.py.
        for case in CASES:
            for module, symbols in case['scope'].items():
                shell = functions((TEMPLATES / folder_name(case, '') / 'workspace/durai' / (module + '.py')).read_bytes())
                trusted = functions((ROOT / 'durai' / (module + '.py')).read_bytes())
                self.assertEqual(set(shell), set(trusted))
                for name in trusted:
                    if name in symbols:
                        self.assertTrue(shell[name].endswith(STUB.strip()), name)
                    else:
                        self.assertEqual(shell[name], trusted[name], name)

    def test_protected_files_not_in_workspace(self):
        # Sample: Load the workspace of task-tx-events-v14. Check no path under /workspace/tests/ and no line of protected/events.py.
        for case in CASES:
            folder = TEMPLATES / folder_name(case, '')
            files = fixture_files(folder)
            self.assertFalse([path for path in files if path.startswith('/workspace/tests/')])
            text = '\n'.join(content.decode('utf-8') for content in files.values())
            protected = (ROOT / 'qualification/phase4/protected' / (case['id'] + '.py')).read_text(encoding='utf-8')
            self.assertFalse([line for line in protected.splitlines() if len(line.strip()) >= 25 and line.strip() in text])

    def test_variants_differ_only_by_contract(self):
        # Sample: Compare task-tx-seats-v14 with task-tx-seats-v14-plain. Check one extra file and the sentence "Read /workspace/public-contract.md."
        for case in CASES:
            contract, plain = TEMPLATES / folder_name(case, ''), TEMPLATES / folder_name(case, '-plain')
            first, second = fixture_files(contract), fixture_files(plain)
            self.assertEqual(set(first) - set(second), {'/workspace/public-contract.md'})
            self.assertEqual({path: first[path] for path in second}, second)
            one, two = fixture_config(contract), fixture_config(plain)
            self.assertEqual(one['objective'], two['objective'] + CONTRACT_NOTE)
            self.assertEqual(fixture_suite(contract), fixture_suite(plain))

    def test_protected_include_loads_suite(self):
        # Sample: Load the suite of task-tx-location-v14. Check tests/seed/99.sql equals protected/seed.sql and tests/test_protected.py equals protected/location.py.
        suite = fixture_suite(TEMPLATES / 'task-tx-location-v14')
        self.assertEqual(suite['/workspace/tests/seed/99.sql'], (ROOT / 'qualification/phase4/protected/seed.sql').read_bytes())
        self.assertEqual(suite['/workspace/tests/test_protected.py'], (ROOT / 'qualification/phase4/protected/location.py').read_bytes())
        self.assertEqual(suite['/workspace/tests/__init__.py'], b'')
        self.assertEqual(len([path for path in suite if path.startswith('/workspace/tests/seed/')]), 10)

    def test_objective_names_expected_errors(self):
        # Sample: Read task-tx-journal-v14 and its suite that expects Conflict. Check the objective names Conflict, so readiness returns READY.
        from durai.error_contract import unnamed_errors
        for case in CASES:
            for suffix in ('', '-plain'):
                folder = TEMPLATES / folder_name(case, suffix)
                self.assertEqual(unnamed_errors(fixture_config(folder)['objective'], fixture_suite(folder)), [], folder.name)

    def test_output_fields_are_public(self):
        # Sample: Read task-tx-location-v14-plain. Check that the objective names location_id. Read task-tx-location-v14. Check the "Output fields" section.
        self.assertIn('location_id', fixture_config(TEMPLATES / 'task-tx-location-v14-plain')['objective'])
        self.assertIn('findings', fixture_config(TEMPLATES / 'task-tx-settlement-v14-plain')['objective'])
        self.assertIn('eventId', fixture_config(TEMPLATES / 'task-tx-events-v14-plain')['objective'])
        self.assertIn('replayed', fixture_config(TEMPLATES / 'task-tx-idempotency-v14-plain')['objective'])
        self.assertIn('SEAT_UNAVAILABLE', fixture_config(TEMPLATES / 'task-tx-seats-v14-plain')['objective'])
        self.assertIn('INSUFFICIENT_REFUND_BALANCE', fixture_config(TEMPLATES / 'task-tx-funds-v14-plain')['objective'])
        self.assertIn('submit(operation)', fixture_config(TEMPLATES / 'task-tx-funds-v14-plain')['objective'])
        for case in CASES:
            text = (TEMPLATES / folder_name(case, '') / 'workspace/public-contract.md').read_text(encoding='utf-8')
            self.assertIn('## Output fields', text)
            self.assertIn('location_id', text)

    def test_input_shapes_are_public(self):
        # Sample: Read the settlement plain objective. Check it names (kind, operationId) and the 7 statement line keys.
        objective = fixture_config(TEMPLATES / 'task-tx-settlement-v14-plain')['objective']
        self.assertIn('(kind, operationId)', objective)
        for key in ('kind', 'operationId', 'currency', 'amountMinor', 'feeMinor', 'netMinor', 'providerReference'):
            self.assertIn(key, objective)
        self.assertIn('Input fields', (TEMPLATES / 'task-tx-settlement-v14/workspace/public-contract.md').read_text(encoding='utf-8'))

    def test_literal_dict_keys_are_public(self):
        # Sample: Read protected/journal.py. Find lines = [{'account': ..., 'signedMinor': ...}] passed to post_journal. Check both keys in the journal plain objective.
        for case in CASES:
            scoped = {symbol for symbols in case['scope'].values() for symbol in symbols}
            objective = fixture_config(TEMPLATES / folder_name(case, '-plain'))['objective']
            tree = ast.parse((ROOT / 'qualification/phase4/protected' / (case['id'] + '.py')).read_text(encoding='utf-8'))
            assigned = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    assigned[node.targets[0].id] = node.value
            for call in ast.walk(tree):
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr in scoped:
                    for index, argument in enumerate(call.args[:4]):
                        if index in FREE_FORM.get(call.func.attr, ()):
                            continue
                        for key in sorted(dict_keys(argument, assigned, set())):
                            self.assertIn(key, objective, case['id'] + ' ' + call.func.attr + ' key ' + key)

    def test_callable_call_forms_are_public(self):
        # Sample: Read protected/downloads.py. Find signer passed to download. Check the downloads plain objective names signer(storage_key, ttl_seconds) and positional.
        for case in CASES:
            objective = fixture_config(TEMPLATES / folder_name(case, '-plain'))['objective']
            tree = ast.parse((ROOT / 'qualification/phase4/protected' / (case['id'] + '.py')).read_text(encoding='utf-8'))
            for module, symbols in case['scope'].items():
                reference = ast.parse((ROOT / 'durai' / (module + '.py')).read_text(encoding='utf-8'))
                params = {node.name: [arg.arg for arg in node.args.args] for node in reference.body
                          if isinstance(node, ast.FunctionDef) and node.name in symbols}
                for call in ast.walk(tree):
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr in params:
                        for index, _ in enumerate(call.args):
                            name = params[call.func.attr][index] if index < len(params[call.func.attr]) else ''
                            if name in CALL_FORMS:
                                self.assertIn(CALL_FORMS[name], objective, case['id'] + ' ' + name)
                                self.assertIn('positional', objective, case['id'] + ' ' + name)

    def test_pack_builds_for_each_task(self):
        # Sample: Build the pack for task-tx-funds-v14. Check at most 60000 characters and /workspace/durai/payment_funds.py first in the file text. For events, check outbox.py and inbox.py first.
        for case in CASES:
            folder = TEMPLATES / folder_name(case, '')
            config = fixture_config(folder)
            task = task_view(config)
            pack = build_pack(task, visible(task, fixture_files(folder)), 60000, [config['failingAcceptanceGoal']])
            self.assertLessEqual(pack['characters'], 60000)
            self.assertEqual(set(pack['included'][:len(config['writePaths'])]), set(config['writePaths']))

    def test_imports_kept_in_transaction_packs(self):
        # Sample: Build the pack of task-tx-funds-v14. Check that db.py, idempotency.py, outbox.py, and payment_intents.py are included.
        from durai.context_pack import imported_paths
        for case in CASES:
            folder = TEMPLATES / folder_name(case, '')
            config = fixture_config(folder)
            task = task_view(config)
            files = visible(task, fixture_files(folder))
            pack = build_pack(task, files, 60000, [config['failingAcceptanceGoal']])
            self.assertEqual([path for path in imported_paths(task, files) if path not in pack['included']], [], folder.name)
        funds = fixture_config(TEMPLATES / 'task-tx-funds-v14')
        task = task_view(funds)
        names = {Path(path).name for path in imported_paths(task, visible(task, fixture_files(TEMPLATES / 'task-tx-funds-v14')))}
        self.assertTrue({'db.py', 'idempotency.py', 'outbox.py', 'payment_intents.py'} <= names)


if __name__ == '__main__':
    unittest.main()
