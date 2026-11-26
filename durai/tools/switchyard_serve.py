"""Sample: Run venv python tools/switchyard_serve.py design/gateway/switchyard-reflection-deepseek.toml. Read the two keys from the provider files into this process only. Print the loopback port. Wait until stdin closes."""
import json
import os
import sys
from pathlib import Path

PROVIDERS = Path(os.environ.get('DURAI_PROVIDER_DIR', 'D:/livebuild/q1-fintech-checkout-flow/durai/private/providers'))


def main():
    # Sample: Set REFLECTION_API_KEY and DEEPSEEK_API_KEY from reflection.json and deepseek.json. Start Server on a free port. Print PORT n. Close when stdin ends.
    for env_name, provider in (('REFLECTION_API_KEY', 'reflection'), ('DEEPSEEK_API_KEY', 'deepseek')):
        os.environ[env_name] = json.loads((PROVIDERS / (provider + '.json')).read_text(encoding='utf-8'))['apiKey']
    from switchyard_rust.server import Server
    server = Server(sys.argv[1], 0)
    print('PORT ' + str(server.port), flush=True)
    sys.stdin.read()
    server.close()


if __name__ == '__main__':
    main()
