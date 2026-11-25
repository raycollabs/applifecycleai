"""Sample: Read 11 blueprint entries. Check 22 typed patterns, one pack version, one binding, and the funds brief."""
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from durai.advisor_brief import build_brief, interface_lines
from durai.advisor_select import framework_patterns, select_patterns
from durai.blueprint_patterns import CASES, catalog_patterns, load_catalog, transaction_pack, validate_catalog
from durai.common import Conflict, canonical, uid
from durai.context_pack import context_procedure
from durai.db import prepare
from durai.decision_points import pinned_procedure
from durai.ontology_patterns import validate_pattern
from durai.packs import PackStore, generic_pack
from durai.qualification import TEMPLATES, fixture_config, fixture_files, fixture_suite
from durai.architecture_defaults import architecture_defaults_pack, validate_preference
from durai.policy_store import record_policies
from durai.worker_contract import resolve_contract
from durai.workspace_manifest import visible
from tools.build_transaction_patterns import BINDING, install
from tools.dry_run_advisor_briefs import task_view

DATABASE = 'durai_test'
ROOT = Path(__file__).resolve().parents[1]
KEY = re.compile(r"\[\s*'([A-Za-z_]\w*)'\s*\]")
LITERAL = re.compile(r"'([A-Za-z_]\w*)'\s*:")
ALIAS = re.compile(r"\bAS\s+(\w+)", re.I)
STATE = {}


def installed():
    # Sample: Install the transaction pack in durai_test twice with rebind. Return one summary with binding foundation-transaction-v14.
    if 'summary' not in STATE:
        prepare(DATABASE)
        STATE['summary'] = install(transaction_pack(generic_pack('14')), DATABASE, rebind=True)
    return STATE['summary']


def framework():
    # Sample: Read binding foundation-transaction-v14 for profile transaction. Return its 36 patterns.
    installed()
    return framework_patterns(BINDING, 'transaction', DATABASE)


def task_for(case):
    # Sample: Read task-tx-funds-v14. Return its task view, its visible files, and its folder.
    folder = TEMPLATES / ('task-tx-' + case + '-v14')
    task = task_view(fixture_config(folder))
    return task, visible(task, fixture_files(folder)), folder


def protected_names(folder):
    # Sample: Read the protected funds test. Return the result keys it reads and the provider methods it calls.
    suite = fixture_suite(folder)
    test = suite['/workspace/tests/test_protected.py'].decode('utf-8')
    support = suite['/workspace/tests/support.py'].decode('utf-8')
    keys = set(KEY.findall(test)) - set(LITERAL.findall(test)) - set(ALIAS.findall(test))
    used = set(re.findall(r'\b([A-Z]\w*)\(\)', test))
    methods = set()
    for part in re.split(r'^class ', support, flags=re.M)[1:]:
        if part.split('(')[0].split(':')[0].strip() in used:
            methods |= set(re.findall(r'^    def ([a-z]\w*)\(', part, re.M)) | set(re.findall(r'^    ([a-z]\w*) = \w+$', part, re.M))
    return sorted(keys | methods)


def strings(value):
    # Sample: Read {"a": ["x", "y"]}. Return the texts a, x, and y.
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for key, item in value.items() for text in [key] + strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in strings(item)]
    return []


