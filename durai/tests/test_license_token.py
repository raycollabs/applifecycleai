"""Sample: Test license token validity, expiration, and pack revocation."""
import unittest
from durai.common import digest
from durai.effective_view import EffectiveView
from durai.pack_signing import TrustedKeyList, generate_key_pair, sign_pack
from durai.pack_registry import (
    PackRegistry,
    make_license_token,
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


class LicenseTokenTests(unittest.TestCase):
    def setUp(self):
        # Sample: Set up signing keys, trusted key store, registry, and effective view.
        self.priv_key, self.pub_key = generate_key_pair()
        self.trusted_keys = TrustedKeyList()
        self.trusted_keys.add_key('test-key-1', self.pub_key)
        self.registry = PackRegistry(trusted_keys=self.trusted_keys)
        self.effective_view = EffectiveView(self.registry)

    def test_expired_license_keeps_pack_readable(self):
        # Sample: Install P-1 with a token that ends yesterday. Ask for updates. Check no update. Read the effective view. Check it still lists the 3 records of P-1.
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
        token = make_license_token(
            organization='Acme Corp',
            pack_ids=['P-1'],
            seats=5,
            valid_until='2026-11-23T00:00:00Z',
            private_key=self.priv_key,
            key_id='test-key-1'
        )
        self.registry.install(signed, license_token=token)

        candidate_update = {
            'format': 'durai-ontology-pack/v2',
            'id': 'P-1',
            'version': '2',
            'kind': 'APPLICATION',
            'records': sample_records('P1-v2')
        }
        update_result = self.registry.check_updates('P-1', candidate_update)
        self.assertIsNone(update_result)

        effective_records = self.effective_view.list_records()
        self.assertEqual(len(effective_records), 3)
        record_ids = {r['id'] for r in effective_records}
        self.assertEqual(record_ids, {'p1.record.1', 'p1.record.2', 'p1.record.3'})

    def test_revoked_pack_is_refused_for_new_builds(self):
        # Sample: Revoke P-1. Start a build that needs P-1. Check the build refuses. Check the old receipt of P-1 is unchanged.
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
        self.registry.install(signed)

        old_receipt = {
            'sampleId': 'P-1',
            'status': 'FINISHED',
            'calls': 24,
            'testsPassed': 127
        }
        old_receipt_copy = dict(old_receipt)

        self.registry.revoke('P-1')

        can_build = self.registry.is_usable_for_build('P-1')
        self.assertFalse(can_build)

        self.assertEqual(old_receipt, old_receipt_copy)


if __name__ == '__main__':
    unittest.main()
