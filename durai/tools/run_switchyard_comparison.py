"""Sample: Run python tools/run_switchyard_comparison.py PORT. Send five worker tasks twice to Reflection direct and twice through Switchyard. Cap 20 calls per arm. Write receipts/switchyard-comparison-2026-11-26.json."""
import ast
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from durai.common import digest  # noqa: E402
from durai.engineering import check_walkthroughs  # noqa: E402

CAP = 20
REPEATS = 2
PROVIDERS = Path('D:/livebuild/q1-fintech-checkout-flow/durai/private/providers')
TASKS = {
    'total': 'def total(prices):\n    return sum(prices)\n\n\ndef average(prices):\n    return total(prices) / len(prices)\n',
    'lots': 'def split_lots(shares, lot=100):\n    return shares // lot, shares % lot\n\n\ndef remove_lots(shares, count, lot=100):\n    if count * lot > shares:\n        raise ValueError("Not enough shares")\n    return shares - count * lot\n',
    'fee': 'def fee(amount_cents, rate_bp):\n    return amount_cents * rate_bp // 10000\n\n\ndef net(amount_cents, rate_bp):\n    return amount_cents - fee(amount_cents, rate_bp)\n',
    'dedupe': 'def dedupe(keys):\n    seen = set()\n    out = []\n    for key in keys:\n        if key not in seen:\n            seen.add(key)\n            out.append(key)\n    return out\n',
    'status': 'def next_state(state):\n    order = ["queued", "running", "succeeded"]\n    return order[min(order.index(state) + 1, len(order) - 1)]\n\n\ndef is_done(state):\n    return state in ("succeeded", "failed", "cancelled", "lost")\n',
}
PROMPT = ('Under each def line in program.py add one comment line in exactly this form: # Sample: Call total with [10, 20]. Check 30. '
          'Use concrete sample inputs and results for that function. Add no other comments or docstrings. Do not change the code. '
          'Answer with the full new program.py in one fenced python block and nothing else.\n\nprogram.py:\n')


def key(provider):
    # Sample: Read apiKey from reflection.json. Return the text. Never print it.
    return json.loads((PROVIDERS / (provider + '.json')).read_text(encoding='utf-8'))['apiKey']


def call(url, token, model, user):
    # Sample: Post one chat completion. Return reply text, usage, served model header, latency seconds, state and body model. A 429 waits and retries without counting.
    body = json.dumps({'model': model, 'max_tokens': 16000, 'temperature': 0.2, 'messages': [{'role': 'user', 'content': user}]}).encode()
    for wait in (0, 30, 60, 120, 240):
        time.sleep(wait)
        request = urllib.request.Request(url, body, {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
        start = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                data = json.load(response)
                return data['choices'][0]['message'].get('content') or '', data.get('usage') or {}, response.headers.get('x-model-router-selected-model'), time.monotonic() - start, 'OK', data.get('model')
        except urllib.error.HTTPError as error:
            if error.code != 429:
                return '', {}, error.headers.get('x-model-router-selected-model'), time.monotonic() - start, 'HTTP ' + str(error.code), None
    return '', {}, None, 0.0, 'HTTP 429 (kept)', None


def grade(seed, reply):
    # Sample: Take the fenced block. Check the code tree equals the seed tree and check_walkthroughs passes. Return pass flag and reason.
    start = reply.find('```')
    if start < 0:
        return False, 'no code block'
    end = reply.find('```', start + 3)
    text = reply[start:end] if end > 0 else reply[start:]
    text = text.split('\n', 1)[1] if '\n' in text else ''
    try:
        if ast.dump(ast.parse(text)) != ast.dump(ast.parse(seed)):
            return False, 'code changed'
        check_walkthroughs(text)
    except Exception as error:
        return False, type(error).__name__ + ': ' + str(error)[:120]
    return True, 'ok'


def main(port):
    # Sample: Run arm A then arm B. Stop an arm at 20 calls. Write the receipt and print one summary line per arm.
    arms = {'A-reflection-direct': ('https://api.reflection.ai/openai/v1/chat/completions', key('reflection'), 'Beam-1.0-501B-A23B'),
            'B-switchyard-primary': ('http://127.0.0.1:' + str(port) + '/v1/chat/completions', 'local-no-auth', 'durai-primary')}
    calls = []
    for arm, (url, token, model) in arms.items():
        used = 0
        for repeat in range(REPEATS):
            for name, seed in TASKS.items():
                if used >= CAP:
                    break
                reply, usage, header, seconds, state, body_model = call(url, token, model, PROMPT + seed)
                if state == 'OK':
                    used += 1
                passed, reason = grade(seed, reply) if state == 'OK' else (False, state)
                calls.append({'arm': arm, 'task': name, 'repeat': repeat, 'state': state, 'servedModel': header or body_model, 'inputTokens': usage.get('prompt_tokens'),
                              'outputTokens': usage.get('completion_tokens'), 'seconds': round(seconds, 2), 'passed': passed, 'reason': reason})
        print(arm, 'calls', used, flush=True)
    receipt = {'date': '2026-11-26', 'cap': CAP, 'repeats': REPEATS, 'tasks': list(TASKS), 'calls': calls}
    receipt['digest'] = digest(receipt)
    (ROOT / 'receipts/switchyard-comparison-2026-11-26.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main(int(sys.argv[1]))