class BlueprintCatalogTests(unittest.TestCase):
    def test_catalog_has_eleven_cases(self):
        # Sample: Read the catalog. Check the IDs idempotency, events, seats, inventory, charge, funds, settlement, journal, jobs, downloads, location.
        catalog = load_catalog()
        self.assertEqual([item['id'] for item in catalog['entries']], list(CASES))
        self.assertEqual(len(CASES), 11)
        self.assertEqual(len(validate_catalog(catalog)), 64)

    def test_patterns_validate(self):
        # Sample: Build 22 patterns. Check validate_pattern for each and validate_preference for 11 defaults.
        selected = catalog_patterns()
        self.assertEqual(len(selected), 22)
        defaults = [item for item in selected if item['strength'] == 'DEFAULT']
        self.assertEqual(len(defaults), 11)
        for item in selected:
            validate_pattern(item)
            self.assertTrue(item['requires'] and item['provenance']['catalogDigest'] and item['provenance']['sourceDigests'])
        by_id = {item['id']: item for item in selected}
        for item in defaults:
            validate_preference(item)
            required = by_id[item['relatedPolicyId']]
            self.assertEqual((required['strength'], required['relatedPolicyId']), ('REQUIRED', item['id']))
            self.assertTrue(required['invariants'] and required['interface']['functions'])

    def test_sources_match_digests(self):
        # Sample: Change one byte of blueprints/payments/v2.sql in a copy. Check the funds digest check fails.
        catalog = load_catalog()
        with tempfile.TemporaryDirectory() as folder:
            for source in catalog['sources'].values():
                target = Path(folder) / source['path']
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / source['path'], target)
            validate_catalog(catalog, folder)
            with (Path(folder) / 'blueprints/payments/v2.sql').open('ab') as stream:
                stream.write(b' ')
            with self.assertRaises(Conflict) as caught:
                validate_catalog(catalog, folder)
            self.assertIn('blueprints/payments/v2.sql', str(caught.exception))
            with self.assertRaises(Conflict):
                catalog_patterns(catalog, folder)

    def test_no_protected_lines(self):
        # Sample: Read protected/funds.py. Check no line of 25 or more characters in the pack text.
        catalog_text = (ROOT / 'design/transaction-blueprints-v1.json').read_text(encoding='utf-8')
        pack = transaction_pack(generic_pack('14'))
        pack_text = canonical(pack).decode('utf-8') + '\n' + '\n'.join(strings(pack))
        files = sorted((ROOT / 'qualification/phase4/protected').glob('*.py'))
        self.assertGreaterEqual(len(files), 12)
        for path in files:
            lines = {line.strip() for line in path.read_text(encoding='utf-8').splitlines() if len(line.strip()) >= 25}
            self.assertTrue(lines, path.name)
            for line in lines:
                self.assertNotIn(line, catalog_text, path.name)
                self.assertNotIn(line, pack_text, path.name)


class BlueprintPackTests(unittest.TestCase):
    def test_pack_keeps_v14_procedures(self):
        # Sample: Bind foundation-transaction-v14 in durai_test. Check context_procedure returns maxCharacters 60000 and four tools.
        summary = installed()
        self.assertRegex(summary['reference']['version'], r'^14-tx-[0-9a-f]{16}$')
        task = {'binding': {'bindingId': BINDING},
                'pins': {'contractDigest': resolve_contract(BINDING, database=DATABASE)['digest']}}
        procedure = context_procedure(task, DATABASE, 'durai_runtime')
        self.assertEqual((procedure['maxCharacters'], procedure['tools']),
                         (60000, ['describe_table', 'find_function', 'show_lines', 'search_context']))
        self.assertTrue(pinned_procedure(task, DATABASE, 'durai_runtime', 'decision.failure-triage', 10))
        self.assertTrue(pinned_procedure(task, DATABASE, 'durai_runtime', 'decision.edit-guard', 9))

    def test_rebind_creates_next_revision(self):
        # Sample: Install pack P-1 and bind B-1. Install P-2 with --rebind. Check B-1 revision 2 pins P-2 and revision 1 still pins P-1.
        prepare(DATABASE)
        name = 'rebind-' + uid()[:8]
        first = install(transaction_pack(generic_pack('14')), DATABASE, binding=name)
        catalog = load_catalog()
        catalog['entries'][0]['rule'] += ' Check the retained outcome.'
        second_pack = transaction_pack(generic_pack('14'), catalog)
        with self.assertRaises(Conflict):
            install(second_pack, DATABASE, binding=name)
        second = install(second_pack, DATABASE, rebind=True, binding=name)
        self.assertNotEqual(first['reference']['version'], second['reference']['version'])
        store = PackStore(DATABASE)
        self.assertEqual((first['bindingRevision'], second['bindingRevision']), (1, 2))
        self.assertEqual(store.resolve(name, 1)['document']['packs'], [first['reference']])
        self.assertEqual(store.resolve(name, 2)['document']['packs'], [second['reference']])
        self.assertEqual(install(second_pack, DATABASE, binding=name)['bindingRevision'], 2)

    def test_install_leaves_other_bindings(self):
        # Sample: Record digests of foundation-generic-v14 and foundation-exchange-v2. Install. Check equal digests.
        prepare(DATABASE)
        store = PackStore(DATABASE)
        names = {}
        for label, document in (('generic', generic_pack('14')), ('exchange', architecture_defaults_pack(generic_pack('10')))):
            reference = store.install(document)
            record_policies(reference, DATABASE)
            names[label] = 'before-' + label + '-' + uid()[:8]
            store.bind(names[label], [reference], 'SYNTHETIC_LOCAL')
        before = {label: store.resolve(name)['digest'] for label, name in names.items()}
        packs = [dict(row) for row in store.discover()]
        again = install(transaction_pack(generic_pack('14')), DATABASE, rebind=True)
        self.assertEqual(again['binding'], BINDING)
        self.assertEqual({label: store.resolve(name)['digest'] for label, name in names.items()}, before)
        self.assertEqual([row for row in store.discover() if row in packs], packs)
        self.assertEqual(install(transaction_pack(generic_pack('14')), DATABASE, rebind=True)['bindingDigest'], again['bindingDigest'])


class BlueprintSelectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Install the transaction pack in durai_test. Read its 36 patterns for profile transaction.
        cls.framework = framework()

    def test_funds_selects_funds_blueprint_first(self):
        # Sample: Select for task-tx-funds-v14. Check blueprint.funds.default first.
        task, files, _ = task_for('funds')
        selection = select_patterns(task, files, self.framework)
        self.assertEqual(selection['patternIds'][0], 'blueprint.funds.default')
        self.assertEqual(selection['patternIds'][1], 'blueprint.funds.correctness')

    def test_blueprint_needs_written_module(self):
        # Sample: Select for task-tx-funds-v14. Check no blueprint.charge or blueprint.settlement pattern.
        task, files, _ = task_for('funds')
        ids = select_patterns(task, files, self.framework)['patternIds']
        self.assertEqual([i for i in ids if i.startswith(('blueprint.charge', 'blueprint.settlement'))], [])
        self.assertIn('blueprint.funds.default', ids)

    def test_requires_adds_companions(self):
        # Sample: Select for funds. Check architecture.idempotency.default in the selection.
        task, files, _ = task_for('funds')
        ids = select_patterns(task, files, self.framework)['patternIds']
        self.assertIn('architecture.idempotency.default', ids)
        self.assertGreater(ids.index('architecture.idempotency.default'), ids.index('blueprint.funds.correctness'))

    def test_interfaces_cover_protected_reads(self):
        # Sample: Read keys outcome, operation, operationId, state, refunded and method submit from protected/funds.py. Check each name in the rendered funds PLAN brief of both variants.
        missing, checked = {}, 0
        folders = sorted(TEMPLATES.glob('task-tx-*-v14*'))
        self.assertEqual(len(folders), 22)
        for folder in folders:
            task = task_view(fixture_config(folder))
            files = visible(task, fixture_files(folder))
            selection = select_patterns(task, files, self.framework)
            text = build_brief(task, files, selection, 'PLAN', limit=4000)['text']
            names = protected_names(folder)
            checked += len(names)
            gone = [name for name in names if not re.search(r'\b' + name + r'\b', text)]
            if gone:
                missing[folder.name] = gone
        self.assertGreater(checked, 40)
        self.assertEqual(missing, {})


class BlueprintBriefTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Select patterns for the funds task from binding foundation-transaction-v14.
        cls.task, cls.files, _ = task_for('funds')
        cls.selection = select_patterns(cls.task, cls.files, framework())

    def interfaces(self, text):
        # Sample: Read a brief. Return the text between "## Interfaces" and the next section.
        start = text.index('## Interfaces') + len('## Interfaces\n')
        return text[start:text.index('\n\n## ', start)]

    def test_brief_has_interfaces_section(self):
        # Sample: Build the funds PLAN brief. Check "create_operation" and "INSUFFICIENT_REFUND_BALANCE" in the Interfaces section.
        text = build_brief(self.task, self.files, self.selection, 'PLAN')['text']
        section = self.interfaces(text)
        for name in ('create_operation', 'INSUFFICIENT_REFUND_BALANCE', 'provider.submit(operation)', 'provider.lookup(operation)', 'TimeoutError'):
            self.assertIn(name, section)
        self.assertLess(text.index('## Problem'), text.index('## Interfaces'))
        self.assertLess(text.index('## Interfaces'), text.index('## Required rules'))

    def test_interfaces_cut_last(self):
        # Sample: Give a 40000-character head. Check the Interfaces section is complete and the brief is 4000 characters or less.
        brief = build_brief(self.task, self.files, self.selection, 'RETRY', [{'verdict': 'FAIL', 'feedback': 'x' * 2000, 'goals': []}],
                            head='x' * 40000)
        self.assertLessEqual(brief['characters'], 4000)
        self.assertEqual(self.interfaces(brief['text']), '\n'.join(interface_lines(self.selection['patterns'])))
        self.assertIn('## Question', brief['text'])


if __name__ == '__main__':
    unittest.main()
