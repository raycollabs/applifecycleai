"""Sample: Configure Anthropic with key K-1 at priority 0. Check claude-opus-5-5 first, medium effort, output headroom, and no key in public status."""
import json
import unittest
from langchain_core.messages import HumanMessage
from durai.coding_worker import anthropic_model, configured_model
from durai.model_routes import ANTHROPIC, candidates, providers, public_profiles
from durai.models import DEFAULT_MODEL, OPENROUTER

KEY = 'sk-ant-test-123456'


def config(anthropic_url=ANTHROPIC):
    # Sample: Build OpenRouter, Mistral, and Anthropic profiles with test keys. Return one configuration.
    return {'provider': 'openrouter', 'baseUrl': OPENROUTER, 'apiKey': 'or-test-key', 'model': DEFAULT_MODEL,
            'fallbackModels': [], 'roleModels': {},
            'fallbackProviders': [
                {'id': 'mistral', 'baseUrl': 'https://api.mistral.ai/v1', 'models': ['mistral-medium-3.5'],
                 'enabled': True, 'apiKey': 'mistral-test-key'},
                {'id': 'anthropic', 'kind': 'anthropic', 'baseUrl': anthropic_url, 'models': ['claude-opus-5-5'],
                 'priority': 0, 'enabled': True, 'apiKey': KEY}]}


class AnthropicRouteTests(unittest.TestCase):
    def test_anthropic_route_is_first(self):
        # Sample: Configure OpenRouter, Mistral, and Anthropic. Check that the first coding route is anthropic claude-opus-5-5.
        routes = candidates('coding', config())
        self.assertEqual([(route['id'], route['model']) for route in routes],
                         [('anthropic', 'claude-opus-5-5'), ('openrouter', DEFAULT_MODEL), ('mistral', 'mistral-medium-3.5')])
        self.assertEqual(type(configured_model(profile=routes[0])).__name__, 'ChatAnthropic')
        self.assertEqual(type(configured_model(profile=routes[1])).__name__, 'ChatOpenAI')

    def test_anthropic_endpoint_is_fixed(self):
        # Sample: Configure anthropic with https://example.com. Check refusal.
        with self.assertRaisesRegex(ValueError, 'Anthropic provider requires'):
            providers(config('https://example.com'))

    def test_payload_has_effort_and_no_sampling(self):
        # Sample: Build one user request. Check claude-opus-5-5, medium effort, 32768 output tokens, and no sampling or fallback field.
        model = anthropic_model('claude-opus-5-5', KEY)
        payload = model._get_request_payload([HumanMessage('Book 2 lots of 100.')])
        self.assertEqual(payload['model'], 'claude-opus-5-5')
        self.assertEqual(payload['output_config'], {'effort': 'medium'})
        self.assertEqual(payload['max_tokens'], 32768)
        self.assertEqual(model.max_retries, 0)
        for name in ('temperature', 'top_p', 'top_k', 'thinking', 'betas', 'fallbacks'):
            self.assertNotIn(name, payload)

    def test_public_status_hides_key(self):
        # Sample: Configure key sk-ant-test-123456. Read public profiles. Check that the key text is absent.
        profiles = public_profiles(config())
        self.assertEqual(profiles[0]['id'], 'anthropic')
        self.assertTrue(profiles[0]['keyConfigured'])
        self.assertNotIn(KEY, json.dumps(profiles))


if __name__ == '__main__':
    unittest.main()
