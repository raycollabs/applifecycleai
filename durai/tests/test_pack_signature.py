"""Sample: Test cryptographic signing and verification of ontology packs."""
import unittest
from durai.common import digest
from durai.pack_signing import (
    TrustedKeyList,
    generate_key_pair,
    sign_pack,
)
from durai.pack_registry import (
    FAILED,
    INSTALLED,
    VERIFIED_COMMUNITY,
    PackRegistry,
)


def sample_records(prefix='R'):
    # Sample: Generate 3 valid records with distinct bodies and matching SHA-256 digests.
    records = []
    for i in range(1, 4):
        body = {'name': f'{prefix}-{i}', 'value': i * 10}
        records.append({
            'id': f'{prefix.lower()}.record.{i}',
            'layer': 'DESIGN',
            'type': 'KNOWLEDGE',
            'body': body,
            'digest': digest(body)
        })
    return records


class PackSignatureTests(unittest.TestCase):
    def setUp(self):
        # Sample: Set up trusted key store and registry for test cases.
        self.priv_key, self.pub_key = generate_key_pair()
        self.trusted_keys = TrustedKeyList()
        self.trusted_keys.add_key('test-key-1', self.pub_key)
        self.registry = PackRegistry(trusted_keys=self.trusted_keys)

    def test_unsigned_pack_cannot_claim_paid_tier(self):
        # Sample: Make a pack with tier PAID and no signature. Install it. Check the state is VERIFIED_COMMUNITY and the label is COMMUNITY.
        pack = {
            'format': 'durai-ontology-pack/v2',
            'id': 'pack-unverified-paid',
            'version': '1',
            'kind': 'APPLICATION',
            'publisher': 'third-party',
            'tier': 'PAID',
            'license_ref': 'durai-pack-commercial-v1',
            'min_shell_version': '1.0.0',
            'records': sample_records('U'),
            'signature': None
        }
        installed = self.registry.install(pack)
        self.assertEqual(installed['state'], VERIFIED_COMMUNITY)
        self.assertEqual(installed['label'], 'COMMUNITY')
        self.assertEqual(installed['tier'], 'COMMUNITY')

    def test_tampered_record_is_refused(self):
        # Sample: Sign pack P-1 with 3 records. Change one record body. Install. Check the state is FAILED and no record is active.
        records = sample_records('P1')
        pack = {
            'format': 'durai-ontology-pack/v2',
            'id': 'P-1',
            'version': '1',
            'kind': 'APPLICATION',
            'publisher': 'durai.io',
            'tier': 'PAID',
            'license_ref': 'durai-pack-commercial-v1',
            'min_shell_version': '1.0.0',
            'records': records
        }
        signed = sign_pack(pack, self.priv_key, key_id='test-key-1')
        signed['records'][0]['body']['value'] = 9999
        result = self.registry.install(signed)
        self.assertEqual(result['state'], FAILED)
        self.assertFalse(result['active'])
        self.assertEqual(len(result['records']), 0)

    def test_signed_pack_installs_once(self):
        # Sample: Install signed pack P-1 version 2 twice. Check one INSTALLED row and two audit rows.
        records = sample_records('P1-v2')
        pack = {
            'format': 'durai-ontology-pack/v2',
            'id': 'P-1',
            'version': '2',
            'kind': 'APPLICATION',
            'publisher': 'durai.io',
            'tier': 'PAID',
            'license_ref': 'durai-pack-commercial-v1',
            'min_shell_version': '1.0.0',
            'records': records
        }
        signed = sign_pack(pack, self.priv_key, key_id='test-key-1')
        first = self.registry.install(signed)
        self.assertEqual(first['state'], INSTALLED)
        second = self.registry.install(signed)
        self.assertEqual(second['state'], INSTALLED)

        installed_rows = [p for p in self.registry.store.list_packs() if p['id'] == 'P-1' and p['version'] == '2']
        self.assertEqual(len(installed_rows), 1)

        audit_rows = [a for a in self.registry.store.list_audit() if a['pack_id'] == 'P-1' and a['version'] == '2']
        self.assertEqual(len(audit_rows), 2)

    def test_trusted_key_rotation_and_revocation(self):
        # Sample: Sign pack with key K-1. Rotate key K-1 to K-2. Check key K-1 is RETIRED and verification succeeds. Revoke K-1. Check verification fails.
        records = sample_records('ROT')
        pack = {
            'format': 'durai-ontology-pack/v2',
            'id': 'pack-rot',
            'version': '1',
            'kind': 'APPLICATION',
            'publisher': 'durai.io',
            'tier': 'PAID',
            'license_ref': 'durai-pack-commercial-v1',
            'min_shell_version': '1.0.0',
            'records': records
        }
        signed = sign_pack(pack, self.priv_key, key_id='test-key-1')
        ver1 = self.registry.verify(signed)
        self.assertTrue(ver1.valid)

        priv2, pub2 = generate_key_pair()
        self.trusted_keys.rotate_key('test-key-1', 'test-key-2', pub2)
        ver2 = self.registry.verify(signed)
        self.assertTrue(ver2.valid)

        self.trusted_keys.revoke_key('test-key-1')
        ver3 = self.registry.verify(signed)
        self.assertFalse(ver3.valid)
        self.assertEqual(ver3.state, FAILED)


if __name__ == '__main__':
    unittest.main()
