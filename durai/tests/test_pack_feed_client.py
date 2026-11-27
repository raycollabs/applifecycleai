"""Sample: Build temporary feed folders with signed, tampered, revoked, and older packs. Run PackFeedClient.sync() and check each goal."""
import json
from pathlib import Path
import tempfile
import unittest

from durai.common import digest
from durai.pack_feed_client import PackFeedClient
from durai.pack_registry import FAILED, INSTALLED, REVOKED, PackRegistry
from durai.pack_signing import TrustedKeyList, generate_key_pair, sign_pack


def build_signed_pack(pack_id, version, value, priv_key, key_id='feed-k1'):
    # Sample: Build and sign pack P-1 version 1.0.0 with 1 record holding value 10.
    body = {'name': f'{pack_id}-{version}', 'value': value}
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
    return sign_pack(doc, priv_key, key_id=key_id)


def write_feed(folder: Path, entries: list, files: dict):
    # Sample: Write feed.json and pack JSON files into temporary feed folder F-1.
    for filename, doc in files.items():
        (folder / filename).write_text(json.dumps(doc), encoding='utf-8')
    (folder / 'feed.json').write_text(json.dumps({'format': 'durai-pack-feed/v1', 'entries': entries}), encoding='utf-8')


class PackFeedClientTests(unittest.TestCase):
    def setUp(self):
        # Sample: Set up signing key pair feed-k1, trusted key store, and in-memory PackRegistry.
        self.priv, self.pub = generate_key_pair()
        self.keys = TrustedKeyList()
        self.keys.add_key('feed-k1', self.pub)
        self.registry = PackRegistry(trusted_keys=self.keys)

    def test_new_pack_installs(self):
        # Sample: Put signed pack P-1 version 1.0.0 in feed folder. Sync. Check P-1 is INSTALLED and listed.
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            p1 = build_signed_pack('P-1', '1.0.0', 10, self.priv)
            write_feed(folder, [{'seq': 1, 'pack_id': 'P-1', 'version': '1.0.0', 'path': 'p1.json'}], {'p1.json': p1})
            client = PackFeedClient(self.registry, folder)
            self.assertEqual(len(client.list_available()), 1)
            result = client.sync()
            self.assertEqual(len(result['installed']), 1)
            self.assertEqual(self.registry.get('P-1', '1.0.0')['state'], INSTALLED)

    def test_replay_changes_nothing(self):
        # Sample: Sync the same feed twice. Check first sync installs 1 pack and second sync changes nothing with 0 new audit rows.
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            p1 = build_signed_pack('P-1', '1.0.0', 10, self.priv)
            write_feed(folder, [{'seq': 1, 'pack_id': 'P-1', 'version': '1.0.0', 'path': 'p1.json'}], {'p1.json': p1})
            client = PackFeedClient(self.registry, folder)
            client.sync()
            audit_before = len(self.registry.store.list_audit())
            packs_before = list(self.registry.store.list_packs())

            second = client.sync()
            self.assertEqual(len(second['installed']), 0)
            self.assertEqual(len(second['replayed']), 1)
            self.assertEqual(len(self.registry.store.list_audit()), audit_before)
            self.assertEqual(list(self.registry.store.list_packs()), packs_before)

    def test_tampered_pack_ends_failed_and_not_usable_for_build(self):
        # Sample: Tamper with record body of P-bad after signing. Sync. Check failed report and is_usable_for_build False.
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            pbad = build_signed_pack('P-bad', '1.0.0', 10, self.priv)
            pbad['records'][0]['body']['value'] = 999
            write_feed(folder, [{'seq': 1, 'pack_id': 'P-bad', 'version': '1.0.0', 'path': 'pbad.json'}], {'pbad.json': pbad})
            client = PackFeedClient(self.registry, folder)
            result = client.sync()
            self.assertEqual(len(result['failed']), 1)
            self.assertEqual(result['failed'][0]['state'], FAILED)
            self.assertFalse(self.registry.is_usable_for_build('P-bad', '1.0.0'))

    def test_revocation_entry_revokes_locally(self):
        # Sample: Install P-1 v1.0.0, then sync feed with seq 2 REVOKE for P-1 v1.0.0. Check state REVOKED and unusable for build.
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            p1 = build_signed_pack('P-1', '1.0.0', 10, self.priv)
            write_feed(
                folder,
                [
                    {'seq': 1, 'pack_id': 'P-1', 'version': '1.0.0', 'path': 'p1.json'},
                    {'seq': 2, 'pack_id': 'P-1', 'version': '1.0.0', 'action': 'REVOKE'},
                ],
                {'p1.json': p1},
            )
            client = PackFeedClient(self.registry, folder)
            result = client.sync()
            self.assertEqual(len(result['revoked']), 1)
            self.assertEqual(self.registry.get('P-1', '1.0.0')['state'], REVOKED)
            self.assertFalse(self.registry.is_usable_for_build('P-1', '1.0.0'))

    def test_older_version_than_installed_is_ignored(self):
        # Sample: Install P-1 version 2.0.0. Sync feed offering P-1 version 1.0.0. Check 1.0.0 is skipped and not installed.
        self.registry.install(build_signed_pack('P-1', '2.0.0', 20, self.priv))
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            p1_old = build_signed_pack('P-1', '1.0.0', 10, self.priv)
            write_feed(folder, [{'seq': 1, 'pack_id': 'P-1', 'version': '1.0.0', 'path': 'p1_old.json'}], {'p1_old.json': p1_old})
            client = PackFeedClient(self.registry, folder)
            result = client.sync()
            self.assertEqual(len(result['installed']), 0)
            self.assertEqual(result['skipped'][0]['reason'], 'older_version')
            self.assertIsNone(self.registry.store.get_pack('P-1', '1.0.0'))


if __name__ == '__main__':
    unittest.main()
