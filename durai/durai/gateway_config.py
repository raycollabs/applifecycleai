"""Sample: Check design/gateway/switchyard-reflection-deepseek.toml. Every client names a key variable, no key text appears, and the host stays on 127.0.0.1. Problems come back as a list."""
import re
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

SECRET_KEYS = ('api_key', 'apikey', 'password', 'token', 'secret', 'authorization', 'bearer')
HOST_KEYS = ('host', 'bind', 'bind_address', 'listen', 'address')
LOCAL_HOSTS = ('127.0.0.1', 'localhost', '::1')
SECRET_SHAPE = re.compile(r'(sk-[A-Za-z0-9]{12,}|[A-Fa-f0-9]{40,}|[A-Za-z0-9_-]{32,})')
ENV_NAME = re.compile(r'[A-Z][A-Z0-9_]{0,99}')


def walk(node, path=''):
    # Sample: A table with llm_clients.a.base_url yields the path llm_clients.a.base_url and its value. Lists yield each item.
    if isinstance(node, dict):
        for key, value in node.items():
            yield from walk(value, path + '.' + key if path else key)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from walk(value, path + '[' + str(index) + ']')
    else:
        yield path, node


def check_text(text):
    # Sample: A file with api_key_env REFLECTION_API_KEY returns []. A file with api_key = "sk-abc..." returns a problem that names the path and never repeats the value.
    data = tomllib.loads(text)
    problems = []
    if data.get('schema_version') != 1:
        problems.append('schema_version must be 1')
    for name, client in (data.get('llm_clients') or {}).items():
        env = client.get('api_key_env')
        if env is not None and not ENV_NAME.fullmatch(str(env)):
            problems.append('llm_clients.' + name + '.api_key_env must be an upper case variable name')
        if env is None and not client.get('forward_auth'):
            problems.append('llm_clients.' + name + ' has no api_key_env')
        parsed = urlsplit(str(client.get('base_url', '')))
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            problems.append('llm_clients.' + name + '.base_url must not hold credentials or a query')
        if parsed.scheme != 'https' and parsed.hostname not in LOCAL_HOSTS:
            problems.append('llm_clients.' + name + '.base_url must use https')
    for path, value in walk(data):
        leaf = path.rsplit('.', 1)[-1].lower()
        if leaf != 'api_key_env' and any(part in leaf for part in SECRET_KEYS):
            problems.append(path + ' looks like an inline secret; use api_key_env')
        if leaf in HOST_KEYS and str(value) not in LOCAL_HOSTS:
            problems.append(path + ' must bind 127.0.0.1')
        if isinstance(value, str) and leaf not in ('id', 'base_url', 'api_key_env') and SECRET_SHAPE.fullmatch(value):
            problems.append(path + ' looks like a key')
    return problems


def check_file(path):
    # Sample: Read the file and return check_text of it. A missing file raises FileNotFoundError.
    return check_text(Path(path).read_text(encoding='utf-8'))
