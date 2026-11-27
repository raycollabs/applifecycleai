"""# Sample: Put zai.json in the provider folder. Call chat_model('zai', 'planning'). Check a ChatOpenAI with the planning model."""
import json
import os
from pathlib import Path

DEFAULT_DIR = Path(r'D:/livebuild/q1-fintech-checkout-flow/durai/private/providers')
REQUIRED_KEYS = {'id', 'kind', 'baseUrl', 'models', 'defaults', 'apiKey'}
KINDS = ('openai-compatible', 'anthropic', 'google')
GEMINI_HOST = 'generativelanguage.googleapis.com'
DEEPSEEK_HOST = 'api.deepseek.com'
CODING_DEFAULTS = {'maxTokens': 32768, 'timeoutSeconds': 600, 'streaming': True}


def provider_dir():
    # Sample: Set DURAI_PROVIDER_DIR to C:/tmp/p. Call provider_dir. Check Path C:/tmp/p.
    value = os.environ.get('DURAI_PROVIDER_DIR')
    return Path(value) if value else DEFAULT_DIR


def load(provider_id):
    # Sample: Folder has openai.json with id openai. Load openai. Check config kind openai-compatible.
    path = provider_dir() / f'{provider_id}.json'
    if not path.is_file():
        raise KeyError(f'Provider {provider_id} not found. Expected file {path}')
    try:
        config = json.loads(path.read_text(encoding='utf-8'))
    except ValueError:
        raise ValueError(f'Provider file {path} is not valid JSON') from None
    if not isinstance(config, dict):
        raise ValueError(f'Provider file {path} must hold a JSON object')
    missing = sorted(REQUIRED_KEYS - set(config))
    if missing:
        raise ValueError(f'Provider file {path} lacks keys {missing}')
    if config['id'] != provider_id:
        raise ValueError(f'Provider file {path} has id {config["id"]} but file name says {provider_id}')
    if config['kind'] not in KINDS:
        raise ValueError(f'Provider file {path} has unknown kind {config["kind"]}; use one of {list(KINDS)}')
    return config


def redacted(config):
    # Sample: Config has apiKey K-1. Call redacted. Check the copy has no apiKey and no K-1.
    return {name: value for name, value in config.items() if name != 'apiKey'}


def is_gemini(provider_id=None, kind=None, base_url=None):
    # Sample: Route id gemini, kind google, or host generativelanguage.googleapis.com. Check True. Route id zai. Check False.
    from urllib.parse import urlsplit
    host = urlsplit(base_url or '').hostname
    return provider_id == 'gemini' or kind == 'google' or host == GEMINI_HOST


def is_deepseek(provider_id=None, base_url=None):
    # Sample: Route id deepseek, or host api.deepseek.com. Check True. Route id zai on api.z.ai. Check False.
    from urllib.parse import urlsplit
    return provider_id == 'deepseek' or urlsplit(base_url or '').hostname == DEEPSEEK_HOST


def coding_limits(provider_id=None, base_url=None):
    # Sample: Provider deepseek has no coding record. Check 32768 tokens, 600 s, streaming true, thinking true. Record maxTokens 16000. Check 16000.
    limits = dict(CODING_DEFAULTS)
    limits['thinking'] = is_deepseek(provider_id, base_url)
    try:
        record = (load(provider_id).get('defaults') or {}).get('coding') or {}
    except KeyError:
        record = {}
    if not isinstance(record, dict):
        raise ValueError(f'Provider {provider_id} coding record must be an object')
    limits.update({name: value for name, value in record.items()
                   if name in ('maxTokens', 'timeoutSeconds', 'streaming', 'thinking', 'reasoningEffort')})
    return limits


def gemini_model(model, api_key, **options):
    # Sample: Build gemini-3.8-flash with max_tokens 4096. Check ChatGoogleGenerativeAI, max_output_tokens 4096, retries 0.
    from langchain_google_genai import ChatGoogleGenerativeAI
    options = dict(options)
    if 'max_tokens' in options:
        options['max_output_tokens'] = options.pop('max_tokens')
    for name in ('base_url', 'api_key', 'reasoning_effort', 'extra_body', 'streaming', 'stream_usage', 'top_p'):
        options.pop(name, None)
    options.setdefault('max_retries', 0)
    return ChatGoogleGenerativeAI(model=model, google_api_key=api_key, **options)


def refuse_openai_gemini(model, provider_id=None, kind=None, base_url=None):
    # Sample: Gemini route built on ChatOpenAI. Check ValueError. Gemini route on ChatGoogleGenerativeAI. Check same model returned.
    if is_gemini(provider_id, kind, base_url) and 'ChatOpenAI' in type(model).__name__:
        raise ValueError('Gemini routes require the native Google client; ChatOpenAI drops the thought signature')
    return model


