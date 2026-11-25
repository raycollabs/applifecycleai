"""# Sample: Build coding models from fake provider files. Check limits 32768 tokens and 600 s. Make zero model calls."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from durai.coding_worker import configured_model


def write_provider(folder, provider_id, base_url, kind='openai-compatible', defaults=None):
    # Sample: Write deepseek.json with key K-1 and no coding record. Return nothing.
    config = {'id': provider_id, 'kind': kind, 'baseUrl': base_url, 'models': {'execution': 'm-exec'},
              'defaults': defaults or {}, 'apiKey': 'K-1'}
    (Path(folder) / f'{provider_id}.json').write_text(json.dumps(config), encoding='utf-8')


def profile(provider_id, base_url, model):
    # Sample: Build a deepseek route with key K-1. Return the profile dict.
    return {'id': provider_id, 'kind': 'openai-compatible', 'model': model, 'apiKey': 'K-1', 'baseUrl': base_url}


class CodingModelLimitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        previous = os.environ.get('DURAI_PROVIDER_DIR')
        os.environ['DURAI_PROVIDER_DIR'] = self.temp.name
        self.addCleanup(lambda: os.environ.pop('DURAI_PROVIDER_DIR', None) if previous is None
                        else os.environ.__setitem__('DURAI_PROVIDER_DIR', previous))

    def test_deepseek_coding_model_uses_benchmark_limits(self):
        # Sample: Configure provider deepseek with no coding record. Build the coding model. Check max_tokens 32768, timeout 600, streaming true, thinking enabled, max_retries 0.
        write_provider(self.temp.name, 'deepseek', 'https://api.deepseek.com')
        model = configured_model(profile=profile('deepseek', 'https://api.deepseek.com', 'deepseek-flash'))
        self.assertEqual((model.max_tokens, model.request_timeout, model.streaming, model.max_retries),
                         (32768, 600, True, 0))
        self.assertTrue(model.stream_usage)
        self.assertEqual(model.extra_body, {'thinking': {'type': 'enabled'}})
        self.assertIsNone(model.temperature)

    def test_gemini_coding_model_uses_benchmark_limits(self):
        # Sample: Configure provider gemini. Build the coding model. Check ChatGoogleGenerativeAI, max_output_tokens 32768, timeout 600.
        url = 'https://generativelanguage.googleapis.com/v1beta/openai'
        write_provider(self.temp.name, 'gemini', url)
        model = configured_model(profile=profile('gemini', url, 'gemini-3.8-flash'))
        self.assertEqual(type(model).__name__, 'ChatGoogleGenerativeAI')
        self.assertEqual((model.max_output_tokens, model.timeout, model.max_retries), (32768, 600, 0))

    def test_provider_coding_record_overrides_defaults(self):
        # Sample: Set coding maxTokens 16000 and streaming false for provider zai. Build the coding model. Check max_tokens 16000, streaming false, temperature 0, no thinking.
        write_provider(self.temp.name, 'zai', 'https://api.z.ai/v1',
                       defaults={'coding': {'maxTokens': 16000, 'streaming': False}})
        model = configured_model(profile=profile('zai', 'https://api.z.ai/v1', 'glm-5.3'))
        self.assertEqual((model.max_tokens, model.request_timeout, model.streaming), (16000, 600, False))
        self.assertEqual(model.temperature, 0)
        self.assertFalse(model.extra_body)
