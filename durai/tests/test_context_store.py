"""Sample: Store plan P-1. Race two writers. Check distinct records and unchanged evidence."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import psycopg
from psycopg.types.json import Jsonb
from durai.common import Conflict, digest, uid
from durai.context_store import ContextStore
from durai.context_transfer import import_plan
from durai.db import connect, prepare, transaction

DATABASE = 'durai_test'


def plan(store=None):
    # Sample: Open a unique plan. Append its first immutable text revision.
    store = store or ContextStore(DATABASE)
    identity = 'context-test-' + uid()[:8]
    store.open_plan(identity, identity)
    text = 'Reserve a seat for ten minutes.'
    record = store.append(identity, 'plan', {'text': text, 'sourcePath': 'synthetic.md',
        'textDigest': hashlib.sha256(text.encode()).hexdigest(), 'previousRevisionDigest': None}, 'initial-plan')
    return store, identity, record


def status(record, state='RUNNING'):
    # Sample: Bind RUNNING phase 1 to the exact plan revision digest.
    return {'phase': '1', 'state': state, 'nextAction': 'Run scoped tests.', 'planRevisionDigest': record['digest']}


class ContextTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply the dedicated context migration to durai_test only.
        prepare(DATABASE)

    def setUp(self):
        # Sample: Give each test its own plan and initial revision.
        self.store, self.plan, self.revision = plan()


class ContextStoreTests(ContextTestCase):
    def test_append_assigns_next_sequence(self):
        # Sample: Append two proposed decisions. Check decision/001 and decision/002.
        value = {'decision': 'Use a bounded retry.', 'alternatives': [], 'evidence': [], 'authority': 'PROPOSED'}
        one = self.store.append(self.plan, 'decision', value, 'one')
        two = self.store.append(self.plan, 'decision', value, 'two')
        self.assertEqual([one['key'], two['key']], ['decision/001', 'decision/002'])

    def test_concurrent_appends_do_not_collide(self):
        # Sample: Race 8 writers on one plan. Check 8 distinct progress numbers.
        def append(number):
            # Sample: Append writer 3 progress with request writer-3. Return its assigned key.
            return ContextStore(DATABASE).append(self.plan, 'progress',
                {'step': 'Write ' + str(number), 'result': 'PASS', 'evidence': []}, 'writer-' + str(number))['key']
        with ThreadPoolExecutor(max_workers=8) as pool:
            keys = list(pool.map(append, range(8)))
        self.assertEqual(sorted(keys), ['progress/' + str(n).zfill(3) for n in range(1, 9)])

    def test_history_records_are_immutable(self):
        # Sample: Update and delete progress/001 as the table owner. Check trigger refusal.
        row = self.store.append(self.plan, 'progress', {'step': 'Run tests', 'result': 'PASS', 'evidence': []}, 'p')
        for command in ("UPDATE durai_context.record SET value='{}' WHERE plan_id=%s AND key=%s",
                        'DELETE FROM durai_context.record WHERE plan_id=%s AND key=%s'):
            with self.assertRaises(psycopg.errors.CheckViolation):
                with transaction('postgres', DATABASE) as db:
                    db.execute(command, (self.plan, row['key']))
        self.assertEqual(self.store.read(self.plan, row['key'])['digest'], row['digest'])

    def test_status_needs_current_revision(self):
        # Sample: Save revision 1 and then 2. Refuse another write with revision 1.
        self.store.set_status(self.plan, status(self.revision), 0, 's1')
        current = self.store.set_status(self.plan, dict(status(self.revision), phase='2'), 1, 's2')
        with self.assertRaises(Conflict):
            self.store.set_status(self.plan, status(self.revision), 1, 'stale')
        self.assertEqual(self.store.read(self.plan, 'status')['digest'], current['digest'])

    def test_status_history_is_immutable(self):
        # Sample: Save two status revisions. Check the digest link and refused history edits.
        one = self.store.set_status(self.plan, status(self.revision), 0, 's1')
        two = self.store.set_status(self.plan, dict(status(self.revision), phase='2'), 1, 's2')
        history = self.store.history(self.plan)
        self.assertEqual([r['revision'] for r in history], [1, 2])
        self.assertEqual(two['previous_digest'], one['digest'])
        with self.assertRaises(psycopg.errors.CheckViolation):
            with transaction('postgres', DATABASE) as db:
                db.execute('DELETE FROM durai_context.status_revision WHERE plan_id=%s', (self.plan,))

    def test_old_status_request_key_cannot_be_reused(self):
        # Sample: Save status requests K-1 and K-2. Refuse a progress record with K-1.
        self.store.set_status(self.plan, status(self.revision), 0, 'K-1')
        self.store.set_status(self.plan, dict(status(self.revision), phase='2'), 1, 'K-2')
        with self.assertRaises(Conflict):
            self.store.append(self.plan, 'progress', {'step': 'Reuse K-1', 'result': 'PASS', 'evidence': []}, 'K-1')

    def test_historical_status_reference_resolves(self):
        # Sample: Save status 1 and status 2. Reference the immutable status 1 digest.
        old = self.store.set_status(self.plan, status(self.revision), 0, 's1')
        self.store.set_status(self.plan, dict(status(self.revision), phase='2'), 1, 's2')
        row = self.store.append(self.plan, 'evidence', {'references': [{'kind': 'record', 'id': 'status', 'digest': old['digest']}]}, 'old-status')
        self.assertEqual(row['key'], 'evidence/001')

    def test_plan_revision_keeps_history(self):
        # Sample: Add corrected plan text. Keep the original and its digest link.
        text = 'Reserve a seat for five minutes.'
        second = self.store.append(self.plan, 'plan', {'text': text, 'sourcePath': 'synthetic.md',
            'textDigest': hashlib.sha256(text.encode()).hexdigest(), 'previousRevisionDigest': self.revision['digest']}, 'p2')
        self.assertEqual(second['key'], 'plan/rev/2')
        self.assertEqual(second['previous_digest'], self.revision['digest'])
        self.assertEqual(self.store.read(self.plan, 'plan/rev/1')['value']['text'], self.revision['value']['text'])

    def test_repeated_request_is_idempotent(self):
        # Sample: Repeat progress request K-1. Refuse K-1 with a different result.
        value = {'step': 'Run tests', 'result': 'PASS', 'evidence': []}
        one = self.store.append(self.plan, 'progress', value, 'K-1')
        self.assertEqual(one, self.store.append(self.plan, 'progress', value, 'K-1'))
        with self.assertRaises(Conflict):
            self.store.append(self.plan, 'progress', dict(value, result='FAIL'), 'K-1')
        first = self.store.set_status(self.plan, status(self.revision), 0, 'status-one')
        self.store.set_status(self.plan, dict(status(self.revision), phase='2'), 1, 'status-two')
        self.assertEqual(first, self.store.set_status(self.plan, status(self.revision), 0, 'status-one'))
        self.assertEqual(self.store.read(self.plan, 'status')['revision'], 2)

    def test_invalid_value_is_refused(self):
        # Sample: Submit a decision without evidence. Check no new sequence is consumed.
        before = self.store.summary(self.plan)['sequence']
        with self.assertRaises(Conflict):
            self.store.append(self.plan, 'decision', {'decision': 'Retry', 'alternatives': [], 'authority': 'PROPOSED'}, 'bad')
        self.assertEqual(self.store.summary(self.plan)['sequence'], before)

    def test_other_application_cannot_read(self):
        # Sample: Read and search the trading plan as the foreign login. Check no content.
        foreign = ContextStore(DATABASE, 'durai_foreign')
        self.assertEqual(foreign.read(self.plan), [])
        self.assertEqual(foreign.search(self.plan, 'seat')['results'], [])
        with self.assertRaises(KeyError):
            foreign.read(self.plan, 'plan/rev/1')
        with self.assertRaises(KeyError):
            foreign.append(self.plan, 'progress', {'step': 'Foreign write', 'result': 'PASS', 'evidence': []}, 'bad')

    def test_evidence_reference_must_resolve(self):
        # Sample: Reference a missing receipt digest. Check refusal.
        with self.assertRaises(Conflict):
            self.store.append(self.plan, 'evidence', {'references': [{'kind': 'receipt', 'id': 'missing.json', 'digest': '0' * 64}]}, 'missing')
        good = self.store.append(self.plan, 'evidence', {'references': [{'kind': 'record', 'id': self.revision['key'], 'digest': self.revision['digest']}]}, 'good')
        self.assertEqual(good['key'], 'evidence/001')

    def test_resolution_requires_finding(self):
        # Sample: Resolve a missing finding, then resolve a real one. Check only the real resolution is stored.
        value = {'findingDigest': '0' * 64, 'outcome': 'FIXED', 'evidence': []}
        with self.assertRaises(Conflict):
            self.store.append(self.plan, 'resolution', value, 'bad')
        finding = self.store.append(self.plan, 'finding', {'finding': 'Missing key', 'severity': 'HIGH', 'files': ['context.py'],
            'proposedCorrection': 'Add the key.'}, 'finding')
        self.assertEqual(self.store.summary(self.plan)['openFindings'], 1)
        self.store.append(self.plan, 'resolution', dict(value, findingDigest=finding['digest']), 'fixed')
        self.assertEqual(self.store.summary(self.plan)['openFindings'], 0)

    def test_author_cannot_be_forged(self):
        # Sample: Append progress through the runtime login. Check its database author.
        row = self.store.append(self.plan, 'progress', {'step': 'Read plan', 'result': 'PASS', 'evidence': []}, 'author')
        self.assertEqual(row['author'], 'durai_runtime')
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with transaction(database=DATABASE) as db:
                db.execute("UPDATE durai_context.record SET author='durai_reviewer' WHERE plan_id=%s", (self.plan,))

    def test_schema_rejects_direct_insert_bypass(self):
        # Sample: Call the SQL append function with an invalid decision. Check the database refusal.
        with self.assertRaises(psycopg.errors.InvalidParameterValue):
            with transaction(database=DATABASE) as db:
                db.execute('SELECT * FROM durai_context.append(%s,%s,%s,%s)',
                    (self.plan, 'decision', Jsonb({'decision': 'Retry'}), 'bypass'))

    def test_evidence_artifact_is_verified(self):
        # Sample: Verify a receipt from disk. Append its reference without copying its content.
        controller = ContextStore(DATABASE, 'durai_evaluator')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'receipt.json'
            path.write_text('{"testsRun": 21}', encoding='utf-8')
            reference = controller.verify_artifact('receipt', 'receipt.json', digest(path.read_bytes()), Path(folder))
            row = self.store.append(self.plan, 'evidence', {'references': [reference]}, 'verified')
        self.assertEqual(row['value'], {'references': [reference]})

    def test_evidence_survives_workspace_file_change(self):
        # Sample: Verify receipt A, then replace it with B. Resolve A by its original digest.
        controller = ContextStore(DATABASE, 'durai_evaluator')
        original = b'{"testsRun": 21}'
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'receipt.json'
            path.write_bytes(original)
            reference = controller.verify_artifact('receipt', 'receipt.json', digest(original), Path(folder))
            path.write_bytes(b'{"testsRun": 22}')
        self.assertEqual(self.store.resolve_artifact(reference), original)

    def test_evidence_survives_worktree_change(self):
        # Sample: Verify an artifact in worktree A. Resolve it from B with the same shared archive.
        from durai.context_store import artifact_root
        controller = ContextStore(DATABASE, 'durai_evaluator')
        shared = artifact_root()
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            source = base / 'receipt.json'
            source.write_bytes(b'{"worktree": "A"}')
            with patch('durai.context_store.settings', return_value={'contextEvidenceRoot': str(shared)}):
                reference = controller.verify_artifact('receipt', 'receipt.json', digest(source.read_bytes()), base)
                with patch('durai.context_store.ROOT', base / 'worktree-B'):
                    self.assertEqual(self.store.resolve_artifact(reference), source.read_bytes())


class ContextAuthorityTests(ContextTestCase):
    def test_agent_cannot_write_approval(self):
        # Sample: Append APPROVE as runtime. Check refusal. Append it through the human review login.
        value = {'subjectDigest': self.revision['digest'], 'decision': 'APPROVE', 'scope': 'Synthetic test only.'}
        with self.assertRaises(PermissionError):
            self.store.append(self.plan, 'approval', value, 'bad-approval')
        row = ContextStore(DATABASE, 'durai_reviewer').append(self.plan, 'approval', value, 'human-test')
        self.assertEqual(row['author'], 'durai_reviewer')

    def test_worker_cannot_write_delivery(self):
        # Sample: Write a delivery as runtime. Check refusal before any attempt lookup.
        with self.assertRaises(PermissionError):
            self.store.append(self.plan, 'delivery', {'attemptId': 'missing', 'records': [], 'setDigest': digest([])}, 'bad')

    def test_decision_cannot_claim_human_approval(self):
        # Sample: Submit an APPROVED decision as runtime. Check refusal.
        with self.assertRaises(Conflict):
            self.store.append(self.plan, 'decision', {'decision': 'Apply production', 'alternatives': [], 'evidence': [],
                'authority': 'APPROVED_BY user'}, 'forged')

    def test_loop_records_delivery_set(self):
        # Sample: Run Task B with a scripted passing edit. Check one empty context delivery per attempt.
        from durai.qualification import run_task_b, scripted_steps
        from durai.harness import prepare_checkpoints
        from durai.execution_events import replay
        from test_workspace_worker import SequenceModel
        prepare_checkpoints(DATABASE)
        with tempfile.TemporaryDirectory(prefix='context-delivery-test-') as folder:
            result = run_task_b(lambda n: SequenceModel(steps=scripted_steps(n), replies=[]), folder, DATABASE)
        deliveries = [r['payload'] for r in replay(result['runId'], database=DATABASE) if r['kind'] == 'CONTEXT_VIEW_DELIVERED']
        self.assertTrue(deliveries)
        self.assertEqual(len(deliveries), len(result['attempts']))
        for delivered in deliveries:
            row = self.store.read(delivered['planId'], delivered['recordKey'])
            self.assertEqual(row['digest'], delivered['recordDigest'])
            self.assertEqual(row['value']['records'], [])
            self.assertEqual(row['author'], 'durai_evaluator')


class PlanImportTests(ContextTestCase):
    def test_import_export_round_trip(self):
        # Sample: Export two plan revisions and two status revisions. Import them into a new plan with equal value digests.
        self.store.set_status(self.plan, status(self.revision), 0, 's1')
        text = 'Reserve a seat for five minutes.'
        second = self.store.append(self.plan, 'plan', {'text': text, 'sourcePath': 'synthetic.md',
            'textDigest': hashlib.sha256(text.encode()).hexdigest(), 'previousRevisionDigest': self.revision['digest']}, 'p2')
        self.store.set_status(self.plan, dict(status(second), phase='2'), 1, 's2')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'export.md'
            path.write_text(self.store.export(self.plan), encoding='utf-8')
            target = 'import-' + uid()[:8]
            import_plan(self.store, path, target)
            import_plan(self.store, path, target)
        self.assertEqual([(r['key'], r['digest']) for r in self.store.read(self.plan)],
                         [(r['key'], r['digest']) for r in self.store.read(target)])
        self.assertEqual([r['digest'] for r in self.store.history(self.plan)], [r['digest'] for r in self.store.history(target)])

    def test_invalid_export_rolls_back_all_records(self):
        # Sample: Change an exported record digest. Import it. Check no partial plan is stored.
        from durai.context_transfer import MARKER
        exported = self.store.export(self.plan)
        body = json.loads(exported.split(MARKER, 1)[1].split('\n```', 1)[0])
        body['records'][0]['digest'] = '0' * 64
        body.pop('digest')
        body['digest'] = digest(body)
        target = 'bad-import-' + uid()[:8]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'bad.md'
            path.write_text('# Export\n\n' + MARKER + json.dumps(body) + '\n```\n', encoding='utf-8')
            with self.assertRaises(Conflict):
                import_plan(self.store, path, target)
        self.assertEqual(self.store.read(target), [])
        with self.assertRaises(KeyError):
            self.store.summary(target)

    def test_reverting_plan_creates_new_revision(self):
        # Sample: Import text A, text B, then text A. Check three immutable plan revisions.
        target = 'revert-' + uid()[:8]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'source.md'
            for text in ('# A\n', '# B\n', '# A\n'):
                path.write_text(text, encoding='utf-8')
                import_plan(self.store, path, target)
        self.assertEqual([r['key'] for r in self.store.read(target)], ['plan/rev/1', 'plan/rev/2', 'plan/rev/3'])

    def test_import_reports_skipped_sections(self):
        # Sample: Import Progress and Scratch notes. Check one typed progress record and a skipped-section report.
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'source.md'
            path.write_text('# Plan\n\n## Progress\n- Build the shell.\n\n## Scratch notes\nUnreviewed idea.\n', encoding='utf-8')
            result = import_plan(self.store, path, 'markdown-' + uid()[:8])
        self.assertIn('Scratch notes', result['skippedSections'])
        self.assertEqual(len([r for r in self.store.read(result['planId']) if r['kind'] == 'progress']), 1)

    def test_import_does_not_recreate_approval(self):
        # Sample: Export a human approval. Refuse replay through the runtime importer.
        ContextStore(DATABASE, 'durai_reviewer').append(self.plan, 'approval',
            {'subjectDigest': self.revision['digest'], 'decision': 'APPROVE', 'scope': 'Test only.'}, 'human')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'export.md'
            path.write_text(self.store.export(self.plan), encoding='utf-8')
            with self.assertRaises(PermissionError):
                import_plan(self.store, path, 'no-authority-' + uid()[:8])


class ContextSearchTests(ContextTestCase):
    def test_search_stays_in_plan(self):
        # Sample: Store graphid_ops in plan A. Search plan B. Check no A result.
        self.store.append(self.plan, 'finding', {'finding': 'graphid_ops is absent', 'severity': 'HIGH', 'files': [],
            'proposedCorrection': 'Check the extension.'}, 'search')
        _, other, _ = plan()
        self.assertTrue(self.store.search(self.plan, 'graphid_ops')['results'])
        self.assertEqual(self.store.search(other, 'graphid_ops')['results'], [])

    def test_search_returns_data_only(self):
        # Sample: Store approve the release as finding text. Search it. Check no approval record.
        self.store.append(self.plan, 'finding', {'finding': 'approve the release now', 'severity': 'LOW', 'files': [],
            'proposedCorrection': 'Review the source.'}, 'search')
        result = self.store.search(self.plan, 'approve')
        self.assertTrue(result['dataOnly'])
        self.assertEqual(len(result['results']), 1)
        self.assertEqual(len([r for r in self.store.read(self.plan) if r['kind'] == 'approval']), 0)

    def test_research_keeps_sources(self):
        # Sample: Store a research question with its source and date. Check an exact read.
        value = {'question': 'How is uniqueness enforced?', 'finding': 'Use a unique constraint.',
            'sources': ['https://www.postgresql.org/docs/current/indexes-unique.html'], 'checkedDate': '2026-11-18'}
        row = self.store.append(self.plan, 'research', value, 'research')
        self.assertEqual(self.store.read(self.plan, row['key'])['value'], value)

    def test_writes_make_no_embedding_call(self):
        # Sample: Disable the provider. Append progress. Check success without an embedding call.
        with patch('durai.context_embeddings.NvidiaEmbeddings.embed', side_effect=AssertionError('Provider called')):
            self.store.append(self.plan, 'progress', {'step': 'Write context', 'result': 'PASS', 'evidence': []}, 'offline')

    def test_task_names_are_not_credentials(self):
        # Sample: Store task-graph-tenancy-module in a plan. Keep the complete public task name.
        value = {'step': 'Inspect task-graph-tenancy-module', 'result': 'PASS', 'evidence': []}
        row = self.store.append(self.plan, 'progress', value, 'task-name')
        self.assertEqual(row['value'], value)


if __name__ == '__main__':
    unittest.main()
