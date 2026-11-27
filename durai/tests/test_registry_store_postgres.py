"""Sample: Run RegistryStore contract tests on InMemoryRegistryStore and PostgresRegistryStore, including restart persistence."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pg_support  # noqa: E402
from durai.adapters.pack_registry_store.postgres import PostgresRegistryStore  # noqa: E402
from durai.common import Conflict, digest  # noqa: E402
from durai.pack_registry import (  # noqa: E402
    ACTIVE,
    INSTALLED,
    REVOKED,
    SUPERSEDED,
    InMemoryRegistryStore,
    PackRegistry,
)
from durai.pack_signing import TrustedKeyList, generate_key_pair, sign_pack  # noqa: E402


def make_pack(pack_id='P-1', version='1', value=10, priv_key=None):
    # Sample: Build signed pack P-1 version 1 with 1 record holding value 10.
    body = {'name': f'{pack_id}-v{version}', 'value': value}
    doc = {
        'format': 'durai-ontology-pack/v2',
        'id': pack_id,
        'version': version,
        'kind': 'APPLICATION',
        'publisher': 'durai.io',
        'tier': 'PAID',
        'license_ref': 'durai-pack-commercial-v1',
        'min_shell_version': '1.0.0',
        'records': [{'id': f'{pack_id}.r1', 'layer': 'DESIGN', 'type': 'KNOWLEDGE', 'body': body, 'digest': digest(body)}],
    }
    return sign_pack(doc, priv_key, key_id='k1') if priv_key else doc


class RegistryStoreContract:
    """Sample: Mix into a test case. Set self.store in make_store and run shared registry tests."""

    def make_store(self):
        raise NotImplementedError

    def setUp(self):
        # Sample: Generate Ed25519 key pair k1 and bind PackRegistry to self.make_store().
        self.priv, self.pub = generate_key_pair()
        self.keys = TrustedKeyList()
        self.keys.add_key('k1', self.pub)
        self.store = self.make_store()
        self.registry = PackRegistry(store=self.store, trusted_keys=self.keys)

    def test_install_and_read_back(self):
        # Sample: Install signed pack P-1 version 1. Read it back via registry.get and check state INSTALLED.
        pack = make_pack('P-1', '1', 10, self.priv)
        installed = self.registry.install(pack)
        self.assertEqual(installed['state'], INSTALLED)
        fetched = self.registry.get('P-1', '1')
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched['digest'], installed['digest'])

    def test_same_digest_twice_leaves_one_entry_and_replay_audit(self):
        # Sample: Install signed pack P-1 version 1 twice. Check one pack row and two audit rows with PACK_INSTALL_REPLAY.
        pack = make_pack('P-1', '1', 10, self.priv)
        self.registry.install(pack)
        self.registry.install(pack)
        rows = [p for p in self.store.list_packs() if p['id'] == 'P-1' and p['version'] == '1']
        self.assertEqual(len(rows), 1)
        audit = self.store.list_audit()
        self.assertEqual([a['action'] for a in audit], ['PACK_INSTALL', 'PACK_INSTALL_REPLAY'])

    def test_changed_document_same_version_raises_conflict(self):
        # Sample: Install P-1 version 1 with value 10. Install P-1 version 1 with value 20. Check Conflict.
        self.registry.install(make_pack('P-1', '1', 10, self.priv))
        with self.assertRaises(Conflict):
            self.registry.install(make_pack('P-1', '1', 20, self.priv))

    def test_activate_supersedes_old_active_version(self):
        # Sample: Install and activate P-1 v1, install and activate P-1 v2. Check v1 is SUPERSEDED and v2 is ACTIVE.
        self.registry.install(make_pack('P-1', '1', 10, self.priv))
        self.registry.activate('P-1', '1')
        self.registry.install(make_pack('P-1', '2', 20, self.priv))
        self.registry.activate('P-1', '2')
        self.assertEqual(self.store.get_pack('P-1', '1')['state'], SUPERSEDED)
        self.assertEqual(self.store.get_pack('P-1', '2')['state'], ACTIVE)

    def test_revoke_marks_revoked_and_unusable(self):
        # Sample: Install P-1 v1 and revoke it. Check state REVOKED and is_usable_for_build False.
        self.registry.install(make_pack('P-1', '1', 10, self.priv))
        self.registry.revoke('P-1', '1')
        self.assertEqual(self.store.get_pack('P-1', '1')['state'], REVOKED)
        self.assertFalse(self.registry.is_usable_for_build('P-1', '1'))


class InMemoryRegistryStoreTests(RegistryStoreContract, unittest.TestCase):
    def make_store(self):
        # Sample: Return a fresh InMemoryRegistryStore.
        return InMemoryRegistryStore()


class PostgresRegistryStoreTests(RegistryStoreContract, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Start disposable PostgreSQL 17 container when Docker is ready. Skip otherwise.
        if not pg_support.docker_ready():
            raise unittest.SkipTest('Docker is not available')
        cls.connect = staticmethod(pg_support.start_postgres())

    @classmethod
    def tearDownClass(cls):
        # Sample: Stop and remove the disposable PostgreSQL container.
        pg_support.stop_postgres()

    def make_store(self):
        # Sample: Truncate pack_registry tables and return a clean PostgresRegistryStore.
        store = PostgresRegistryStore(self.connect)
        store.ensure_schema()
        with self.connect() as conn:
            conn.execute('TRUNCATE TABLE pack_registry_pack, pack_registry_audit RESTART IDENTITY')
        return store

    def test_restart_new_store_reads_earlier_installs(self):
        # Sample: Install P-1 v1 with store 1. Create store 2 on the same database. Check store 2 reads P-1 v1 and its audit row.
        self.registry.install(make_pack('P-1', '1', 10, self.priv))
        restarted_store = PostgresRegistryStore(self.connect)
        restarted_registry = PackRegistry(store=restarted_store, trusted_keys=self.keys)
        loaded = restarted_registry.get('P-1', '1')
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded['state'], INSTALLED)
        self.assertEqual(len(restarted_store.list_audit()), 1)


if __name__ == '__main__':
    unittest.main()
