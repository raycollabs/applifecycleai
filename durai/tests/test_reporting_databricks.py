from decimal import Decimal
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from databricks.reporting import package
from durai.reporting.contract import digest


class DatabricksReportingTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.stage_args = ('7474651325426508', '4e3aa3d5f38a3fb7', 'workspace', 'durai_reporting_acceptance',
                           '00000000-0000-0000-0000-000000000001', 'oil-test', 'gas-test')

    def test_stage_pins_the_factory_and_report_fixture(self):
        # Sample: Stage the same fixture twice. Check equal manifests and nine file digests.
        first = self.folder / 'first'
        second = self.folder / 'second'
        manifest = package.stage(first, *self.stage_args)
        self.assertEqual(manifest, package.stage(second, *self.stage_args))
        self.assertEqual(manifest, package.verified(first, manifest['package_id']))
        self.assertEqual(set(manifest['artifacts']), set(package.ARTIFACTS))
        self.assertEqual(manifest['fixture']['version'], 0)
        self.assertEqual(manifest['fixture']['rows'], 16)
        self.assertEqual(manifest['report_semantic_sha256'], digest(json.loads(package.FIXTURE.read_bytes())))

    def test_revision_replay_has_one_key_and_conflict_guard(self):
        # Sample: Submit oil revision 2 twice. Check one insert path and a conflict guard.
        stage = self.folder / 'stage'
        manifest = package.stage(stage, *self.stage_args)
        sql = (stage / 'sql/publish.sql').read_text(encoding='utf-8')
        self.assertIn("TBLPROPERTIES ('delta.isolationLevel' = 'Serializable')", sql)
        self.assertIn('ON stored.application = incoming.application', sql)
        for field in ('business_date', 'book', 'revision'):
            self.assertIn(f'AND stored.{field} = incoming.{field}', sql)
        self.assertIn("raise_error('Report revision conflict')", sql)
        self.assertEqual(sql.count('WHEN NOT MATCHED THEN INSERT'), 1)
        bundle = (stage / 'databricks.yml').read_text(encoding='utf-8')
        self.assertIn('max_concurrent_runs: 1', bundle)
        self.assertIn('task_key: replay_revisions', bundle)
        self.assertEqual(bundle.count('/sql/publish.sql'), 2)
        self.assertEqual(len(manifest['report_revisions']), 4)

    def test_latest_complete_rows_and_separate_failure_evidence(self):
        # Sample: Read gas after an incomplete correction. Check revision 1 and 300 USD.
        stage = self.folder / 'stage'
        manifest = package.stage(stage, *self.stage_args)
        rows = {row[1]: (row[4], Decimal(row[10])) for row in manifest['expected_rows']}
        self.assertEqual(rows, {'SYN-GAS': ('1', Decimal(300)), 'SYN-OIL': ('2', Decimal(320))})
        evidence = [item for item in manifest['report_revisions'] if item['status'] == 'INCOMPLETE']
        self.assertEqual(evidence[0]['issues'][0]['code'], 'MISSING_CLOSE_MARK')
        reconcile = (stage / 'sql/reconcile.sql').read_text(encoding='utf-8')
        self.assertIn('rows_missing', reconcile)
        self.assertIn('rows_extra', reconcile)
        self.assertIn('evidence_missing', reconcile)
        self.assertIn('evidence_extra', reconcile)
        self.assertIn('revision_digest', reconcile)

    def test_invalid_fixture_revision_is_refused(self):
        # Sample: Give oil revision 1 twice in the fixture. Check that staging stops.
        fixture = json.loads(package.FIXTURE.read_bytes())
        fixture['reports'].append(fixture['reports'][0])
        source = self.folder / 'duplicate.json'
        source.write_text(json.dumps(fixture), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'repeats a report revision'):
            package.stage(self.folder / 'stage', *self.stage_args, fixture_path=source)

    def test_dashboard_viewer_binding(self):
        # Sample: Open the staged data query. Check the desk view and all seven measures.
        stage = self.folder / 'stage'
        package.stage(stage, *self.stage_args)
        dashboard = json.loads((stage / 'end_of_day.lvdash.json').read_bytes())
        query, = dashboard['datasets'][0]['queryLines']
        self.assertEqual(query + '\n', (stage / package.VISIBLE).read_text(encoding='utf-8'))
        self.assertIn('.desk_report', query)
        for column in package.COLUMNS:
            self.assertIn(column, query)
        bundle = (stage / 'databricks.yml').read_text(encoding='utf-8')
        self.assertIn('embed_credentials: false', bundle)
        self.assertIn('service_principal_name: "00000000-0000-0000-0000-000000000001"', bundle)
        grants = (stage / 'sql/test_grants.sql').read_text(encoding='utf-8')
        self.assertEqual(grants.count('GRANT SELECT ON VIEW'), 2)
        self.assertNotIn('report_revisions', grants)
        self.assertNotIn('accepted_report', grants)

    def test_modified_artifact_stops_verification(self):
        # Sample: Add one byte to the visible query. Refuse the release.
        stage = self.folder / 'stage'
        manifest = package.stage(stage, *self.stage_args)
        with (stage / package.VISIBLE).open('ab') as stream:
            stream.write(b' ')
        with self.assertRaisesRegex(ValueError, 'Package artifact differs'):
            package.verified(stage, manifest['package_id'])

    def test_bundle_plan_scope(self):
        # Sample: Add a warehouse to the preview. Refuse the preview.
        candidate = {resource: {'action': 'create'} for resource in package.RESOURCES}
        self.assertEqual(len(package.plan_scope({'plan': candidate})), 3)
        with self.assertRaisesRegex(ValueError, 'one report job and one dashboard'):
            package.plan_scope({'plan': candidate | {'resources.sql_warehouses.extra': {'action': 'create'}}})
        candidate['resources.jobs.report_acceptance']['action'] = 'delete'
        with self.assertRaisesRegex(ValueError, 'unexpected action'):
            package.plan_scope({'plan': candidate})

    def test_preview_checks_workspace_before_bundle_commands(self):
        # Sample: Point the profile at workspace 1. Stop before bundle validation.
        stage = self.folder / 'stage'
        manifest = package.stage(stage, *self.stage_args)
        profile = {'details': {'configuration': {'workspace_id': {'value': '1'}}}}
        with patch.object(package.factory, 'run_command', return_value=json.dumps(profile)) as cli:
            with self.assertRaisesRegex(ValueError, 'different workspace'):
                package.preview(stage, manifest['package_id'], 'TEST')
        cli.assert_called_once_with(['databricks', 'auth', 'describe', '--profile', 'TEST', '--output', 'json'])

    def test_read_only_receipt_checks_the_full_projection(self):
        # Sample: Compare two accepted rows with their exact measures. Record three statement IDs.
        stage = self.folder / 'stage'
        manifest = package.stage(stage, *self.stage_args)
        fixture = manifest['fixture']
        warehouse = Mock()
        warehouse.sql.side_effect = [([[str(fixture['rows']), fixture['snapshot_sha256'], None]], 'stmt-one'),
                                     ([[str(fixture['bookings']), '0', None]], 'stmt-two'),
                                     (manifest['expected_rows'], 'stmt-three')]
        receipt = package.verify_fixture(stage, manifest['package_id'], 'TEST', lambda *_: warehouse)
        self.assertEqual(receipt['statement_ids'][package.PARITY], 'stmt-three')
        self.assertEqual(receipt['observed_rows'], manifest['expected_rows'])
        self.assertEqual(receipt['columns'], list(package.COLUMNS))
        self.assertEqual(warehouse.sql.call_count, 3)
        for call in warehouse.sql.call_args_list:
            self.assertTrue(call.args[0].lstrip().startswith('WITH '))
