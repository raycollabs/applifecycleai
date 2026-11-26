"""Sample: Run python tools/set_reflection_key.py. Type the key at the hidden prompt. The file reflection.json appears in the provider folder. The key is never printed."""
import getpass
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from durai import provider_config  # noqa: E402

BASE_URL = 'https://api.reflection.ai/openai/v1'
MODEL = 'Beam-1.0-501B-A23B'


def build_config(key):
    # Sample: Key K-1 gives id reflection, four roles on Beam-1.0-501B-A23B, maxTokens 16000 and streaming true. An empty key raises ValueError.
    key = (key or '').strip()
    if not key:
        raise ValueError('The key is empty')
    return {'id': 'reflection', 'kind': 'openai-compatible', 'baseUrl': BASE_URL,
            'models': {role: MODEL for role in ('execution', 'advisor', 'planning', 'verification')},
            'defaults': {'coding': {'maxTokens': 16000, 'timeoutSeconds': 600, 'streaming': True}}, 'apiKey': key}


def write_provider(key, folder=None):
    # Sample: Folder C:/tmp/p. Write C:/tmp/p/reflection.json. Return the path. The loader reads it back with kind openai-compatible.
    folder = Path(folder) if folder else provider_config.provider_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'reflection.json'
    path.write_text(json.dumps(build_config(key), indent=2) + '\n', encoding='utf-8')
    return path


def main():
    # Sample: Ask for the key with getpass. Write the file. Print only the file path.
    path = write_provider(getpass.getpass('Reflection API key (hidden): '))
    print('Wrote', path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
