"""Replica Phase 1 acceptance goals with synthetic data and isolated test records."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from durai.common import digest, uid, Conflict
from durai.db import transaction
from durai.engineering import Engineering
from durai.files import NativeFixtureRunner
from durai.generation import ROOT
from durai.packs import PackStore, generic_pack
from durai.trading_import import trading_pack
from durai.replica_manifest import capture, verify, fixture_data
from durai.replica_requests import ReplicaRequests, NAMESPACE
from durai.replica_phase1 import qualify, restore

DATABASE = 'durai_test'


class PhaseOneTests(unittest.TestCase):
    def setUp(self):
        # Sample: Bind G-1 and trading T-1 to a unique synthetic application.
        self.store = PackStore(DATABASE)
        generic = self.store.install(generic_pack())
        document, _ = trading_pack()
        domain = self.store.install(document)
        self.binding = 'phase1-' + uid()
        self.store.bind(self.binding, [generic, domain], 'SYNTHETIC_LOCAL')
        self.key = uid()
        self.files, self.manifest = capture(self.binding, DATABASE)
        native = NativeFixtureRunner(ROOT / 'private/phase1-test-workspaces')
        self.runner = Mock(wraps=native)
        self.runner.image = self.manifest['imageId']

    def test_manifest_tamper_is_rejected(self):
        # Sample: Change each input byte or policy digest. Check refusal before execution.
        for name in self.files:
            changed = dict(self.files)
            changed[name] += b'changed'
            with self.assertRaises(Conflict):
                verify(changed, self.manifest, self.binding)
        for name in ('bindingDigest', 'imageId', 'provider'):
            changed = copy.deepcopy(self.manifest)
            changed[name] = 'changed'
            with self.assertRaises(Conflict):
                verify(self.files, changed, self.binding)

    def test_foreign_scope_is_rejected(self):
        # Sample: Read app A fixtures as app B. Return no rows.
        with self.assertRaises(Conflict):
            fixture_data(self.binding, 'foreign')
        with self.assertRaises(Conflict):
            verify(self.files, self.manifest, 'foreign')

    def test_replay_returns_same_run_without_execution(self):
        # Sample: Finish K-1. Replay through a new API call with no runner calls.
        first = qualify(self.binding, self.key, DATABASE, self.runner)
        self.assertEqual(first['state'], 'WAITING_FOR_REVIEW')
        self.runner.execute.reset_mock()
        second = qualify(self.binding, self.key, DATABASE, self.runner)
        self.assertEqual(first, second)
        self.runner.execute.assert_not_called()

    def test_changed_request_is_rejected(self):
        # Sample: Claim K-1 for manifest M-1. Refuse M-2 with the same key.
        requests = ReplicaRequests(DATABASE)
        requests.claim(self.key, {'manifestDigest': 'M-1'})
        with self.assertRaises(Conflict):
            requests.claim(self.key, {'manifestDigest': 'M-2'})

    def test_active_owner_cannot_be_replaced(self):
        # Sample: Hold K-1 in RUNNING. Refuse another caller.
        request = {'manifestDigest': self.manifest['id']}
        ReplicaRequests(DATABASE).claim(self.key, request)
        with self.assertRaises(Conflict):
            ReplicaRequests(DATABASE).claim(self.key, request)

    def test_expired_owner_is_fenced(self):
        # Sample: Expire K-1. Record LOST and refuse the original owner result.
        requests = ReplicaRequests(DATABASE)
        request = {'manifestDigest': self.manifest['id']}
        owner, _ = requests.claim(self.key, request)
        with transaction(database=DATABASE) as db:
            db.execute("UPDATE durai_coordination.kv SET value=jsonb_set(value,'{deadline}',to_jsonb('2000-01-01T00:00:00+00:00'::text)) WHERE namespace=%s AND key=%s", (NAMESPACE, self.key))
        _, result = requests.claim(self.key, request)
        self.assertEqual(result['state'], 'LOST')
        with self.assertRaises(Conflict):
            requests.finish(self.key, owner, {'state': 'WAITING_FOR_REVIEW'})

    def test_provider_failure_is_terminal(self):
        # Sample: Stop the runner. Save FAILED and no review evidence on replay.
        self.runner.execute.side_effect = RuntimeError('Synthetic provider loss')
        first = qualify(self.binding, self.key, DATABASE, self.runner)
        self.assertEqual(first['state'], 'FAILED')
        state = Engineering(DATABASE).get(first['runId'])
        self.assertEqual(state['state'], 'FAILED')
        self.assertEqual(state['evidence'][-1]['document']['errorType'], 'RuntimeError')
        self.runner.execute.reset_mock()
        self.assertEqual(first, qualify(self.binding, self.key, DATABASE, self.runner))
        self.runner.execute.assert_not_called()

    def test_runner_initialization_failure_is_terminal(self):
        # Sample: Remove the runner provider. Save FAILED after run creation.
        with patch('durai.sandbox.DockerRunner', side_effect=RuntimeError('Missing provider')):
            result = qualify(self.binding, self.key, DATABASE)
        self.assertEqual(result['state'], 'FAILED')
        self.assertEqual(Engineering(DATABASE).get(result['runId'])['state'], 'FAILED')

    def test_restore_recreates_inputs_and_results(self):
        # Sample: Restore all six files. Run a restored correction and check 100 shares.
        result = qualify(self.binding, self.key, DATABASE, self.runner)
        with tempfile.TemporaryDirectory() as temporary:
            restored = Path(temporary) / 'replica'
            receipt = restore(result['outputDirectory'], restored, result['packageDigest'], self.binding)
            for name, checksum in result['manifest']['files'].items():
                self.assertEqual(digest((restored / name).read_bytes()), checksum)
            source = (restored / 'candidate/program.py').read_bytes()
            inputs = json.loads((restored / 'fixtures.json').read_bytes())
            self.assertEqual(self.runner.execute(source, digest(source), inputs[1]['input'])['netShares'], 100)
            self.assertFalse(receipt['executionStarted'])

    def test_restore_rejects_foreign_scope(self):
        # Sample: Restore app A as app B. Create no destination folder.
        result = qualify(self.binding, self.key, DATABASE, self.runner)
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / 'replica'
            with self.assertRaises(Conflict):
                restore(result['outputDirectory'], destination, result['packageDigest'], 'foreign')
            self.assertFalse(destination.exists())

    def test_candidate_requires_review(self):
        # Sample: Check opening, correction, short, invalid, and duplicate goals.
        result = qualify(self.binding, self.key, DATABASE, self.runner)
        self.assertEqual(result['state'], 'WAITING_FOR_REVIEW')
        checks = result['comparison']['checks']
        self.assertEqual(len(checks), 8)
        self.assertTrue(all(item['passed'] for item in checks))
        self.assertFalse(result['approvalCreated'])
        self.assertIn('duplicate-trade-id', [item['goal'] for item in checks])
        state = Engineering(DATABASE).get(result['runId'])
        self.assertFalse(state['document']['releaseAuthority'])

    def test_wrong_image_is_rejected_before_execution(self):
        # Sample: Pin image I-1. Refuse a runner that reports image I-2.
        self.runner.image = 'other-image'
        result = qualify(self.binding, self.key, DATABASE, self.runner)
        self.assertEqual(result['state'], 'FAILED')
        self.runner.execute.assert_not_called()

    def test_abandoned_run_records_lost_on_replay(self):
        # Sample: Interrupt the owner. Expire its deadline. Replay without execution.
        self.runner.execute.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            qualify(self.binding, self.key, DATABASE, self.runner)
        with transaction(database=DATABASE) as db:
            db.execute("UPDATE durai_coordination.kv SET value=jsonb_set(value,'{deadline}',to_jsonb('2000-01-01T00:00:00+00:00'::text)) WHERE namespace=%s AND key=%s", (NAMESPACE, self.key))
        self.runner.execute.reset_mock()
        result = qualify(self.binding, self.key, DATABASE, self.runner)
        self.assertEqual(result['state'], 'LOST')
        self.assertEqual(Engineering(DATABASE).get(result['runId'])['state'], 'FAILED')
        self.runner.execute.assert_not_called()
