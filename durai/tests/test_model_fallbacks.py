"""Provider recovery goals. Synthetic clients make no paid or external calls."""
import json
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from durai import models
from durai.common import digest
from durai.model_fallbacks import propose, ModelAttemptsFailed
from durai.model_routes import candidates, catalog_status, configure_fallbacks, configure_provider, configure_provider_key, public_profiles, validate_models

SOURCE = b'print(2)\n'
CANDIDATE = b'print(200)\n'


class ProviderError(RuntimeError):
    def __init__(self, code):
        # Sample: Simulate provider 429 with private response text. Keep only the safe code.
        self.status_code = code
        super().__init__('synthetic-private-provider-response')


class FallbackTests(unittest.TestCase):
    def setUp(self):
        # Sample: Configure two synthetic model IDs under a temporary private settings root.
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root = Path(self.folder.name)
        directory = root / 'private'
        directory.mkdir()
        (directory / 'model.json').write_text(json.dumps({'model': 'primary', 'fallbackModels': ['secondary'],
            'baseUrl': 'https://openrouter.ai/api/v1', 'apiKey': 'synthetic-primary-key'}))
        for target in ('durai.models.ROOT', 'durai.model_fallbacks.ROOT'):
            guard = patch(target, root)
            guard.start()
            self.addCleanup(guard.stop)
        guard = patch('durai.models.variable', return_value=None)
        guard.start()
        self.addCleanup(guard.stop)

    def test_rate_limit_uses_next_model_from_clean_source(self):
        # Sample: Let M-1 alter source A and fail. Check that M-2 receives source A again.
        folders = []
        def worker(folder, model, task):
            # Sample: Capture a failed edit. Return a candidate only from the second fresh workspace.
            path = Path(folder, 'workspace/program.py')
            self.assertEqual(path.read_bytes(), SOURCE)
            folders.append(folder)
            if len(folders) == 1:
                path.write_bytes(b'partial edit')
                raise ProviderError(429)
            return {'source': CANDIDATE, 'digest': digest(CANDIDATE)}
        result = propose(SOURCE, 'Store 200 shares.', model_factory=Mock(return_value=object()), worker=worker)
        self.assertEqual(result['model'], 'secondary')
        self.assertTrue(result['fallbackUsed'])
        self.assertNotEqual(folders[0], folders[1])
        self.assertEqual(result['modelAttempts'][0]['statusCode'], 429)

    def test_authentication_failure_stops_chain(self):
        # Sample: Return 401 from M-1. Check that M-2 is never called and text stays private.
        worker = Mock(side_effect=ProviderError(401))
        with self.assertRaises(ModelAttemptsFailed) as caught:
            propose(SOURCE, 'Store shares.', model_factory=Mock(return_value=object()), worker=worker)
        self.assertEqual(worker.call_count, 1)
        self.assertEqual(caught.exception.attempts[0]['outcome'], 'STOPPED')
        self.assertNotIn('synthetic-private-provider-response', json.dumps(caught.exception.attempts))

    def test_exhaustion_records_all_attempts(self):
        # Sample: Fail five models with 503. End with five records and no candidate.
        configure_fallbacks(['two', 'three', 'four', 'five'])
        worker = Mock(side_effect=ProviderError(503))
        with self.assertRaises(ModelAttemptsFailed) as caught:
            propose(SOURCE, 'Store shares.', model_factory=Mock(return_value=object()), worker=worker)
        self.assertEqual(len(caught.exception.attempts), 5)
        self.assertEqual(worker.call_count, 5)

    def test_candidate_failure_does_not_switch_model(self):
        # Sample: Return runnable but wrong 2-share code. Leave business judgment to the evaluator.
        worker = Mock(return_value={'source': SOURCE, 'digest': digest(SOURCE)})
        result = propose(SOURCE, 'Store 200 shares.', model_factory=Mock(return_value=object()), worker=worker)
        self.assertEqual(result['source'], SOURCE)
        self.assertFalse(result['fallbackUsed'])
        self.assertEqual(worker.call_count, 1)

    def test_configuration_rejects_more_than_five_models(self):
        # Sample: Supply six IDs. Refuse before changing the two-model settings.
        with self.assertRaises(ValueError):
            configure_fallbacks(['two', 'three', 'four', 'five', 'six'])
        self.assertEqual(len(candidates()), 2)

    def test_configuration_rejects_duplicate_models(self):
        # Sample: Repeat the primary in a fallback slot. Refuse duplicate routing.
        with self.assertRaises(ValueError):
            validate_models(['primary', 'primary'])
        with self.assertRaises(ValueError):
            configure_fallbacks(['primary'])

    def test_public_status_contains_no_model_key(self):
        # Sample: Show both model IDs and key presence. Hide the synthetic credential bytes.
        public = models.readiness()
        self.assertEqual(public['fallbackModels'], ['secondary'])
        self.assertEqual(public['providers'][0]['models'], ['primary', 'secondary'])
        self.assertNotIn('synthetic-primary-key', json.dumps(public))
        self.assertFalse(public['networkCallsMade'])

    def test_provider_fallback_keeps_credentials_separate(self):
        # Sample: Exhaust OpenRouter. Use P-2's endpoint and key for its next model only.
        configure_provider('local-vllm', 'http://127.0.0.1:8000/v1', ['local-model'])
        factory = Mock(return_value=object())
        worker = Mock(side_effect=[ProviderError(503), ProviderError(503), {'source': CANDIDATE, 'digest': digest(CANDIDATE)}])
        result = propose(SOURCE, 'Store shares.', model_factory=factory, worker=worker)
        calls = factory.call_args_list
        self.assertEqual(calls[0].kwargs['profile']['apiKey'], 'synthetic-primary-key')
        self.assertEqual(calls[2].kwargs['profile']['baseUrl'], 'http://127.0.0.1:8000/v1')
        self.assertEqual(calls[2].kwargs['profile']['apiKey'], 'local-no-auth')
        self.assertEqual(result['provider'], 'local-vllm')

    def test_unconfigured_role_cannot_use_coding_models(self):
        # Sample: Configure coding only. Reject evaluator routing until a judge is named.
        with self.assertRaisesRegex(ValueError, 'No configured model route'):
            candidates('evaluation')
        configure_provider('openrouter', 'https://openrouter.ai/api/v1', ['synthetic-judge'], role='evaluation')
        self.assertEqual([r['model'] for r in candidates('evaluation')], ['synthetic-judge'])
        self.assertEqual([r['model'] for r in candidates('coding')], ['primary', 'secondary'])

    def test_disabled_provider_is_not_called(self):
        # Sample: Save local vLLM as disabled. Check that only two OpenRouter routes exist.
        configure_provider('local-vllm', 'http://127.0.0.1:8000/v1', ['local-model'], enabled=False)
        self.assertEqual(len(candidates()), 2)
        self.assertFalse(public_profiles()[1]['enabled'])

    def test_optional_provider_can_wait_for_models_and_key(self):
        # Sample: Save Mistral as disabled with no chosen model. Make no Mistral call.
        configure_provider('mistral', 'https://api.mistral.ai/v1', [], key_env='MISTRAL_API_KEY', enabled=False)
        profiles = public_profiles()
        self.assertEqual(profiles[1]['id'], 'mistral')
        self.assertEqual(profiles[1]['models'], [])
        self.assertFalse(profiles[1]['enabled'])
        self.assertEqual(len(candidates()), 2)

    def test_key_setup_preserves_disabled_provider_and_hides_key(self):
        # Sample: Enter a synthetic Mistral key. Keep models empty and Mistral disabled.
        configure_provider('mistral', 'https://api.mistral.ai/v1', [], key_env='MISTRAL_API_KEY', enabled=False)
        with patch('getpass.getpass', return_value='synthetic-mistral-secret'):
            status = configure_provider_key('mistral')
        profile = status['providers'][1]
        self.assertTrue(profile['keyConfigured'])
        self.assertFalse(profile['enabled'])
        self.assertEqual(profile['models'], [])
        self.assertNotIn('synthetic-mistral-secret', json.dumps(status))
        self.assertEqual(len(candidates()), 2)

    def test_model_selection_preserves_saved_provider_key(self):
        # Sample: Save Mistral's key first. Select its model later. Keep that key usable.
        configure_provider('mistral', 'https://api.mistral.ai/v1', [], key_env='MISTRAL_API_KEY', enabled=False)
        with patch('getpass.getpass', return_value='synthetic-mistral-secret'):
            configure_provider_key('mistral')
        configure_provider('mistral', 'https://api.mistral.ai/v1', ['mistral-medium-3.5'], key_env='MISTRAL_API_KEY')
        route = candidates()[-1]
        self.assertEqual(route['id'], 'mistral')
        self.assertEqual(route['apiKey'], 'synthetic-mistral-secret')

    def test_provider_catalog_uses_only_its_own_key(self):
        # Sample: Read Mistral's catalog with its key. Send no OpenRouter key or inference request.
        configure_provider('mistral', 'https://api.mistral.ai/v1', [], key_env='MISTRAL_API_KEY', enabled=False)
        with patch('getpass.getpass', return_value='synthetic-mistral-secret'):
            configure_provider_key('mistral')
        data = {'data': [{'id': 'mistral-medium-3.5', 'max_context_length': 262144, 'capabilities': {'function_calling': True}}]}
        with patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps(data).encode())) as request:
            result = catalog_status('mistral')
        sent = request.call_args.args[0]
        self.assertEqual(sent.full_url, 'https://api.mistral.ai/v1/models')
        self.assertEqual(sent.get_header('Authorization'), 'Bearer synthetic-mistral-secret')
        self.assertNotIn('synthetic-mistral-secret', json.dumps(result))
        self.assertTrue(result['checks'][0]['toolsAdvertised'])
        self.assertFalse(result['inferenceCallsMade'])
