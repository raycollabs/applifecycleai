"""# Sample: Write a fake zai.json in a temp folder. Load it. Check baseUrl and model without the real folder."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from durai import provider_config


def write_provider(folder, provider_id, **changes):
    config = {'id': provider_id, 'kind': 'openai-compatible', 'baseUrl': 'https://api.openai.com/v1',
              'models': {'execution': 'm-exec', 'planning': 'glm-5.3', 'verification': 'm-ver', 'advisor': 'm-adv'},
              'defaults': {'reasoningEffort': 'low', 'thinking': False, 'maxTokens': 2000}, 'apiKey': 'K-1'}
    config.update(changes)
    (Path(folder) / f'{provider_id}.json').write_text(json.dumps(config), encoding='utf-8')


class ProviderConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        previous = os.environ.get('DURAI_PROVIDER_DIR')
        os.environ['DURAI_PROVIDER_DIR'] = self.temp.name
        self.addCleanup(lambda: os.environ.pop('DURAI_PROVIDER_DIR', None) if previous is None
                        else os.environ.__setitem__('DURAI_PROVIDER_DIR', previous))

    def test_load_reads_shared_folder(self):
        # Sample: Write openai-sol.json in a temp folder. Set DURAI_PROVIDER_DIR. Check load returns baseUrl https://api.openai.com/v1.
        write_provider(self.temp.name, 'openai-sol')
        self.assertEqual(provider_config.load('openai-sol')['baseUrl'], 'https://api.openai.com/v1')

    def test_missing_provider_names_file(self):
        # Sample: Load provider zai with no file. Check the error names zai.json and no key text.
        with self.assertRaises(KeyError) as caught:
            provider_config.load('zai')
        self.assertIn('zai.json', str(caught.exception))
        self.assertNotIn('K-1', str(caught.exception))

    def test_redacted_has_no_key(self):
        # Sample: Redact a config with apiKey K-1. Check no K-1 in the result.
        write_provider(self.temp.name, 'zai')
        result = provider_config.redacted(provider_config.load('zai'))
        self.assertNotIn('apiKey', result)
        self.assertNotIn('K-1', json.dumps(result))

    def test_chat_model_uses_role_model(self):
        # Sample: Provider zai with planning glm-5.3. Build the planning model. Check model glm-5.3.
        write_provider(self.temp.name, 'zai')
        model = provider_config.chat_model('zai', 'planning')
        self.assertEqual(model.model_name, 'glm-5.3')
        self.assertEqual(model.reasoning_effort, 'low')
        self.assertEqual(model.max_tokens, 2000)
        self.assertEqual(model.max_retries, 0)

    def test_gemini_uses_native_client(self):
        # Sample: Provider gemini with execution gemini-3.8-flash and maxTokens 2000. Build execution. Check ChatGoogleGenerativeAI, max_output_tokens 2000, retries 0.
        write_provider(self.temp.name, 'gemini', baseUrl='https://generativelanguage.googleapis.com/v1beta/openai/',
                       models={'execution': 'gemini-3.8-flash'})
        model = provider_config.chat_model('gemini', 'execution')
        self.assertEqual(type(model).__name__, 'ChatGoogleGenerativeAI')
        self.assertEqual(model.model, 'gemini-3.8-flash')
        self.assertEqual(model.max_output_tokens, 2000)
        self.assertEqual(model.max_retries, 0)

    def test_gemini_overrides_mapped(self):
        # Sample: Override max_tokens 321 and timeout 7. Check max_output_tokens 321, timeout 7, no ChatOpenAI.
        write_provider(self.temp.name, 'gemini', kind='google', models={'execution': 'gemini-3.8-flash'})
        model = provider_config.chat_model('gemini', 'execution', max_tokens=321, timeout=7, callbacks=[])
        self.assertEqual((model.max_output_tokens, model.timeout, model.max_retries), (321, 7, 0))
        from langchain_openai import ChatOpenAI
        with self.assertRaises(ValueError):
            provider_config.refuse_openai_gemini(ChatOpenAI(model='m', api_key='K-1'), 'gemini')

    def test_openai_compatible_unchanged(self):
        # Sample: Provider zai on api.openai.com. Build planning. Check ChatOpenAI.
        write_provider(self.temp.name, 'zai')
        self.assertEqual(type(provider_config.chat_model('zai', 'planning')).__name__, 'ChatOpenAI')
        self.assertFalse(provider_config.is_gemini('zai', 'openai-compatible', 'https://api.openai.com/v1'))

    def test_reflection_preserves_reasoning_content(self):
        # Sample: Provider reflection captures reasoning_content from streamed chunks and re-emits it on assistant payloads.
        from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
        write_provider(self.temp.name, 'reflection', base_url='https://api.reflection.ai/openai/v1',
                       models={'execution': 'Beam-1.0-501B-A23B'})
        model = provider_config.chat_model('reflection', 'execution')
        self.assertEqual(type(model).__name__, 'ReasoningChatOpenAI')
        chunk = model._convert_chunk_to_generation_chunk(
            {'choices': [{'delta': {'role': 'assistant', 'content': 'ok', 'reasoning_content': 'step 1'}}]},
            AIMessageChunk, None)
        self.assertEqual(chunk.message.additional_kwargs.get('reasoning_content'), 'step 1')
        payload = model._get_request_payload([
            HumanMessage(content='hi'),
            AIMessage(content='ok', additional_kwargs={'reasoning_content': 'step 1'}),
            HumanMessage(content='next'),
        ])
        self.assertEqual(payload['messages'][1]['reasoning_content'], 'step 1')


if __name__ == '__main__':
    unittest.main()
