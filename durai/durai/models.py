"""Model configuration. Public readiness contains no credentials or paid calls."""
import getpass
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = 'baidu/ernie-5.5-flash-preview:free'
OPENROUTER = 'https://openrouter.ai/api/v1'


def variable(name):
    # Sample: Read the named local model setting. Return no value when it is absent.
    value = os.environ.get(name)
    if value:
        return value
    if os.name == 'nt':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
                return winreg.QueryValueEx(key, name)[0]
        except OSError:
            pass
    return None


def configuration():
    # Sample: Load primary and optional provider settings. Keep every key private.
    path = ROOT / 'private/model.json'
    saved = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    endpoint = variable('DURAI_MODEL_BASE_URL') or saved.get('baseUrl', OPENROUTER)
    model = variable('DURAI_MODEL') or saved.get('model', DEFAULT_MODEL)
    parsed = urlsplit(endpoint)
    local = parsed.hostname in ('localhost', '127.0.0.1', '::1')
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Model endpoint must not contain credentials, queries, or fragments')
    if not parsed.hostname or parsed.scheme not in ('https', 'http') or (parsed.scheme == 'http' and not local):
        raise ValueError('Remote model endpoints require HTTPS')
    is_openrouter = endpoint.rstrip('/') == OPENROUTER
    key = variable('DURAI_MODEL_API_KEY') or saved.get('apiKey')
    if not key and is_openrouter:
        key = variable('OPENROUTER_API_KEY')
    if not key and local:
        key = 'local-no-auth'
    fallback_env = variable('DURAI_FALLBACK_MODELS')
    fallbacks = json.loads(fallback_env) if fallback_env else saved.get('fallbackModels', [])
    if not isinstance(fallbacks, list):
        raise ValueError('Fallback models must be a list')
    from .model_routes import validate_models
    validate_models([model] + fallbacks)
    return {'baseUrl': endpoint, 'model': model, 'apiKey': key, 'fallbackModels': fallbacks,
            'fallbackProviders': saved.get('fallbackProviders', []), 'roleModels': saved.get('roleModels', {}),
            'provider': 'openrouter' if is_openrouter else 'compatible-gateway'}


def readiness():
    # Sample: Show ERNIE and four fallback IDs. Exclude API keys and make no network call.
    config = configuration()
    from .model_routes import public_profiles
    profiles = public_profiles(config)
    ready = any(p['enabled'] and p['keyConfigured'] and p['models'] for p in profiles)
    return {'state': 'READY' if ready else 'NEEDS_CONFIGURATION',
            'provider': config['provider'], 'baseUrl': config['baseUrl'], 'model': config['model'],
            'keyConfigured': bool(config['apiKey']), 'networkCallsMade': False,
            'fallbackModels': config['fallbackModels'], 'providers': profiles,
            'limits': {'modelsPerProviderRole': 5, 'providers': 5}}


def require_configuration():
    # Sample: Permit a configured coding provider. Refuse when no eligible credential exists.
    config = configuration()
    from .model_routes import providers
    if not any(p['enabled'] and p['apiKey'] and p['models'] for p in providers(config)):
        raise ValueError('Configure the model and key with model-setup or OPENROUTER_API_KEY before repair')
    return config


def setup():
    # Sample: Replace the primary key with hidden input. Preserve configured fallback routes.
    key = getpass.getpass('OpenRouter API key (hidden; stored only in ignored private/model.json): ').strip()
    if not key:
        raise ValueError('No key was entered')
    path = ROOT / 'private/model.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    saved = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    saved.update(baseUrl=OPENROUTER, model=DEFAULT_MODEL, apiKey=key)
    path.write_text(json.dumps(saved, indent=2) + '\n', encoding='utf-8')
    return readiness()
