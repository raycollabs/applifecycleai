"""Sample: Resolve OpenRouter and local vLLM routes with separate credentials and role bindings. Validate model lists."""
import json
import re
from urllib.parse import urlsplit

ROLES = ('coding', 'evaluation', 'design', 'context')
MAX_MODELS = 5
MAX_PROVIDERS = 5
KINDS = ('openai-compatible', 'anthropic', 'google')
ANTHROPIC = 'https://api.anthropic.com'
ANTHROPIC_MODEL = 'claude-opus-5-5'


def validate_models(values, allow_empty=False):
    # Sample: Accept five distinct model IDs. Reject six IDs or a repeated primary.
    if not isinstance(values, list) or len(values) > MAX_MODELS or (not values and not allow_empty):
        raise ValueError('Each provider role requires one to five model IDs')
    if any(not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}', value) for value in values):
        raise ValueError('Invalid model ID')
    if len(set(values)) != len(values):
        raise ValueError('Model IDs must be distinct within a provider role')
    return list(values)


def endpoint(value):
    # Sample: Accept HTTPS OpenRouter or loopback vLLM. Reject URL credentials and queries.
    parsed = urlsplit(value)
    local = parsed.hostname in ('127.0.0.1', 'localhost', '::1')
    if (parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.hostname or
        parsed.scheme not in ('http', 'https') or (parsed.scheme == 'http' and not local)):
        raise ValueError('Model endpoint requires HTTPS or loopback HTTP without URL credentials')
    return value.rstrip('/'), local


def providers(config=None):
    # Sample: Resolve OpenRouter and local vLLM. Keep their keys bound to their endpoints.
    from .models import configuration, variable
    config = config or configuration()
    primary = {'id': config['provider'], 'baseUrl': config['baseUrl'], 'apiKey': config.get('apiKey'),
               'models': validate_models([config['model']] + config.get('fallbackModels', [])),
               'roleModels': config.get('roleModels', {}), 'enabled': True, 'priority': 50}
    profiles = [primary] + config.get('fallbackProviders', [])
    if len(profiles) > MAX_PROVIDERS:
        raise ValueError('At most five provider profiles are supported')
    resolved, seen = [], set()
    for profile in profiles:
        identity = profile.get('id', '')
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,49}', identity) or identity in seen:
            raise ValueError('Provider IDs must be valid and distinct')
        seen.add(identity)
        base_url, local = endpoint(profile['baseUrl'])
        kind = profile.get('kind', 'openai-compatible')
        if kind not in KINDS:
            raise ValueError('Unknown provider kind')
        if kind == 'anthropic' and base_url != ANTHROPIC:
            raise ValueError('Anthropic provider requires ' + ANTHROPIC)
        priority = profile.get('priority', 100)
        if not isinstance(priority, int) or isinstance(priority, bool) or not 0 <= priority <= 1000:
            raise ValueError('Provider priority must be an integer from 0 to 1000')
        model_ids = validate_models(profile.get('models', []), allow_empty=True)
        role_models = profile.get('roleModels', {})
        if not isinstance(role_models, dict) or any(role not in ROLES or role == 'coding' for role in role_models):
            raise ValueError('Use models for coding and explicit roleModels for other roles')
        role_models = {role: validate_models(values) for role, values in role_models.items()}
        if not model_ids and not role_models and profile.get('enabled', False) is True:
            raise ValueError('Provider requires an explicit model role')
        env_name = profile.get('apiKeyEnv')
        if env_name and not re.fullmatch(r'[A-Z][A-Z0-9_]{0,99}', env_name):
            raise ValueError('Invalid provider key environment name')
        key = (variable(env_name) if env_name else None) or profile.get('apiKey') or ('local-no-auth' if local else None)
        resolved.append({'id': identity, 'baseUrl': base_url, 'apiKey': key, 'models': model_ids, 'kind': kind,
                         'priority': priority, 'roleModels': role_models, 'enabled': profile.get('enabled', False) is True})
    return sorted(resolved, key=lambda item: item['priority'])


