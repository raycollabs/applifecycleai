"""Sample: Write reflection.json into a temp folder with a fake key. Check the loader reads it and redacted hides the key."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from durai import provider_config  # noqa: E402
import set_reflection_key  # noqa: E402


class ReflectionProviderTests(unittest.TestCase):
    def test_file_loads_and_hides_key(self):
        # Sample: Fake key FAKE-123. Load reflection. Check kind, model and coding limits. Redacted has no key.
        with tempfile.TemporaryDirectory() as folder:
            set_reflection_key.write_provider('FAKE-123', folder)
            os.environ['DURAI_PROVIDER_DIR'] = folder
            try:
                config = provider_config.load('reflection')
                limits = provider_config.coding_limits('reflection', config['baseUrl'])
            finally:
                del os.environ['DURAI_PROVIDER_DIR']
        self.assertEqual((config['kind'], config['models']['planning']), ('openai-compatible', 'Beam-1.0-501B-A23B'))
        self.assertEqual((limits['maxTokens'], limits['streaming']), (16000, True))
        self.assertNotIn('FAKE-123', str(provider_config.redacted(config)))

    def test_empty_key_refused(self):
        # Sample: Blank key. ValueError. No file written.
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                set_reflection_key.write_provider('  ', folder)
            self.assertEqual(list(Path(folder).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
