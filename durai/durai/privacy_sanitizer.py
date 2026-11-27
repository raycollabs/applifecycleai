"""Sample: Strip source code snippets, local file paths, user overlays, and secrets from a public ticket payload."""
import re
from typing import Any, Iterable, Mapping, Optional

SECRET_KEY_RE = re.compile(
    r'^(?:.*[_-])?(?:api[_-]?key|secret|password|passwd|authorization|credential|credentials|'
    r'private[_-]?key|access[_-]?key|review[_-]?key|signing[_-]?key|session[_-]?secret|'
    r'client[_-]?secret|auth[_-]?token|access[_-]?token|refresh[_-]?token|session[_-]?token|api[_-]?token|token)$',
    re.IGNORECASE,
)

OVERLAY_KEYS = frozenset({
    'user_overlay', 'user_overlays', 'useroverlay', 'useroverlays',
    'overlay', 'overlays', 'custom_rules', 'local_overrides',
    'prompt_overlay', 'user_prompt', 'user_prompts', 'custom_prompts',
    'user_config', 'client_overlay',
})

CODE_KEYS = frozenset({
    'source', 'source_code', 'code', 'snippet', 'file_content', 'file_contents',
    'content', 'body', 'patch', 'diff', 'raw_code', 'messages',
    'requesttext', 'request_text', 'prompt', 'stdout', 'stderr',
    'traceback', 'stacktrace',
})

PATH_KEYS = frozenset({
    'workspace', 'workspace_path', 'tracepath', 'trace_path',
    'file', 'file_path', 'path', 'paths', 'changedpaths', 'changed_paths', 'root',
})

PUBLIC_TICKET_FIELDS = (
    'ticket_id', 'fingerprint', 'source_mode', 'client_instance_hash',
    'pack_id', 'pack_version', 'shell_version', 'category', 'severity',
    'summary', 'sanitized_payload', 'occurrence_count', 'status',
)

PUBLIC_PAYLOAD_FIELDS = (
    'fingerprint', 'pack_id', 'pack_version', 'shell_version', 'shell_profile',
    'component_category', 'category', 'severity', 'rule_id', 'stop_reason',
    'signal_kinds', 'rule_ids', 'smell_kinds', 'tags', 'metrics',
    'effective_view_digest', 'calls_used', 'passed_checks', 'failed_checks',
    'tool_error_count', 'stall_nudges', 'list_cost_usd',
)

PEM_BLOCK_RE = re.compile(r'-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----')
PEM_HEADER_RE = re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----')
FENCED_CODE_RE = re.compile(r'```[\s\S]*?```')

SECRET_VALUE_PATTERNS = (
    re.compile(r'AIza[0-9A-Za-z_\-]{35}'),
    re.compile(r'\bsk-(?:proj-|live-|test-)?[A-Za-z0-9_\-]{16,}'),
    re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})'),
    re.compile(r'\bAKIA[0-9A-Z]{16}\b'),
    re.compile(r'\bxox[baprs]-[A-Za-z0-9-]{10,}'),
    re.compile(r'\bBearer\s+[A-Za-z0-9._\-+/=]{12,}'),
    re.compile(r'\b(?:postgres(?:ql)?|mysql|redis|amqp|mongodb|https?)://[^:\s/@]+:[^@\s/]+@[^\s\'"`<>]+'),
    re.compile(r'''(?i)\b(?:api[_-]?key|secret|password|passwd|private[_-]?key|access[_-]?key|token)\b\s*[:=]\s*['"]?[A-Za-z0-9_\-./+=]{8,}['"]?'''),
)

WINDOWS_PATH_RE = re.compile(
    r'(?:[A-Za-z]:[\\/][^\s\'"`<>|,;)}\]]+(?:\s+[^\s\'"`<>|,;)}\]]*[\\/][^\s\'"`<>|,;)}\]]+)*'
    r'|\\\\[A-Za-z0-9_.-]+\\[^\s\'"`<>|,;)}\]]+)'
)

UNIX_PATH_RE = re.compile(
    r'(?<![:\w/])(?:/(?:home|Users|users|tmp|var|etc|opt|root|mnt|srv|usr|private|workspace|workspaces|app|builds|repos|projects)/[^\s\'"`<>|,;)}\]]+'
    r'|~(?:/[^\s\'"`<>|,;)}\]]+)+)'
)

EMAIL_RE = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')

CODE_LINE_RE = re.compile(
    r'^\s*(?:'
    r'(?:async\s+)?def\s+\w+\s*\('
    r'|class\s+\w+\s*[\(:]'
    r'|from\s+\w+(?:\.\w+)*\s+import\s+'
    r'|import\s+(?:\w+(?:\s*,\s*\w+)*|\{[^}]+\}\s+from\s+[\'"])'
    r'|export\s+(?:default\s+)?(?:async\s+)?(?:function|const|class|type|interface)\b'
    r'|(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?(?:\([^)]*\)\s*=>|[\'"{\[\d]|new\s+|await\s+)'
    r'|function\s+\w*\s*\([^)]*\)\s*\{'
    r'|try\s*:\s*$'
    r'|except\s+[^:]*:\s*$'
    r')',
    re.MULTILINE,
)

SQL_SNIPPET_RE = re.compile(
    r'\b(?:SELECT|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|CREATE\s+TABLE)\b[\s\S]*?\b(?:FROM|VALUES|WHERE|PRIMARY\s+KEY)\b',
    re.IGNORECASE,
)

