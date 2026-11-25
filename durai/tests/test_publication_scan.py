"""Prevent a second provider's key from entering publication history."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock


class PublicationScanTests(unittest.TestCase):
    def test_provider_key_is_rejected_from_history(self):
        # Sample: Put a synthetic Mistral key in a fake blob. Reject it without printing it.
        spec = importlib.util.spec_from_file_location('publication_scan_test', Path(__file__).resolve().parents[1] / 'tools/publish_github.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            module.ROOT = Path(folder)
            private = module.ROOT / 'durai/private'
            private.mkdir(parents=True)
            key = 'synthetic-provider-key-never-publish'
            (private / 'model.json').write_text(json.dumps({'fallbackProviders': [{'id': 'mistral', 'apiKey': key}]}))
            module.git = Mock(side_effect=[b'object source.py\n', b'blob', key.encode()])
            with self.assertRaisesRegex(ValueError, 'Potential credential found in history: source.py') as caught:
                module.scan_history()
            self.assertNotIn(key, str(caught.exception))
