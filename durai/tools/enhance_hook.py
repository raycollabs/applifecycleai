"""Sample: Claude Code sends {"prompt": "Compare Durai with github.com/acme/agentbox"} on stdin. Print JSON with additionalContext. Print nothing for other prompts. A build request for an app, screen or dashboard adds UI themes. Exit 0 always."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


MACHINE_MARKERS = ('[system notification', '<task-notification', '<system-reminder', '<pasted_content')
MAX_REQUEST_CHARS = 2000


def is_machine_text(request):
    # Sample: Text that starts with "[SYSTEM NOTIFICATION" returns True. "Compare Durai with LangGraph" returns False. A text of 2001 characters returns True.
    head = request.lstrip().lower()
    return len(request) > MAX_REQUEST_CHARS or any(head.startswith(marker) or marker in head[:400] for marker in MACHINE_MARKERS)


def run(raw):
    # Sample: Raw text of a compare request or of an app build request returns a hook JSON string. A code question, broken JSON or any error returns an empty string.
    try:
        from durai.compare_intent import enhance as compare_enhance
        from durai.theme_intent import enhance as theme_enhance
        request = json.loads(raw).get('prompt', '')
        if is_machine_text(request):
            return ''
        result = compare_enhance(request)
        label = 'Durai compare format applied'
        if result is None:
            result = theme_enhance(request)
            label = 'Durai UI themes applied'
        if result is None:
            return ''
        added = result['text'][len(request):].strip() if result['text'].startswith(request) else result['text']
        return json.dumps({'systemMessage': label,
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
