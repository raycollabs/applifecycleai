import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from durai import models


class ModelConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root_patch = patch.object(models, 'ROOT', Path(self.folder.name))
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def test_openrouter_requires_key_and_model(self):
        # Select ERNIE. Leave the key empty. Refuse before any factory work.
        with patch.object(models, 'variable', return_value=None):
            self.assertEqual(models.readiness()['state'], 'NEEDS_CONFIGURATION')
            with self.assertRaises(ValueError):
                models.require_configuration()

    def test_readiness_does_not_expose_key(self):
        # Configure a synthetic key. Public readiness must not contain it.
        key = 'synthetic-private-key-for-test'
        with patch.object(models, 'variable', side_effect=lambda name: key if name == 'OPENROUTER_API_KEY' else None):
            public = models.readiness()
            self.assertEqual(public['state'], 'READY')
            self.assertNotIn(key, json.dumps(public))
            self.assertFalse(public['networkCallsMade'])

    def test_remote_plain_http_is_denied(self):
        # Refuse a remote HTTP endpoint before constructing a model client.
        with patch.object(models, 'variable', side_effect=lambda name: 'http://remote.example/v1' if name == 'DURAI_MODEL_BASE_URL' else None):
            with self.assertRaises(ValueError):
                models.configuration()

    def test_local_gateway_uses_gateway_credentials(self):
        # Send only the gateway key to localhost. Keep its upstream key separate.
        variables = {'DURAI_MODEL_BASE_URL': 'http://localhost:4000/v1',
                     'DURAI_MODEL_API_KEY': 'gateway-key', 'OPENROUTER_API_KEY': 'upstream-key'}
        with patch.object(models, 'variable', side_effect=variables.get):
            self.assertEqual(models.configuration()['apiKey'], 'gateway-key')
        variables.pop('DURAI_MODEL_API_KEY')
        with patch.object(models, 'variable', side_effect=variables.get):
            self.assertEqual(models.configuration()['apiKey'], 'local-no-auth')
