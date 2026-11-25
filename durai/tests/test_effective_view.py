"""Sample: Test lazy caching and invalidation of the effective ontology view."""
import unittest
from durai.common import digest
from durai.effective_view import EffectiveView
from durai.pack_signing import TrustedKeyList, generate_key_pair, sign_pack
from durai.pack_registry import PackRegistry


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


class EffectiveViewTests(unittest.TestCase):
    def setUp(self):
        # Sample: Set up signing keys, trusted key store, registry, and effective view.
        self.priv_key, self.pub_key = generate_key_pair()
        self.trusted_keys = TrustedKeyList()
        self.trusted_keys.add_key('test-key-1', self.pub_key)
        self.registry = PackRegistry(trusted_keys=self.trusted_keys)
        self.effective_view = EffectiveView(self.registry)

    def test_view_is_cached_until_pack_change(self):
        # Sample: Read the view twice. Check one computation. Install P-2. Read again. Check a second computation.
        records_p1 = sample_records('P1')
        pack_p1 = {
            'format': 'durai-ontology-pack/v2',
            'id': 'P-1',
            'version': '1',
            'kind': 'APPLICATION',
            'publisher': 'durai.io',
            'tier': 'PAID',
            'license_ref': 'durai-pack-commercial-v1',
            'min_shell_version': '1.0.0',
            'records': records_p1
        }
        signed_p1 = sign_pack(pack_p1, self.priv_key, key_id='test-key-1')
        self.registry.install(signed_p1)

        first_read = self.effective_view.get_view()
        self.assertEqual(len(first_read['records_list']), 3)
        self.assertEqual(self.effective_view.computation_count, 1)

        second_read = self.effective_view.get_view()
        self.assertEqual(len(second_read['records_list']), 3)
        self.assertEqual(self.effective_view.computation_count, 1)

        records_p2 = sample_records('P2')
        pack_p2 = {
            'format': 'durai-ontology-pack/v2',
            'id': 'P-2',
            'version': '1',
            'kind': 'APPLICATION',
            'publisher': 'durai.io',
            'tier': 'PAID',
            'license_ref': 'durai-pack-commercial-v1',
            'min_shell_version': '1.0.0',
            'records': records_p2
        }
        signed_p2 = sign_pack(pack_p2, self.priv_key, key_id='test-key-1')
        self.registry.install(signed_p2)

        third_read = self.effective_view.get_view()
        self.assertEqual(len(third_read['records_list']), 6)
        self.assertEqual(self.effective_view.computation_count, 2)


if __name__ == '__main__':
    unittest.main()
