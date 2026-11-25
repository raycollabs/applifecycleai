"""Business acceptance goals for the local Durai trading track.

Each test uses the separate durai_test database. The local demo and pilot are
never reset. Test approval authority cannot activate LOCAL_DEMO targets.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import psycopg
from psycopg import sql

from durai.common import Conflict, digest, uid
from durai.composite import CompositeTask
from durai.coordination import Coordination
from durai.db import connect, prepare, transaction
from durai.evaluation_consumer import LocalProtectedEvaluator
from durai.files import NativeFixtureRunner, export_package, import_package
from durai.registry import Registry
from durai.release import ReleaseController
from durai.trading import ROOT, seed, discover

DATABASE = 'durai_test'


class PlatformTests(unittest.TestCase):
    def setUp(self):
        with transaction('postgres', DATABASE) as db:
            tables = db.execute("SELECT schemaname,tablename FROM pg_tables WHERE schemaname IN ('durai_agents','durai_files','durai_coordination','durai_governance','durai_improvement','dev_trading','app_trading')").fetchall()
            names = sql.SQL(',').join(sql.Identifier(row['schemaname'], row['tablename']) for row in tables)
            db.execute(sql.SQL('TRUNCATE {} RESTART IDENTITY CASCADE').format(names))
        self.registry = seed(DATABASE)
        self.runner = NativeFixtureRunner(ROOT / 'private/test-workspaces')
        self.evaluator = LocalProtectedEvaluator(self.registry, self.runner)
        self.controller = ReleaseController(self.registry, self.runner)
        self.coordinator = Coordination(DATABASE)

    def target(self):
        return self.controller.initialize_target('test-target', 'TEST')

    def accepted(self):
        self.target()
        run_id, finding_id = discover(DATABASE)
        verdict = self.evaluator.evaluate('repaired-v3')
        approval = self.controller.approve('repaired-v3', verdict['digest'], 'test-target',
                                           'automated-test', 'TEST_ONLY')
        return run_id, finding_id, verdict, approval

    def test_migrations_are_replayable_and_digest_bound(self):
        # Apply again. Keep V-1. Change only a private migration copy. Reject it.
        prepare(DATABASE)
        self.assertEqual(self.registry.get('broken-v1')['agent_id'], 'trading-producer')
        with tempfile.TemporaryDirectory(dir=ROOT / 'private') as folder:
            root = Path(folder)
            directory = root / 'migrations/core'
            directory.mkdir(parents=True)
            original = ROOT / 'migrations/core/core_0001.sql'
            (directory / original.name).write_bytes(original.read_bytes() + b'\n-- changed\n')
            settings = __import__('durai.db', fromlist=['settings']).settings()
            with patch('durai.db.ROOT', root), patch('durai.db.settings', return_value=settings):
                with self.assertRaisesRegex(ValueError, 'Applied migration changed'):
                    prepare(DATABASE)

    def test_registered_version_is_immutable(self):
        # Keep D-1 for V-1. Refuse a service replacement and a direct SQL update.
        before = self.registry.get('broken-v1')
        with self.assertRaises(Conflict):
            self.registry.register('trading-producer', 'broken-v1', b'new code', {})
        with self.assertRaises(psycopg.errors.CheckViolation):
            with transaction('postgres', DATABASE) as db:
                db.execute("UPDATE durai_agents.version SET digest=%s WHERE id='broken-v1'", ('0' * 64,))
        self.assertEqual(self.registry.get('broken-v1')['digest'], before['digest'])

    def test_registration_does_not_authorize_deployment(self):
        # Register the producer. Refuse activation and direct runtime target writes.
        self.target()
        with self.assertRaises(Conflict):
            self.controller.activate('missing-approval', 'F-101', 'run')
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with transaction(database=DATABASE) as db:
                db.execute("UPDATE durai_governance.target SET active_version='repaired-v3'")
        self.controller.initialize_target('local-target', 'LOCAL_DEMO')
        verdict = self.evaluator.evaluate('repaired-v3')
        with self.assertRaisesRegex(Conflict, 'Test authority'):
            self.controller.approve('repaired-v3', verdict['digest'], 'local-target', 'test', 'TEST_ONLY')

    def test_foreign_application_is_denied(self):
        # Store F-101 for trading. A foreign login must see no trading records.
        discover(DATABASE)
        foreign = Registry(DATABASE, 'durai_foreign')
        self.assertEqual(foreign.discover('trade.book'), [])
        with self.assertRaises(KeyError):
            foreign.get('broken-v1')
        with transaction('durai_foreign', DATABASE) as db:
            self.assertEqual(db.execute('SELECT * FROM durai_improvement.finding').fetchall(), [])
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with transaction('durai_foreign', DATABASE) as db:
                db.execute("INSERT INTO app_trading.trade VALUES('trading','foreign-write',2,100,200,'broken-v1')")

    def test_stale_owner_cannot_commit(self):
        # Reclaim expired J-1 as W-2. Refuse W-1. Commit W-2 once.
        self.coordinator.enqueue('J-1')
        first = self.coordinator.claim('J-1', 'W-1')
        with transaction('postgres', DATABASE) as db:
            db.execute("UPDATE durai_coordination.job SET lease_until=clock_timestamp()-interval '1 second' WHERE id='J-1'")
        second = self.coordinator.claim('J-1', 'W-2')
        self.assertGreater(second, first)
        with self.assertRaises(Conflict):
            self.coordinator.commit('J-1', 'W-1', first, {'shares': 2})
        self.coordinator.commit('J-1', 'W-2', second, {'shares': 200})
        with self.assertRaises(Conflict):
            self.coordinator.commit('J-1', 'W-2', second, {'shares': 300})

    def test_idempotent_mutation_rejects_changed_input(self):
        # Replay K-1. Keep one trade. Refuse the same key with 3 lots.
        first = self.coordinator.book_once('K-1', 'T-new', 2, 100, 'repaired-v3')
        self.assertEqual(first, self.coordinator.book_once('K-1', 'T-new', 2, 100, 'repaired-v3'))
        with self.assertRaises(Conflict):
            self.coordinator.book_once('K-1', 'T-new', 3, 100, 'repaired-v3')
        with transaction(database=DATABASE) as db:
            self.assertEqual(db.execute("SELECT count(*) AS n FROM app_trading.trade WHERE id='T-new'").fetchone()['n'], 1)

    def test_concurrent_reservations_obey_capacity(self):
        # Run 10 callers against 3 agent slots. Preserve the separate API slot.
        self.coordinator.pool('agent', 3)
        self.coordinator.pool('api', 1)
        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(lambda n: self.coordinator.reserve('agent', 'R-' + str(n)), range(10)))
        self.assertEqual(sum(results), 3)
        self.assertTrue(self.coordinator.reserve('api', 'request'))

    def test_finding_preserves_business_evidence(self):
        # Discover 2 stored shares against the 200-share rule. Retain all bindings.
        run_id, finding_id = discover(DATABASE)
        with transaction(database=DATABASE) as db:
            finding = db.execute('SELECT * FROM durai_improvement.finding WHERE id=%s', (finding_id,)).fetchone()
            context = db.execute('SELECT document FROM durai_files.manifest WHERE digest=%s', (finding['evidence']['contextManifest'],)).fetchone()['document']
        evidence = finding['evidence']
        self.assertEqual((evidence['observedShares'], evidence['expectedShares']), (2, 200))
        self.assertEqual(evidence['snapshotDigest'], digest(evidence['trade']))
        self.assertEqual(evidence['sourceRevision'], self.registry.get('broken-v1')['digest'])
        self.assertEqual(evidence['contextManifest'], digest(context))
        self.assertEqual(discover(DATABASE, run_id), (run_id, finding_id))

    def test_regression_keeps_accepted_baseline(self):
        # Reject the repair that returns 199 shares after one-lot correction.
        self.target()
        rejected = self.evaluator.evaluate('quantity-only-v2')
        accepted = self.evaluator.evaluate('repaired-v3')
        self.assertEqual(rejected['verdict'], 'FAIL')
        self.assertEqual(accepted['verdict'], 'PASS')
        with self.assertRaises(Conflict):
            self.controller.approve('quantity-only-v2', rejected['digest'], 'test-target', 'test', 'TEST_ONLY')
        self.assertEqual(self.target()['active_version'], 'broken-v1')

    def test_activation_requires_evaluated_approved_digest(self):
        # Refuse another digest and refuse a target generation changed after review.
        run_id, finding_id, verdict, approval = self.accepted()
        with self.assertRaises(Conflict):
            self.controller.approve('repaired-v3', 'f' * 64, 'test-target', 'test', 'TEST_ONLY')
        with transaction('postgres', DATABASE) as db:
            db.execute("UPDATE durai_governance.target SET generation=generation+1 WHERE id='test-target'")
        with self.assertRaisesRegex(Conflict, 'Target changed'):
            self.controller.activate(approval, finding_id, run_id)

    def test_target_verification_and_rollback(self):
        # Verify 200 opening shares and 100 after correction. Roll back the version.
        run_id, finding_id, verdict, approval = self.accepted()
        result = self.controller.activate(approval, finding_id, run_id)
        self.assertEqual((result['shares'], result['correctionShares']), (200, 100))
        self.controller.activate(approval, finding_id, run_id)  # replay has no effect
        with transaction(database=DATABASE) as db:
            self.assertEqual(db.execute('SELECT state FROM durai_improvement.finding WHERE id=%s', (finding_id,)).fetchone()['state'], 'RESOLVED')
        restored = self.controller.rollback('test-target')
        self.assertEqual((restored['versionId'], restored['shares']), ('broken-v1', 2))
        with self.assertRaises(Conflict):
            self.controller.activate(approval, finding_id, run_id)

    def test_restart_resumes_durable_run(self):
        # Create a new coordinator after checkpoint. Preserve the effect and outcome.
        run_id = uid()
        self.coordinator.run(run_id)
        self.coordinator.enqueue('restart-job')
        generation = self.coordinator.claim('restart-job', 'worker-1')
        self.coordinator.commit('restart-job', 'worker-1', generation, {'stage': 'BOOKED'}, run_id)
        resumed = Coordination(DATABASE)
        self.assertEqual(resumed.run(run_id)['checkpoint']['stage'], 'BOOKED')
        self.assertIsNone(resumed.claim('restart-job', 'worker-2'))
        with transaction(database=DATABASE) as db:
            self.assertEqual(db.execute('SELECT count(*) AS n FROM durai_coordination.event WHERE run_id=%s', (run_id,)).fetchone()['n'], 1)

    def test_sqlite_round_trip_preserves_package(self):
        # Export the exact source and skill. Refuse a changed source byte on import.
        with tempfile.TemporaryDirectory(dir=ROOT / 'private') as folder:
            package = Path(folder, 'context.sqlite')
            files = {'workspace/program.py': self.registry.get('repaired-v3')['source_bytes'], 'skills/units.md': b'Use canonical shares.'}
            checksum = export_package(package, files, {'ontologyVersion': '1'})
            restored, _ = import_package(package, checksum)
            self.assertEqual(restored, files)
            with closing(sqlite3.connect(package)) as db, db:
                db.execute("UPDATE files SET content=? WHERE path='workspace/program.py'", (b'tampered',))
            with self.assertRaises(Conflict):
                import_package(package, checksum)

    def test_sandbox_cannot_receive_controller_secrets(self):
        # Read only names of inherited secret variables. Expect none. Refuse D-2.
        source = b'import os,json\nprint(json.dumps({"secretNames":[k for k in os.environ if any(x in k for x in ["PASSWORD","TOKEN","API_KEY","DSN","REVIEW_KEY"])]}))\n'
        with patch.dict('os.environ', {'DURAI_CONTROLLER_PASSWORD': 'test-secret', 'OPENROUTER_API_KEY': 'test-secret'}):
            self.assertEqual(self.runner.execute(source, digest(source), {})['secretNames'], [])
        with self.assertRaises(Conflict):
            self.runner.execute(source, '0' * 64, {})

    def test_lazy_aggregate_invalidates_ancestors(self):
        # Cache the root. Change the leaf concurrently. Recompute without stale output.
        leaf = CompositeTask(2)
        branch = CompositeTask(children=[leaf])
        root = CompositeTask(children=[branch])
        self.assertEqual(root.result(), 2)
        self.assertEqual(root.result(), 2)
        self.assertEqual(root.computations, 1)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(leaf.set, range(20)))
        leaf.set(200)
        self.assertEqual(root.result(), 200)
        self.assertEqual((root.computations, branch.computations), (2, 2))

    def test_manifest_restores_database_and_files(self):
        # Sample: Restore F-101 SQL and exact source bytes. Check that activation authority stays disabled.
        from durai.recovery import export_fixture, restore_fixture
        discover(DATABASE)
        with tempfile.TemporaryDirectory(dir=ROOT / 'private') as folder:
            path = Path(folder, 'recovery.sqlite')
            checksum = export_fixture(path, DATABASE)
            result = restore_fixture(path, checksum)
            self.assertEqual(result['state'], 'RESTORED_FIXTURE')
            restored = Registry(result['database']).get('repaired-v3')
            self.assertEqual(restored['source_bytes'], self.registry.get('repaired-v3')['source_bytes'])
            self.assertEqual(result['activationAuthority'], 'DISABLED')

    def test_existing_restore_is_not_overwritten(self):
        # Sample: Restore F-101 twice with the same database ID. Refuse the second restore and keep the first snapshot unchanged.
        from durai.recovery import export_fixture,restore_fixture,snapshot
        fixed_id = uid()
        with tempfile.TemporaryDirectory(dir=ROOT/'private') as folder:
            path = Path(folder,'recovery-collision.sqlite')
            checksum = export_fixture(path,DATABASE)
            with patch('durai.recovery.uid',return_value=fixed_id):
                first = restore_fixture(path,checksum)
                before = snapshot(first['database'])
                with self.assertRaises(psycopg.errors.DuplicateDatabase):
                    restore_fixture(path,checksum)
            self.assertEqual(snapshot(first['database']),before)


if __name__ == '__main__':
    unittest.main()
