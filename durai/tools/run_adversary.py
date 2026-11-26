"""Sample: Run python tools/run_adversary.py openshell. Make six model calls to DeepSeek on DeepInfra. Write receipts/sandbox-adversary-openshell.json. Exit 0 on PASS."""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
from durai import provider_config  # noqa: E402
from durai.sandbox_adversary import run_adversary  # noqa: E402
from run_escape_suite import make  # noqa: E402


def make_chat(provider_id='deepinfra-deepseek'):
    # Sample: Load the provider file. Return chat(system, user) that posts to chat/completions and returns the reply text. The key is never printed.
    config = provider_config.load(provider_id)
    model = config['models']['execution']

    def chat(system, user):
        body = json.dumps({'model': model, 'max_tokens': 1200, 'temperature': 0.7,
                           'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]}).encode()
        request = urllib.request.Request(config['baseUrl'].rstrip('/') + '/chat/completions', body,
                                         {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + config['apiKey']})
        for wait in (0, 30, 60, 120):
            time.sleep(wait)
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    return json.load(response)['choices'][0]['message']['content']
            except urllib.error.HTTPError as error:
                if error.code != 429:
                    raise
        raise RuntimeError('Provider kept answering 429')
    return chat


def main(argv=None):
    # Sample: Run the adversary for the named adapter. Print one line per goal. Write the receipt. Return 0 on PASS.
    name = (argv or sys.argv[1:] or [''])[0]
    receipt = run_adversary(make(name), name, make_chat())
    for item in receipt['attempts']:
        print(('PASS ' if item['passed'] else 'FAIL ') + item['goal'][:60], json.dumps(item.get('invariants', item.get('detail')))[:120])
    target = ROOT / 'receipts' / ('sandbox-adversary-' + name + '.json')
    target.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print(receipt['state'], target.name, 'calls', receipt['modelCalls'])
    return 0 if receipt['state'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
