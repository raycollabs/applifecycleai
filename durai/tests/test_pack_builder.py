"""Sample: Test automated pack builder for starter and paid transaction packs."""
import json
from pathlib import Path
import unittest
from durai.pack_signing import (
    TrustedKeyList,
    load_public_key_hex,
    verify_pack,
)
from durai.pack_registry import PackRegistry
from tools.build_pack import (
    build_paid_transaction_pack,
    build_starter_pack,
    get_or_create_test_key,
)

ROOT = Path(__file__).resolve().parents[1]


class PackBuilderTests(unittest.TestCase):
    def setUp(self):
        # Sample: Set up test key pair and trusted key list for builder tests.
        self.priv, self.pub = get_or_create_test_key()
        self.trusted = TrustedKeyList()
        self.trusted.add_key('durai-test-signer-2026', self.pub)
        self.registry = PackRegistry(trusted_keys=self.trusted)

    def test_build_starter_pack_validates_and_installs(self):
        # Sample: Build free starter pack. Check tier FREE, 31 records, and state INSTALLED.
        starter = build_starter_pack(self.priv)
        self.assertEqual(starter['format'], 'durai-ontology-pack/v2')
        self.assertEqual(starter['id'], 'durai.starter')
        self.assertEqual(starter['tier'], 'FREE')
        self.assertGreaterEqual(len(starter['records']), 31)

        verification = verify_pack(starter, self.trusted)
        self.assertTrue(verification.valid)
        self.assertEqual(verification.tier, 'FREE')

        installed = self.registry.install(starter)
        self.assertEqual(installed['state'], 'INSTALLED')
        self.assertEqual(installed['tier'], 'FREE')

    def test_build_paid_pack_validates_and_installs(self):
        # Sample: Build paid transaction pack. Check tier PAID, 23 records, and state INSTALLED.
        paid = build_paid_transaction_pack(self.priv)
        self.assertEqual(paid['format'], 'durai-ontology-pack/v2')
        self.assertEqual(paid['id'], 'durai.blueprints.transactions')
        self.assertEqual(paid['tier'], 'PAID')
        self.assertGreaterEqual(len(paid['records']), 23)

        verification = verify_pack(paid, self.trusted)
        self.assertTrue(verification.valid)
        self.assertEqual(verification.tier, 'PAID')

        installed = self.registry.install(paid)
        self.assertEqual(installed['state'], 'INSTALLED')
        self.assertEqual(installed['tier'], 'PAID')


if __name__ == '__main__':
    unittest.main()