def candidates(role='coding', config=None):
    # Sample: Exhaust five OpenRouter models. Continue with the next enabled coding provider.
    if role not in ROLES:
        raise ValueError('Unknown model role')
    routes = []
    for profile in providers(config):
        if not profile['enabled'] or not profile['apiKey']:
            continue
        models = profile['models'] if role == 'coding' else profile['roleModels'].get(role, [])
        routes.extend(dict(profile, model=model, role=role) for model in models)
    if not routes:
        raise ValueError('No configured model route for role: ' + role)
    return routes


def public_profiles(config=None):
    # Sample: Show provider URLs and five model IDs. Return no credential bytes.
    return [{'id': p['id'], 'kind': p['kind'], 'priority': p['priority'], 'baseUrl': p['baseUrl'], 'models': p['models'],
             'roleModels': p['roleModels'], 'enabled': p['enabled'], 'keyConfigured': bool(p['apiKey'])} for p in providers(config)]


def configure_fallbacks(values):
    # Sample: Save the user's four fallback IDs after ERNIE. Preserve its existing key.
    from .models import configuration, readiness, ROOT
    config = configuration()
    validate_models([config['model']] + values)
    path = ROOT / 'private/model.json'
    saved = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    saved['fallbackModels'] = list(values)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(saved, indent=2) + '\n', encoding='utf-8')
    return readiness()


def configure_provider(identity, base_url, model_ids, role='coding', key_env=None, enabled=True):
    # Sample: Configure a local vLLM coding route. Keep unknown evaluator routes empty.
    import getpass
    from .models import configuration, readiness, ROOT, variable
    config = configuration()
    if role not in ROLES:
        raise ValueError('Unknown model role')
    validate_models(model_ids, allow_empty=not enabled)
    base_url, local = endpoint(base_url)
    path = ROOT / 'private/model.json'
    saved = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if identity == config['provider']:
        if base_url != config['baseUrl'].rstrip('/') or role == 'coding':
            raise ValueError('Use model-fallbacks for primary coding models; primary role setup must retain its endpoint')
        saved.setdefault('roleModels', {})[role] = list(model_ids)
    else:
        entries = saved.setdefault('fallbackProviders', [])
        existing = next((p for p in entries if p['id'] == identity), None)
        profile = dict(existing or {'id': identity, 'models': [], 'roleModels': {}})
        if existing and existing['baseUrl'].rstrip('/') != base_url:
            profile.pop('apiKey', None)
            profile.pop('apiKeyEnv', None)
        profile.update(baseUrl=base_url, enabled=enabled)
        if role == 'coding':
            if model_ids or not existing:
                profile['models'] = list(model_ids)
        elif model_ids:
            profile.setdefault('roleModels', {})[role] = list(model_ids)
        if key_env:
            profile['apiKeyEnv'] = key_env
        elif not local and enabled:
            key = getpass.getpass('API key for ' + identity + ' (hidden; private local settings only): ').strip()
            if not key:
                raise ValueError('Provider key was not supplied')
            profile['apiKey'] = key
        if existing:
            entries[entries.index(existing)] = profile
        else:
            entries.append(profile)
        # Validate the complete candidate config before replacing settings.
        proposed = dict(config, fallbackProviders=entries)
        providers(proposed)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(saved, indent=2) + '\n', encoding='utf-8')
    return readiness()


