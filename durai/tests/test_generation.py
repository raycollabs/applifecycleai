"""Business acceptance for generation, import reconciliation, and system replicas."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from durai.db import transaction
from durai.common import digest, uid, Conflict
from durai.engineering import Engineering
from durai.files import NativeFixtureRunner
from durai.generation import DEFAULT_SPEC, generate, verify_bundle
from durai.packs import PackStore, generic_pack
from durai.replica import capture_replica, verify_replica, evolve
from durai.trading import seed
from durai.trading_import import trading_pack

DATABASE = 'durai_test'
ROOT = Path(__file__).resolve().parents[1]


class ImportTests(unittest.TestCase):
    def test_historical_artifacts_keep_type_and_layer(self):
        # Sample: Map six historical memory slots through their revisions. Keep each layer.
        catalog = json.loads((ROOT / 'design/reference-packs/exchange-trading/implementation-source.json').read_text())
        snapshot = {'learning.artifact': [dict(a, content_digest=a['digest'], profile=catalog['profile']) for a in catalog['artifacts']],
                    'graph.vertices': [], 'graph.edges': [], 'ontology_meta.artifact_slot': [], 'ontology_meta.artifact_revision': []}
        for index, layer in enumerate(['DESIGN', 'ENTITY', 'DAO', 'SERVICE', 'UI', 'TEST']):
            slot = {'id': 'slot-' + str(index), 'layer': layer, 'ontology_type': 'memory'}
            snapshot['ontology_meta.artifact_slot'].append(slot)
            snapshot['ontology_meta.artifact_revision'].append({'id': 'revision-' + str(index), 'slot_id': slot['id'],
                'document_id': 'unavailable-document', 'template_id': 'unavailable-template'})
        document, _ = trading_pack(snapshot)
        historical = [r for r in document['records'] if r['body'].get('sourceTable') == 'ontology_meta.artifact_revision']
        self.assertEqual({(r['layer'], r['type']) for r in historical}, {(layer, 'MEMORY') for layer in ['DESIGN', 'ENTITY', 'DAO', 'SERVICE', 'UI', 'TEST']})

    def test_trading_import_preserves_catalog(self):
        # Sample: Import the 18 cells, 30 rules, and 3 solutions. Retain original bodies.
        document, files = trading_pack()
        catalog = json.loads((ROOT / 'design/reference-packs/exchange-trading/implementation-source.json').read_text())
        records = {r['id']: r['body'] for r in document['records']}
        for artifact in catalog['artifacts']:
            self.assertEqual(records['catalog.' + artifact['id']], artifact)
        for rule in catalog['rules']:
            self.assertEqual(records['rule.' + rule['id']], rule)
        self.assertEqual(len([name for name in records if name.startswith('solution.')]), 3)
        self.assertEqual(document['reviewState'], 'IMPLEMENTED_UNREVIEWED')
        self.assertFalse(document['provenance']['missingFiles'])
        self.assertEqual(set(document['provenance']['fileDigests']), set(files))


class GreenfieldTests(unittest.TestCase):
    def setUp(self):
        # Sample: Bind G-1 to an isolated test app. Execute only trusted test templates.
        self.store = PackStore(DATABASE)
        self.generic = self.store.install(generic_pack())
        self.binding = 'greenfield-test-' + uid()
        self.runner = NativeFixtureRunner(ROOT / 'private/generation-test-workspaces')

    def test_generated_application_satisfies_business_goals(self):
        # Sample: Add T-1 ontology. Generate the app. Check all eight independent goals.
        document, _ = trading_pack()
        domain = self.store.install(document)
        self.store.bind(self.binding, [self.generic, domain], 'SYNTHETIC_LOCAL')
        result = generate(self.binding, dict(DEFAULT_SPEC), DATABASE, runner=self.runner)
        self.assertEqual(result['state'], 'WAITING_FOR_REVIEW')
        self.assertEqual(len(result['evaluation']['checks']), 8)
        self.assertTrue(all(item['passed'] for item in result['evaluation']['checks']))
        folder = Path(result['outputDirectory'])
        self.assertIn('index.html', verify_bundle(folder)['files'])
        evidence = Engineering(DATABASE).get(result['runId'])['evidence']
        self.assertEqual(evidence[2]['document']['observed'], 2)
        self.assertEqual(evidence[2]['document']['expected'], 200)
        self.assertEqual(Engineering(DATABASE).get(result['runId'])['document']['domainSources'][0]['recordId'], 'sample.position-domain')

    def test_generic_only_application_needs_domain_semantics(self):
        # Sample: Bind only G-1. Supply user rules and generate without the optional pack.
        self.store.bind(self.binding, [self.generic], 'SYNTHETIC_LOCAL')
        spec = dict(DEFAULT_SPEC, applicationName='User-defined lot ledger')
        result = generate(self.binding, spec, DATABASE, runner=self.runner)
        run = Engineering(DATABASE).get(result['runId'])
        self.assertEqual(len(run['document']['packs']), 1)
        self.assertEqual(run['document']['specification']['applicationName'], spec['applicationName'])
        self.assertEqual(run['document']['domainSources'][0]['kind'], 'USER_DEFINED')
        self.assertIn(spec['applicationName'], (Path(result['outputDirectory']) / 'index.html').read_text())
        self.assertEqual(result['state'], 'WAITING_FOR_REVIEW')

    def test_changed_generated_source_is_rejected(self):
        # Sample: Seal the generated program. Add one source byte. Refuse bundle verification.
        self.store.bind(self.binding, [self.generic], 'SYNTHETIC_LOCAL')
        result = generate(self.binding, DEFAULT_SPEC, DATABASE, runner=self.runner)
        path = Path(result['outputDirectory']) / 'program.py'
        path.write_bytes(path.read_bytes() + b'\n# changed\n')
        with self.assertRaisesRegex(Conflict, 'digest'):
            verify_bundle(path.parent)

    def test_conflicting_application_ontology_blocks_coding(self):
        # Sample: Bind a domain that adds correction lots. Request REMOVE_LOTS. Refuse coding.
        document, _ = trading_pack()
        document['id'] = 'test.conflicting.' + uid()
        record = next(r for r in document['records'] if r['id'] == 'sample.position-domain')
        record['body']['correctionDirection'] = 'ADD_LOTS'
        record['digest'] = digest(record['body'])
        domain = self.store.install(document)
        self.store.bind(self.binding, [self.generic, domain], 'SYNTHETIC_LOCAL')
        with self.assertRaisesRegex(Conflict, 'contradicts the application ontology'):
            generate(self.binding, DEFAULT_SPEC, DATABASE, runner=self.runner)

    def test_model_provider_failure_has_no_fallback(self):
        # Sample: Fail model initialization after business-red. Record FAILED and no candidate.
        self.store.bind(self.binding, [self.generic], 'SYNTHETIC_LOCAL')
        with patch('durai.coding_worker.configured_model', side_effect=RuntimeError('Provider unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'no fallback or approval'):
                generate(self.binding, DEFAULT_SPEC, DATABASE, use_model=True, runner=self.runner)
        with transaction(database=DATABASE) as db:
            run = db.execute("SELECT id FROM durai_engineering.run WHERE document->>'bindingId'=%s", (self.binding,)).fetchone()
        state = Engineering(DATABASE).get(run['id'])
        self.assertEqual(state['state'], 'FAILED')
        self.assertFalse(any(item['stage'] == 'WAITING_FOR_REVIEW' for item in state['evidence']))


class ReplicaTests(unittest.TestCase):
    def setUp(self):
        # Sample: Register the known synthetic baseline. Keep source versions immutable.
        seed(DATABASE)
        self.store = PackStore(DATABASE)
        generic = self.store.install(generic_pack())
        self.binding = 'replica-test-' + uid()
        self.store.bind(self.binding, [generic], 'SYNTHETIC_LOCAL')
        self.runner = NativeFixtureRunner(ROOT / 'private/replica-test-workspaces')

    def test_replica_preserves_source_and_snapshot(self):
        # Sample: Capture broken-v1. Recheck source and the 2-lot snapshot before running.
        source, manifest = capture_replica(DATABASE)
        self.assertTrue(verify_replica(source, manifest))
        self.assertEqual(manifest['snapshot'], {'lots': 2, 'lot_size': 100})
        result = self.runner.execute(source, digest(source), manifest['snapshot'])
        self.assertEqual(result, {'shares': 2})

    def test_refactor_preserves_business_results(self):
        # Sample: Repair opening and correction. Preserve the single-share-unit regression.
        result = evolve(self.binding, DATABASE, self.runner)
        checks = {item['goal']: item for item in result['comparison']['checks']}
        self.assertEqual(checks['opening']['observed'], 200)
        self.assertEqual(checks['correction']['observed'], 100)
        self.assertEqual(checks['short']['observed'], -200)
        self.assertEqual(checks['unit-lot-regression']['baseline'], checks['unit-lot-regression']['observed'])
        self.assertTrue(result['originalSourceUnchanged'])
        self.assertFalse(result['approvalCreated'])

    def test_replica_rejects_changed_manifest(self):
        # Sample: Change the captured lot size to 50. Reject the unsealed snapshot.
        source, manifest = capture_replica(DATABASE)
        changed = copy.deepcopy(manifest)
        changed['snapshot']['lot_size'] = 50
        with self.assertRaises(Conflict):
            verify_replica(source, changed)
        with self.assertRaises(Conflict):
            verify_replica(source + b'\n# tampered', manifest)

    def test_runner_failure_records_failed_state(self):
        # Sample: Stop the runner for T-101. Retain FAILED and no review evidence.
        with patch.object(self.runner, 'execute', side_effect=RuntimeError('Synthetic runner loss')):
            with self.assertRaisesRegex(RuntimeError, 'Synthetic runner loss'):
                evolve(self.binding, DATABASE, self.runner)
        with transaction(database=DATABASE) as db:
            row = db.execute("SELECT id FROM durai_engineering.run WHERE document->>'bindingId'=%s", (self.binding,)).fetchone()
        state = Engineering(DATABASE).get(row['id'])
        self.assertEqual(state['state'], 'FAILED')
        self.assertEqual(state['evidence'][-1]['document']['errorType'], 'RuntimeError')
        self.assertFalse(any(item['stage'] == 'WAITING_FOR_REVIEW' for item in state['evidence']))