HTML_SNIPPET_RE = re.compile(r'<\s*(?:div|span|form|button|html|script|input)\b[^>]*>', re.IGNORECASE)


def _is_redacted(value: Any) -> bool:
    # Sample: Return True when value is already replaced with a redaction marker.
    return isinstance(value, str) and value.strip() in ('[REDACTED]', '[CODE_STRIPPED]', '[PATH]', '[EMAIL]')


def contains_raw_code_or_secret(payload: Any) -> bool:
    # Sample: Check payload dict or string for raw code blocks, syntax snippets, or unredacted secrets.
    if payload is None:
        return False
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            key_lower = str(key).lower()
            if key_lower in OVERLAY_KEYS:
                return True
            if key_lower in CODE_KEYS and value not in (None, '', [], {}) and not _is_redacted(value):
                return True
            if SECRET_KEY_RE.match(key_lower) and value not in (None, '') and not _is_redacted(value):
                return True
            if contains_raw_code_or_secret(value):
                return True
        return False
    if isinstance(payload, (list, tuple, set)):
        return any(contains_raw_code_or_secret(item) for item in payload)
    if isinstance(payload, str):
        if FENCED_CODE_RE.search(payload) or PEM_BLOCK_RE.search(payload) or PEM_HEADER_RE.search(payload):
            return True
        for pattern in SECRET_VALUE_PATTERNS:
            if pattern.search(payload):
                return True
        if CODE_LINE_RE.search(payload) or SQL_SNIPPET_RE.search(payload) or HTML_SNIPPET_RE.search(payload):
            return True
        if '=>' in payload and ('{' in payload or ';' in payload):
            return True
        return False
    return False


class PrivacySanitizer:
    def __init__(self, known_secrets: Optional[Iterable[str]] = None):
        # Sample: Initialize sanitizer with optional extra secret strings to scrub alongside built-in patterns.
        self._secrets = tuple(sorted({s for s in (known_secrets or ()) if isinstance(s, str) and len(s) >= 6}, key=len, reverse=True))

    def sanitize_text(self, text: str) -> str:
        # Sample: Scrub secrets, fenced code, inline code lines, local Windows/Unix paths, and email addresses from text.
        if not isinstance(text, str):
            text = str(text)
        cleaned = PEM_BLOCK_RE.sub('[REDACTED]', text)
        cleaned = PEM_HEADER_RE.sub('[REDACTED]', cleaned)
        cleaned = FENCED_CODE_RE.sub('[CODE_STRIPPED]', cleaned)
        for secret in self._secrets:
            cleaned = cleaned.replace(secret, '[REDACTED]')
        for pattern in SECRET_VALUE_PATTERNS:
            cleaned = pattern.sub('[REDACTED]', cleaned)
        cleaned = WINDOWS_PATH_RE.sub('[PATH]', cleaned)
        cleaned = UNIX_PATH_RE.sub('[PATH]', cleaned)
        cleaned = EMAIL_RE.sub('[EMAIL]', cleaned)
        cleaned = SQL_SNIPPET_RE.sub('[CODE_STRIPPED]', cleaned)
        cleaned = HTML_SNIPPET_RE.sub('[CODE_STRIPPED]', cleaned)
        if CODE_LINE_RE.search(cleaned):
            kept_lines = []
            for line in cleaned.splitlines():
                if CODE_LINE_RE.match(line):
                    kept_lines.append('[CODE_STRIPPED]')
                else:
                    kept_lines.append(line)
            cleaned = '\n'.join(kept_lines)
        if '=>' in cleaned and ('{' in cleaned or ';' in cleaned):
            cleaned = '[CODE_STRIPPED]'
        return cleaned

    def sanitize_payload(self, payload: Any) -> Any:
        # Sample: Recursively strip user overlays, raw code keys, local path keys, and secrets from dict or list payloads.
        if isinstance(payload, Mapping):
            cleaned = {}
            for key, value in payload.items():
                key_str = str(key)
                key_lower = key_str.lower()
                if key_lower in OVERLAY_KEYS or key_lower in CODE_KEYS or key_lower in PATH_KEYS:
                    continue
                if SECRET_KEY_RE.match(key_lower):
                    cleaned[key_str] = '[REDACTED]'
                    continue
                cleaned[key_str] = self.sanitize_payload(value)
            return cleaned
        if isinstance(payload, list):
            return [self.sanitize_payload(item) for item in payload]
        if isinstance(payload, tuple):
            return [self.sanitize_payload(item) for item in payload]
        if isinstance(payload, str):
            return self.sanitize_text(payload)
        return payload

    def sanitize(self, payload: Any) -> Any:
        # Sample: Alias for sanitize_payload on arbitrary text, list, or mapping input.
        return self.sanitize_payload(payload)

    def sanitize_ticket(self, ticket: Mapping[str, Any]) -> dict:
        # Sample: Produce schema-only public ticket stripping workspace code, paths, secrets, and user overlays.
        raw_inner = ticket.get('sanitized_payload')
        inner = raw_inner if isinstance(raw_inner, Mapping) else {}
        allowed_inner = {}
        for field in PUBLIC_PAYLOAD_FIELDS:
            if field in inner:
                allowed_inner[field] = self.sanitize_payload(inner[field])
        result = {}
        for field in PUBLIC_TICKET_FIELDS:
            if field == 'sanitized_payload':
                result['sanitized_payload'] = allowed_inner
            elif field in ticket:
                result[field] = self.sanitize_payload(ticket[field])
        result.setdefault('sanitized_payload', allowed_inner)
        return result
