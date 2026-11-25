"""Sample: Save a.py edit messages for attempt A-1. Remove its workspace. Read the same redacted trace by digest."""
import os
from pathlib import Path
import re
from .common import Conflict, canonical, digest, uid
from .execution_events import known_secrets, redact


def configured_secrets():
    # Sample: Load passwords P-1 and P-2 and provider keys K-1 and K-2. Return the four values.
    from .model_routes import providers
    secrets = known_secrets()
    secrets.update(profile['apiKey'] for profile in providers() if profile.get('apiKey'))
    return secrets


def completion_summary(message):
    # Sample: Read max_tokens with 16000 output tokens and 16000 reasoning tokens. Return those counts and no provider body.
    if getattr(message, 'type', None) != 'ai':
        return {}
    metadata = message.response_metadata
    reason = metadata.get('stop_reason', metadata.get('finish_reason'))
    allowed = {'end_turn', 'max_tokens', 'tool_use', 'stop_sequence', 'pause_turn', 'refusal',
               'stop', 'length', 'content_filter', 'tool_calls', 'function_call'}
    result = {'stopReason': reason} if reason in allowed else {}
    usage = message.usage_metadata or {}
    for name, target in [('input_tokens', 'inputCount'), ('output_tokens', 'outputCount')]:
        value = usage.get(name)
        if type(value) is int and value >= 0:
            result[target] = value
    value = (usage.get('output_token_details') or {}).get('reasoning')
    if type(value) is int and value >= 0:
        result['reasoningCount'] = value
    if any(name in result for name in ('inputCount', 'outputCount', 'reasoningCount')):
        result['unit'] = 'tokens'
    return result


def retain_worker_trace(task, attempt_id, messages, changed, root=None):
    # Sample: Retain A-1 with one edit call and its result. Redact key K-1. Return its immutable trace path and digest.
    entries = []
    for message in messages:
        content = message.content
        if isinstance(content, list):
            content = [block if isinstance(block, str) else {'type': 'text', 'text': block['text']}
                       for block in content if isinstance(block, str) or
                       (isinstance(block, dict) and block.get('type') == 'text')]
        entry = {'role': message.type, 'content': content}
        summary = completion_summary(message)
        if summary:
            entry['completion'] = summary
        for name in ('tool_calls', 'tool_call_id', 'name', 'status'):
            value = getattr(message, name, None)
            if value:
                entry[name] = value
        entries.append(entry)
    body = redact({'format': 'durai-worker-trace/v1', 'attemptId': attempt_id,
                   'taskDigest': task['digest'], 'changedPaths': sorted(changed), 'messages': entries}, configured_secrets())
    checksum = digest(body)
    record = dict(body, digest=checksum)
    folder = Path(root) if root is not None else Path(__file__).resolve().parents[1] / 'private/worker-evidence'
    folder.mkdir(parents=True, exist_ok=True)
    prefix = re.sub(r'[^A-Za-z0-9_.-]', '_', attempt_id or ('unbound-' + uid()))
    path = folder / (prefix + '-' + checksum + '.json')
    content = canonical(record)
    if path.exists():
        if path.read_bytes() != content:
            raise Conflict('Saved worker evidence differs')
    else:
        with path.open('xb') as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
    return {'path': str(path), 'digest': checksum, 'messageCount': len(entries), 'changedPaths': list(changed)}
