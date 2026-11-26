"""Sample: Make a throwaway key in a temp folder. Sign the starter egress pack. Check it verifies and the folder rule refuses the repository."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import make_publisher_key  # noqa: E402
import sign_egress_pack  # noqa: E402
from durai.pack_signing import generate_key_pair, load_private_key_hex  # noqa: E402


class PublisherToolTests(unittest.TestCase):
    def test_key_made_outside_repository(self):
        # Sample: Make a key in a temp folder. The public key is 64 hex characters and the private file exists.
        with tempfile.TemporaryDirectory() as folder:
            public = make_publisher_key.make(folder)
            self.assertEqual(len(public), 64)
            self.assertTrue((Path(folder) / 'durai-publisher-private.hex').is_file())

    def test_key_inside_repository_refused(self):
        # Sample: Ask for a key folder inside the repository. ValueError.
        with self.assertRaises(ValueError):
            make_publisher_key.make(Path(make_publisher_key.ROOT) / 'private' / 'k')

    def test_signed_pack_verifies_and_holds_one_record(self):
        # Sample: Sign with a throwaway key. One record sandbox.egress. The pack signature names key durai-publisher-2026.
        private_key, _ = generate_key_pair()
        signed = sign_egress_pack.build(private_key)
        self.assertEqual([r['id'] for r in signed['records']], ['sandbox.egress'])
        self.assertEqual(signed['signature']['key_id'], 'durai-publisher-2026')

    def test_key_file_round_trip(self):
        # Sample: Make a key, load the private file, sign, and check the public key matches the one printed by make.
        with tempfile.TemporaryDirectory() as folder:
            public = make_publisher_key.make(folder)
            private_key = load_private_key_hex((Path(folder) / 'durai-publisher-private.hex').read_text(encoding='utf-8').strip())
            from durai.pack_signing import export_public_key_hex
            self.assertEqual(export_public_key_hex(private_key.public_key()), public)


if __name__ == '__main__':
    unittest.main()
