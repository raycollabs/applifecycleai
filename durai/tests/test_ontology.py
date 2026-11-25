"""Executable ontology and engineering invariants on durai_test only."""
import copy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import psycopg
from durai.common import digest, uid, Conflict
from durai.db import transaction
from durai.engineering import Engineering, validate_domain, check_walkthroughs
from durai.packs import PackStore, generic_pack, validate_pack

DATABASE = 'durai_test'
SPEC = {'applicationName': 'Exchange positions', 'quantityUnit': 'shares',
        'signedLots': True, 'correctionDirection': 'REMOVE_LOTS'}


class PackTests(unittest.TestCase):
    def setUp(self):
        # Sample: Use a new G-1 test identity. Do not reset the approved local target.
        self.store = PackStore(DATABASE)
        self.document = generic_pack()
        self.document['id'] = 'test.generic.' + uid()
        self.reference = self.store.install(self.document)

    def test_matrix_and_digest_integrity(self):
        # Sample: Check 18 cells. Change one policy byte. Refuse the changed digest.
        self.assertEqual(len({(r['layer'], r['type']) for r in self.document['records']}), 18)
        changed = copy.deepcopy(self.document)
        changed['records'][0]['body']['changed'] = True
        with self.assertRaises(Conflict):
            validate_pack(changed)

    def test_pack_versions_cannot_change(self):
        # Sample: Replay G-1 once. Attempt a new body and a direct SQL edit. Refuse both.
        self.assertEqual(self.store.install(self.document), self.reference)
        changed = copy.deepcopy(self.document)
        changed['reviewState'] = 'CHANGED'
        with self.assertRaises(Conflict):
            self.store.install(changed)
        with self.assertRaises(psycopg.errors.CheckViolation):
            with transaction('postgres', DATABASE) as db:
                db.execute('UPDATE durai_ontology.pack SET document=%s WHERE id=%s', (json.dumps(changed), self.document['id']))

    def test_foreign_scope_cannot_read_application_pack(self):
        # Sample: Store private trading ontology. Query it with the foreign login.
        application = copy.deepcopy(self.document)
        application.update(id='test.application.' + uid(), kind='APPLICATION')
        ref = self.store.install(application)
        with self.assertRaises(KeyError):
            PackStore(DATABASE, 'durai_foreign').get(ref)
        with transaction('durai_foreign', DATABASE) as db:
            self.assertEqual(db.execute('SELECT count(*) AS n FROM durai_ontology.record WHERE pack_id=%s', (ref['id'],)).fetchone()['n'], 0)

    def test_reference_pack_cannot_execute(self):
        # Sample: Bind G-1 for reference. Request a coding run. Check refusal.
        identity = 'reference-' + uid()
        self.store.bind(identity, [self.reference])
        with self.assertRaisesRegex(Conflict, 'Reference-only'):
            Engineering(DATABASE).create(identity, SPEC, {'source': 'fixture'})

    def test_fork_preserves_upstream_and_pinned_runs(self):
        # Sample: Bind G-1. Fork G-2. Rebind new work. Keep revision 1 unchanged.
        identity = 'fork-' + uid()
        first = self.store.bind(identity, [self.reference], 'SYNTHETIC_LOCAL')
        body = copy.deepcopy(self.document['records'][0]['body'])
        body['commentPreference'] = 'Show a 200-share example.'
        fork = self.store.fork(self.reference, 'test.fork.' + uid(), '2', self.document['records'][0]['id'], body)
        second = self.store.bind(identity, [fork], 'SYNTHETIC_LOCAL')
        self.assertEqual((first['revision'], second['revision']), (1, 2))
        self.assertEqual(self.store.resolve(identity, 1)['digest'], first['digest'])
        self.assertEqual(self.store.get(self.reference), self.document)

    def test_archive_rejects_tampered_or_escaping_content(self):
        # Sample: Export G-1. Replace its archive path with ../escape. Reject import.
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'pack.sqlite'
            checksum = self.store.export(self.reference, path)
            imported = self.store.import_archive(path, checksum)
            self.assertEqual(imported['scope'], 'trading')
            self.assertEqual(self.store.get(imported), self.document)
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("UPDATE files SET path='../escape'")
            with self.assertRaises(Conflict):
                self.store.import_archive(path, checksum)

    def test_graph_matches_canonical_records(self):
        # Sample: Project G-1 twice. Keep one node per canonical record and six links.
        result = self.store.project(self.reference)
        self.assertEqual(result['records'], len(self.document['records']))
        self.assertEqual(self.store.project(self.reference), result)

    def test_foreign_reader_cannot_invoke_operator_writes(self):
        # Sample: Use the foreign reader. Refuse an install or binding under trading authority.
        foreign = PackStore(DATABASE, 'durai_foreign')
        with self.assertRaises(PermissionError):
            foreign.install(self.document)
        with self.assertRaises(PermissionError):
            foreign.bind('foreign-write', [self.reference])