def _build_reasoning_chat_openai():
    # Sample: Build ChatOpenAI subclass that preserves reasoning_content across streaming chunks and request payloads.
    from langchain_openai import ChatOpenAI

    class ReasoningChatOpenAI(ChatOpenAI):
        def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
            gen_chunk = super()._convert_chunk_to_generation_chunk(chunk, default_chunk_class, base_generation_info)
            if gen_chunk is not None:
                choices = chunk.get('choices', []) or chunk.get('chunk', {}).get('choices', [])
                if choices:
                    delta = choices[0].get('delta') or {}
                    rc = delta.get('reasoning_content')
                    if rc is not None:
                        gen_chunk.message.additional_kwargs['reasoning_content'] = rc
            return gen_chunk

        def _create_chat_result(self, response, generation_info=None):
            result = super()._create_chat_result(response, generation_info)
            choices = response.get('choices') or [] if isinstance(response, dict) else getattr(response, 'choices', None) or []
            for idx, gen in enumerate(result.generations):
                if idx < len(choices):
                    msg = choices[idx].get('message', {}) if isinstance(choices[idx], dict) else getattr(choices[idx], 'message', None)
                    rc = msg.get('reasoning_content') if isinstance(msg, dict) else getattr(msg, 'reasoning_content', None)
                    if rc is not None:
                        gen.message.additional_kwargs['reasoning_content'] = rc
            return result

        def _get_request_payload(self, input_, *, stop=None, **kwargs):
            payload = super()._get_request_payload(input_, stop=stop, **kwargs)
            lc_msgs = input_.to_messages() if hasattr(input_, 'to_messages') else list(input_)
            ai_lc = [m for m in lc_msgs if getattr(m, 'type', None) == 'ai']
            ai_pay = [m for m in payload.get('messages', []) if m.get('role') == 'assistant']
            need_default = 'reflection' in str(getattr(self, 'openai_api_base', '') or '').lower()
            for lc_msg, pay_msg in zip(ai_lc, ai_pay):
                rc = (getattr(lc_msg, 'additional_kwargs', None) or {}).get('reasoning_content')
                if rc is not None:
                    pay_msg['reasoning_content'] = rc
                elif need_default:
                    pay_msg['reasoning_content'] = ''
            return payload

    return ReasoningChatOpenAI


def is_reflection(provider_id=None, base_url=None):
    # Sample: Route id reflection or host api.reflection.ai. Check True.
    from urllib.parse import urlsplit
    return provider_id == 'reflection' or urlsplit(base_url or '').hostname == 'api.reflection.ai'


def openai_chat_class(provider_id=None, base_url=None):
    # Sample: Return ReasoningChatOpenAI for reflection and deepseek routes, and standard ChatOpenAI otherwise.
    if is_reflection(provider_id, base_url) or is_deepseek(provider_id, base_url):
        return _build_reasoning_chat_openai()
    from langchain_openai import ChatOpenAI
    return ChatOpenAI


def chat_model(provider_id, role, **overrides):
    # Sample: Provider zai has planning glm-5.3. Call chat_model('zai', 'planning'). Check model_name glm-5.3.
    config = load(provider_id)
    if config['kind'] == 'anthropic':
        raise ValueError(f'Provider {provider_id} has kind anthropic; anthropic is not supported yet')
    models = config['models']
    if role not in models:
        raise ValueError(f'Provider {provider_id} has no role {role}; known roles {sorted(models)}')
    defaults = config['defaults'] or {}
    if is_gemini(provider_id, config['kind'], config['baseUrl']):
        options = {}
        if defaults.get('maxTokens'):
            options['max_tokens'] = defaults['maxTokens']
        options.update(overrides)
        return refuse_openai_gemini(gemini_model(models[role], config['apiKey'], **options),
                                    provider_id, config['kind'], config['baseUrl'])
    args = {'model': models[role], 'base_url': config['baseUrl'], 'api_key': config['apiKey'], 'max_retries': 0}
    if defaults.get('reasoningEffort'):
        args['reasoning_effort'] = defaults['reasoningEffort']
    if defaults.get('thinking'):
        args['extra_body'] = {'thinking': {'type': 'enabled'}}
    if defaults.get('maxTokens'):
        args['max_tokens'] = defaults['maxTokens']
    args.update(overrides)
    cls = openai_chat_class(provider_id, config['baseUrl'])
    return cls(**args)
