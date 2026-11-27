from pathlib import Path
import json
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from databricks.factory.package import IDENTITY, open_profile, preview, stage, verify_fixture


class FactoryPackageTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(TemporaryDirectory())
        self.root = Path(temporary)
        self.bundle = self.root / 'release'
        self.workspace = '1234567'
        self.warehouse = '1234567890abcdef'
        self.manifest = stage(self.bundle, self.workspace, self.warehouse, 'workspace')

    def test_stage_is_reproducible(self):
        # Sample: Stage the same fixture in a second folder. Check an equal package ID.
        repeat = stage(self.root / 'repeat', self.workspace, self.warehouse, 'workspace')
        self.assertEqual(self.manifest, repeat)
        self.assertEqual(repeat['fixture']['rows'], 16)

    def test_changed_sql_is_refused(self):
        # Sample: Add a line to the identity query. Refuse verification before a SQL call.
        sql = self.bundle / IDENTITY
        sql.write_bytes(sql.read_bytes() + b'\n')
        open_warehouse = Mock()
        with self.assertRaisesRegex(ValueError, 'Package artifact differs'):
            verify_fixture(self.bundle, self.manifest['package_id'], open_warehouse)
        open_warehouse.assert_not_called()

    def test_fixture_failure_stops_the_job(self):
        # Sample: Return 15 rows for a 16-row package. Refuse the business check.
        warehouse = Mock()
        warehouse.sql.return_value = ([['15', 'other', None]], 'identity-statement')
        with self.assertRaisesRegex(ValueError, 'Fixture identity check differs'):
            verify_fixture(self.bundle, self.manifest['package_id'], lambda _: warehouse)
        warehouse.sql.assert_called_once()

    def test_verified_fixture_records_statement_ids(self):
        # Sample: Check 16 fixture rows and four bookings. Return both statement IDs.
        warehouse = Mock()
        fixture = self.manifest['fixture']
        warehouse.sql.side_effect = [([['16', fixture['snapshot_sha256'], None]], 'identity-statement'),
                                     ([['4', '0', None]], 'business-statement')]
        result = verify_fixture(self.bundle, self.manifest['package_id'], lambda _: warehouse)
        self.assertEqual(result['status'], 'FIXTURE_VERIFIED')
        self.assertEqual(result['package_id'], self.manifest['package_id'])
        self.assertEqual(result['statement_ids'], {'identity': 'identity-statement',
                                                   'business_rules': 'business-statement'})

    def test_preview_refuses_resource_delete(self):
        # Sample: Return a planned job deletion. Refuse the preview receipt.
        plan = {'plan': {'resources.jobs.acceptance': {'action': 'delete'}}, 'cli_version': '1.16.1'}
        with patch('databricks.factory.package.open_profile'), patch(
                'databricks.factory.package.run_command', side_effect=['{}', json.dumps(plan)]):
            with self.assertRaisesRegex(ValueError, 'Unexpected bundle action'):
                preview(self.bundle, self.manifest['package_id'], 'DEFAULT')

    def test_profile_refuses_another_workspace(self):
        # Sample: Use workspace 7654321 for a package for 1234567. Refuse the profile.
        auth = {'details': {'configuration': {'workspace_id': {'value': 7654321}}}}
        with patch('databricks.factory.package.run_command', return_value=json.dumps(auth)):
            with self.assertRaisesRegex(ValueError, 'different workspace'):
                open_profile('DEFAULT', self.manifest)
