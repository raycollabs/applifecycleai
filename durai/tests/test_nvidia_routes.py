"""Sample: Put NVIDIA after Claude. Build its streaming tool client. Check requested options and no key in public profiles."""
import json
import unittest
from langchain_core.messages import HumanMessage
from durai.coding_worker import configured_model
from durai.model_routes import candidates, public_profiles
from test_anthropic_routes import config

MODEL = 'nvidia/nemotron-3-ultra-550b-a55b'
KEY = 'nvapi-synthetic-test-key'


def routes_config():
    # Sample: Add NVIDIA at priority 10 after Claude at priority 0. Keep OpenRouter at priority 50.
    value = config()
    value['fallbackProviders'].append({'id': 'nvidia', 'baseUrl': 'https://integrate.api.nvidia.com/v1',
        'models': [MODEL], 'priority': 10, 'enabled': True, 'apiKey': KEY})
    return value


class NvidiaRouteTests(unittest.TestCase):
    def test_nvidia_constructor_uses_requested_options(self):
        # Sample: Build NVIDIA with a synthetic key. Check its endpoint, sampling, thinking, tool parsing, and zero retries.
        profile = candidates('coding', routes_config())[1]
        model = configured_model(profile=profile)
        payload = model._get_request_payload([HumanMessage('Book 2 lots of 100.')])
        self.assertEqual(str(model.openai_api_base), profile['baseUrl'])
        self.assertEqual(model.openai_api_key.get_secret_value(), KEY)
        self.assertEqual(payload['model'], MODEL)
        self.assertEqual(payload['temperature'], 1)
        self.assertEqual(payload['top_p'], 0.95)
        self.assertEqual(payload.get('max_completion_tokens', payload.get('max_tokens')), 16384)
        self.assertEqual(payload['extra_body'], {'chat_template_kwargs': {'enable_thinking': True, 'force_nonempty_content': True}})
        self.assertTrue(payload['stream'])
        self.assertEqual(model.max_retries, 0)

    def test_mistral_on_nvidia_has_no_nemotron_thinking_options(self):
        # Sample: Build mistralai/mistral-large-4-1t-a52b on NVIDIA. Check sampling 1 and 0.95. Check no chat_template_kwargs.
        profile = dict(candidates('coding', routes_config())[1], model='mistralai/mistral-large-4-1t-a52b')
        payload = configured_model(profile=profile)._get_request_payload([HumanMessage('Book 2 lots of 100.')])
        self.assertEqual(payload['model'], 'mistralai/mistral-large-4-1t-a52b')
        self.assertEqual((payload['temperature'], payload['top_p']), (1, 0.95))
        self.assertNotIn('extra_body', payload)

    def test_second_provider_is_selected_after_claude(self):
        # Sample: Read four synthetic coding routes. Check Claude, NVIDIA, OpenRouter, then Mistral.
        routes = candidates('coding', routes_config())
        self.assertEqual([row['id'] for row in routes], ['anthropic', 'nvidia', 'openrouter', 'mistral'])
        self.assertEqual(routes[1]['model'], MODEL)

    def test_public_profile_has_no_key(self):
        # Sample: Read public profiles with a synthetic NVIDIA key. Check keyConfigured true and no key bytes.
        profiles = public_profiles(routes_config())
        self.assertTrue(profiles[1]['keyConfigured'])
        self.assertNotIn(KEY, json.dumps(profiles))


if __name__ == '__main__':
    unittest.main()