def catalog_status(provider_id=None):
    # Sample: Check Mistral's key with a model-list request. Make no inference call.
    import urllib.request
    import urllib.error
    from .models import configuration
    config = configuration()
    profile = next((p for p in providers(config) if p['id'] == (provider_id or config['provider'])), None)
    if profile is None:
        raise ValueError('Unknown model provider')
    if profile['kind'] == 'anthropic':
        return anthropic_catalog(profile)
    source = profile['baseUrl'] + '/models'
    headers = {}
    if profile['id'] != 'openrouter':
        if not profile['apiKey']:
            raise ValueError('Configure this provider key before catalog verification')
        if profile['apiKey'] != 'local-no-auth':
            headers['Authorization'] = 'Bearer ' + profile['apiKey']
    request = urllib.request.Request(source, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            catalog = {row['id']: row for row in json.load(response)['data']}
    except urllib.error.HTTPError as error:
        raise RuntimeError('Provider catalog access failed with HTTP ' + str(error.code) + '; key remains private') from None
    checks = []
    selected = profile['models'] or sorted(catalog)[:100]
    for model_id in selected:
        row = catalog.get(model_id)
        checks.append({'model': model_id, 'listed': row is not None,
                       'contextLength': row.get('context_length', row.get('max_context_length')) if row else None,
                       'toolsAdvertised': ('tools' in row.get('supported_parameters', []) or row.get('capabilities', {}).get('function_calling', False)) if row else False,
                       'pricing': row.get('pricing', {}) if row else {}, 'inferenceTested': False})
    return {'provider': profile['id'], 'source': source, 'checks': checks, 'availableModelCount': len(catalog),
            'networkCallsMade': True, 'inferenceCallsMade': False,
            'scope': 'Catalog metadata only; does not qualify inference or evaluation quality'}


def configure_provider_key(identity):
    # Sample: Save Mistral's hidden key before model selection. Keep the provider disabled.
    import getpass
    from .models import ROOT, configuration, readiness
    config = configuration()
    profiles = providers(config)
    if not any(profile['id'] == identity for profile in profiles):
        raise ValueError('Configure the provider profile before setting its key')
    path = ROOT / 'private/model.json'
    saved = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    key = getpass.getpass('API key for ' + identity + ' (hidden): ').strip()
    if not key:
        raise ValueError('Provider key was not supplied')
    if identity == config['provider']:
        saved['apiKey'] = key
    else:
        target = next(profile for profile in saved['fallbackProviders'] if profile['id'] == identity)
        target['apiKey'] = key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(saved, indent=2) + '\n', encoding='utf-8')
    return readiness()


def configure_anthropic(model_ids=None, key_env=None):
    # Sample: Save provider anthropic with claude-opus-5-5 at priority 0. Read its key from a hidden prompt.
    import getpass
    from .models import ROOT, configuration, readiness
    config = configuration()
    model_ids = validate_models(list(model_ids or [ANTHROPIC_MODEL]))
    path = ROOT / 'private/model.json'
    saved = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    entries = saved.setdefault('fallbackProviders', [])
    existing = next((p for p in entries if p['id'] == 'anthropic'), None)
    profile = {'id': 'anthropic', 'kind': 'anthropic', 'baseUrl': ANTHROPIC, 'models': model_ids,
               'roleModels': (existing or {}).get('roleModels', {}), 'priority': 0, 'enabled': True}
    if key_env:
        profile['apiKeyEnv'] = key_env
    else:
        key = getpass.getpass('Anthropic API key (hidden; stored only in ignored private/model.json): ').strip()
        if not key:
            raise ValueError('Provider key was not supplied')
        profile['apiKey'] = key
    if existing:
        entries[entries.index(existing)] = profile
    else:
        entries.append(profile)
    providers(dict(config, fallbackProviders=entries))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(saved, indent=2) + '\n', encoding='utf-8')
    return readiness()


def anthropic_catalog(profile):
    # Sample: List Anthropic models with key K-1. Check that claude-opus-5-5 is listed. Make no inference call.
    import anthropic
    if not profile['apiKey']:
        raise ValueError('Configure the Anthropic key before catalog verification')
    client = anthropic.Anthropic(api_key=profile['apiKey'], max_retries=2, timeout=30)
    try:
        catalog = {row.id: row for row in client.models.list()}
    except anthropic.AuthenticationError:
        raise RuntimeError('Anthropic rejected the key; key remains private') from None
    except anthropic.APIStatusError as error:
        raise RuntimeError('Anthropic catalog access failed with HTTP ' + str(error.status_code) + '; key remains private') from None
    checks = [{'model': model_id, 'listed': model_id in catalog,
               'contextLength': getattr(catalog.get(model_id), 'max_input_tokens', None),
               'maxOutputTokens': getattr(catalog.get(model_id), 'max_tokens', None), 'inferenceTested': False}
              for model_id in profile['models']]
    return {'provider': profile['id'], 'source': 'anthropic.models.list', 'checks': checks,
            'availableModelCount': len(catalog), 'networkCallsMade': True, 'inferenceCallsMade': False,
            'scope': 'Catalog metadata only; does not qualify inference or evaluation quality'}
