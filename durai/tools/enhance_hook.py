"""Sample: Claude Code sends {"prompt": "Compare Durai with github.com/acme/agentbox"} on stdin. Print JSON with additionalContext. Print nothing for other prompts. Exit 0 always."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run(raw):
    # Sample: Raw text of a compare request returns a hook JSON string. A build request, broken JSON or any error returns an empty string.
    try:
        from durai.compare_intent import enhance
        request = json.loads(raw).get('prompt', '')
        result = enhance(request)
        if result is None:
            return ''
        added = result['text'][len(request):].strip() if result['text'].startswith(request) else result['text']
        return json.dumps({'systemMessage': 'Durai compare format applied',
                           'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', 'additionalContext': added}})
    except Exception:
        return ''


def main():
    # Sample: Read stdin. Print the result of run. Exit 0 even when run fails.
    try:
        output = run(sys.stdin.buffer.read().decode('utf-8-sig'))
        if output:
            print(output)
    except Exception:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