class ProcedureTests(unittest.TestCase):
    def setUp(self):
        # Sample: Bind a fresh G-1 run with context A. Keep each goal independent.
        self.store = PackStore(DATABASE)
        self.ref = self.store.install(generic_pack())
        identity = 'procedure-' + uid()
        self.store.bind(identity, [self.ref], 'SYNTHETIC_LOCAL')
        self.engine = Engineering(DATABASE)
        self.run = self.engine.create(identity, SPEC, {'sourceDigest': digest(b'baseline')})

    def design(self):
        # Sample: Record the approved brief for source A. Permit shell preparation.
        return self.engine.record(self.run, 'DESIGN_READY', self.engine.evidence(self.run, briefDigest=digest({'goal': '200 shares'})))

    def shell(self):
        # Sample: Compile source A and run its empty input. Record successful smoke.
        self.design()
        return self.engine.record(self.run, 'EXECUTION_SHELL_READY', self.engine.evidence(self.run,
            compiled=True, smokePassed=True, baselineDigest=digest(b'baseline')))

    def test_missing_domain_rule_blocks_design(self):
        # Sample: Remove correction direction. Ask for readiness. Require clarification.
        spec = dict(SPEC)
        del spec['correctionDirection']
        with self.assertRaisesRegex(Conflict, 'NEEDS_CLARIFICATION'):
            validate_domain(spec)

    def test_shell_precedes_business_red(self):
        # Sample: Record design. Request implementation before runnable-shell evidence.
        self.design()
        with self.assertRaisesRegex(Conflict, 'gate order'):
            self.engine.record(self.run, 'IMPLEMENTATION_READY', self.engine.evidence(self.run))

    def test_infrastructure_failure_is_not_business_red(self):
        # Sample: Pass shell smoke. Submit an import failure. Refuse business-red.
        self.shell()
        with self.assertRaises(Conflict):
            self.engine.record(self.run, 'BASELINE_TEST_FAILURE_CONFIRMED', self.engine.evidence(self.run,
                category='IMPORT_ERROR', baselineDigest=digest(b'baseline'), expected=200, observed=2), 'durai_evaluator')
        self.assertEqual(self.engine.get(self.run)['state'], 'EXECUTION_SHELL_READY')

    def test_changed_context_invalidates_readiness(self):
        # Sample: Bind the run to source A. Supply evidence for source B. Refuse it.
        evidence = self.engine.evidence(self.run, briefDigest='brief')
        evidence['contextDigest'] = digest(b'other')
        with self.assertRaisesRegex(Conflict, 'pinned context'):
            self.engine.record(self.run, 'DESIGN_READY', evidence)

    def test_passing_candidate_has_no_release_authority(self):
        # Sample: Pass the ordered five gates. Keep WAITING_FOR_REVIEW and no approval.
        self.shell()
        suite = digest({'open': 200})
        red = self.engine.evidence(self.run, category='BUSINESS_ASSERTION', baselineDigest=digest(b'baseline'),
                                   suiteDigest=suite, assertion='shares == 200', expected=200, observed=2)
        self.engine.record(self.run, 'BASELINE_TEST_FAILURE_CONFIRMED', red, 'durai_evaluator')
        self.engine.record(self.run, 'IMPLEMENTATION_READY', self.engine.evidence(self.run, suiteDigest=suite))
        result = self.engine.record(self.run, 'WAITING_FOR_REVIEW', self.engine.evidence(self.run,
            suiteDigest=suite, candidateDigest=digest(b'candidate'), verdict='PASS', checks=[{'passed': True}]), 'durai_evaluator')
        self.assertEqual(result['state'], 'WAITING_FOR_REVIEW')
        self.assertFalse(result['document']['releaseAuthority'])

    def test_walkthrough_coverage_reports_missing_function(self):
        # Sample: Remove the example from book(). Check that the gate names book.
        with self.assertRaisesRegex(Conflict, 'book'):
            check_walkthroughs('def book():\n    return 200\n')
        self.assertEqual(check_walkthroughs('def book():\n    # Sample: Book 2 lots of 100. Check 200 shares.\n    return 200\n')['functions'], 1)
