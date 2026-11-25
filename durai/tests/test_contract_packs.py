"""Sample: Run PackContractTests on the PackRegistry of agent A2. Check community labels, idempotent install, tamper refusal and revoked use."""
import importlib.util
import sys
import unittest
from pathlib import Path

if importlib.util.find_spec('durai') is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
HAS_REGISTRY = importlib.util.find_spec('durai.pack_registry') is not None and importlib.util.find_spec('cryptography') is not None
if HAS_REGISTRY:
    from durai.common import digest
    from durai.pack_registry import PackRegistry
    from durai.pack_signing import TrustedKeyList, generate_key_pair, export_public_key_hex, sign_pack


def pack(pack_id='P-1', version='1', tier='PAID', name='N1'):
    # Sample: Make pack P-1 version 1 with tier PAID and one record R-N1 with its digest.
    body = {'name': name}
    return {'id': pack_id, 'version': version, 'kind': 'APPLICATION', 'tier': tier,
            'records': [{'id': 'R-' + name, 'layer': 'DESIGN', 'type': 'KNOWLEDGE', 'body': body, 'digest': digest(body)}]}


@unittest.skipUnless(HAS_REGISTRY, 'durai.pack_registry is not merged into this tree yet')
class PackContractTests(unittest.TestCase):
    def setUp(self):
        # Sample: Make a trusted key K-1. Make a registry that trusts K-1.
        self.private, public = generate_key_pair()
        keys = TrustedKeyList()
        keys.add_key('K-1', export_public_key_hex(public))
        self.registry = PackRegistry(trusted_keys=keys)

    def test_unsigned_pack_is_community(self):
        # Sample: Install an unsigned pack with tier PAID. Check state VERIFIED_COMMUNITY and label COMMUNITY.
        entry = self.registry.install(pack())
        self.assertEqual(('VERIFIED_COMMUNITY', 'COMMUNITY'), (entry['state'], entry['label']))

    def test_signed_pack_installs(self):
        # Sample: Sign pack P-1 with K-1. Install. Check state INSTALLED and tier PAID.
        entry = self.registry.install(sign_pack(pack(), self.private, 'K-1'))
        self.assertEqual(('INSTALLED', 'PAID'), (entry['state'], entry['tier']))

    def test_install_is_idempotent_on_digest(self):
        # Sample: Install the same signed pack twice. Check one pack entry and a replay audit row.
        document = sign_pack(pack(), self.private, 'K-1')
        self.registry.install(document)
        self.registry.install(document)
        self.assertEqual(1, len([p for p in self.registry.store.list_packs() if p['id'] == 'P-1']))
        self.assertIn('PACK_INSTALL_REPLAY', [row['action'] for row in self.registry.store.list_audit()])

    def test_tampered_record_ends_failed(self):
        # Sample: Sign P-1. Change a record body. Install. Check state FAILED and the pack is not usable for a build.
        document = sign_pack(pack(), self.private, 'K-1')
        document['records'][0]['body'] = {'name': 'changed'}
        entry = self.registry.install(document)
        self.assertEqual('FAILED', entry['state'])
        self.assertFalse(self.registry.is_usable_for_build('P-1'))

    def test_revoked_pack_is_not_usable_for_a_build(self):
        # Sample: Install P-1. Revoke it. Check is_usable_for_build is False and the state is REVOKED.
        self.registry.install(sign_pack(pack(), self.private, 'K-1'))
        self.registry.revoke('P-1')
        self.assertFalse(self.registry.is_usable_for_build('P-1'))
        self.assertEqual('REVOKED', self.registry.get('P-1')['state'])

    def test_unknown_pack_is_not_usable_for_a_build(self):
        # Sample: Ask is_usable_for_build for P-9. Check False. Ask get for P-9. Check None.
        self.assertFalse(self.registry.is_usable_for_build('P-9'))
        self.assertIsNone(self.registry.get('P-9'))


if __name__ == '__main__':
    unittest.main()
