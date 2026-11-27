"""Sample: Create intake app with secret K-1 and max_bytes 16384. Post signed ticket T-1 twice. Check count 2 and PUBLIC_SHELL."""
import hashlib
import hmac
import json
import os
import re
from typing import Any, Mapping, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

ALLOWED_CATEGORIES = frozenset({
    'TOOL_FRICTION',
    'WEB_RULE_GAP',
    'SQL_BLUEPRINT_GAP',
    'UI_UX_DEFECT',
    'PROMPT_AMBIGUITY',
    'ROUTING',
})
ALLOWED_SEVERITIES = frozenset({'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'})
ALLOWED_FIELDS = frozenset({
    'ticket_id',
    'fingerprint',
    'source_mode',
    'client_instance_hash',
    'pack_id',
    'pack_version',
    'shell_version',
    'category',
    'severity',
    'summary',
    'sanitized_payload',
})
REQUIRED_FIELDS = ('fingerprint', 'pack_id', 'pack_version', 'category', 'severity', 'summary')

SECRET_PATTERNS = (
    ('GOOGLE_API_KEY', re.compile(r'AIza[0-9A-Za-z_\-]{35}')),
    ('SK_KEY', re.compile(r'\bsk-[A-Za-z0-9_\-]{20,}')),
    ('GITHUB_TOKEN', re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})')),
    ('AWS_ACCESS_KEY', re.compile(r'\bAKIA[0-9A-Z]{16}\b')),
    ('SLACK_TOKEN', re.compile(r'\bxox[baprs]-[A-Za-z0-9-]{10,}')),
    ('PRIVATE_KEY_BLOCK', re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----')),
    ('BEARER_TOKEN', re.compile(r'[Bb]earer\s+[A-Za-z0-9._\-]{24,}')),
    ('API_KEY_FIELD', re.compile(r'''["']?(?:api[_-]?key|apiKey|secret|token|password)["']?\s*[:=]\s*["'][A-Za-z0-9_\-./+=]{16,}["']''')),
)

PATH_PATTERNS = (
    ('WINDOWS_PATH', re.compile(r'[A-Za-z]:(?:\\+|/)[^\s"\']+')),
    ('UNC_PATH', re.compile(r'\\\\[A-Za-z0-9_.-]+\\[^\s"\']+')),
    ('UNIX_PATH', re.compile(r'(?:^|[\s"\'=(])/(?:Users|home|var|etc|opt|srv|root|tmp|workspace)/[^\s"\']+')),
)

CODE_PATTERNS = (
    ('CODE_FENCE', re.compile(r'```')),
    ('PYTHON_DEF', re.compile(r'\b(?:async\s+)?def\s+[A-Za-z_]\w*\s*\(')),
    ('PYTHON_IMPORT', re.compile(r'(?:^|\n)\s*(?:import\s+[A-Za-z_][\w.]*|from\s+[A-Za-z_][\w.]*\s+import\s+)', re.MULTILINE)),
    ('JS_CODE', re.compile(r'\b(?:export\s+(?:default\s+)?(?:async\s+)?function|const\s+\w+\s*=\s*(?:async\s+)?\(|import\s+\{[^}]+\}\s+from\s+["\'])')),
    ('SQL_RAW', re.compile(r'\b(?:CREATE\s+TABLE|SELECT\s+\*\s+FROM|INSERT\s+INTO|DROP\s+TABLE)\b', re.IGNORECASE)),
    ('SCRIPT_TAG', re.compile(r'</?script\b|<\?php', re.IGNORECASE)),
)


def compute_signature(body: bytes, secret: str | bytes) -> str:
    # Sample: Sign raw JSON bytes with secret K-1. Return lowercase SHA-256 hex digest.
    key = secret.encode('utf-8') if isinstance(secret, str) else secret
    return hmac.new(key, body, hashlib.sha256).hexdigest()


def _verify_signature(raw_body: bytes, header_value: Optional[str], secret: Optional[str | bytes]) -> bool:
    # Sample: Compare X-Durai-Signature header against expected HMAC digest. Return False on mismatch or missing secret.
    if not secret or not header_value:
        return False
    provided = header_value.strip()
    if provided.lower().startswith('sha256='):
        provided = provided[7:].strip()
    expected = compute_signature(raw_body, secret)
    return hmac.compare_digest(provided.lower(), expected.lower())


def _iter_strings(value: Any):
    # Sample: Walk nested dict and list values. Yield every string field and key.
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str):
                yield key
            yield from _iter_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_strings(item)


def find_leak_markers(payload: Any) -> Optional[str]:
    # Sample: Scan payload dict for API keys, file paths, and code fences. Return first matched rule name or None.
    for text in _iter_strings(payload):
        for rule_group in (SECRET_PATTERNS, PATH_PATTERNS, CODE_PATTERNS):
            for rule_name, pattern in rule_group:
                if pattern.search(text):
                    return rule_name
    return None


def _validate_ticket_payload(payload: Any) -> tuple[Optional[dict], Optional[str]]:
    # Sample: Validate ticket fields, category, severity, PUBLIC_SHELL mode, and leak rules. Return record or error code.
    if not isinstance(payload, dict):
        return None, 'Payload must be a JSON object'
    unknown = sorted(set(payload) - ALLOWED_FIELDS)
    if unknown:
        return None, f'Unsupported fields: {", ".join(unknown)}'
    for field in REQUIRED_FIELDS:
        val = payload.get(field)
        if not isinstance(val, str) or not val.strip():
            return None, f'Missing or invalid required field: {field}'
    source_mode = payload.get('source_mode', 'PUBLIC_SHELL')
    if source_mode != 'PUBLIC_SHELL':
        return None, 'Only PUBLIC_SHELL tickets are accepted'
    if payload['category'] not in ALLOWED_CATEGORIES:
        return None, f"Unsupported category: {payload['category']}"
    if payload['severity'] not in ALLOWED_SEVERITIES:
        return None, f"Unsupported severity: {payload['severity']}"
    sanitized_payload = payload.get('sanitized_payload', {})
    if not isinstance(sanitized_payload, dict):
        return None, 'sanitized_payload must be a JSON object'
    leak_rule = find_leak_markers(payload)
    if leak_rule:
        return None, f'Rejected sensitive or raw-code content ({leak_rule})'
    fingerprint = payload['fingerprint'].strip()
    ticket_id = str(payload.get('ticket_id') or '').strip()
    if not ticket_id:
        ticket_id = 'T-' + hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()[:12]
    record = {
        'ticket_id': ticket_id,
        'fingerprint': fingerprint,
        'source_mode': 'PUBLIC_SHELL',
        'client_instance_hash': payload.get('client_instance_hash'),
        'pack_id': payload['pack_id'].strip(),
        'pack_version': payload['pack_version'].strip(),
        'shell_version': str(payload.get('shell_version') or '1.0.0').strip(),
        'category': payload['category'],
        'severity': payload['severity'],
        'summary': payload['summary'].strip(),
        'sanitized_payload': dict(sanitized_payload),
    }
    return record, None


def create_intake_app(store: Any, hmac_secret: Optional[str | bytes] = None, max_bytes: int = 16384) -> FastAPI:
    # Sample: Build FastAPI intake app with GET /v1/health and signed POST /v1/tickets storing PUBLIC_SHELL tickets.
    app = FastAPI(title='Durai Ticket Intake API', version='1.0.0')
    resolved_secret = hmac_secret if hmac_secret is not None else os.environ.get('DURAI_INTAKE_HMAC_SECRET')

    @app.get('/v1/health')
    def health() -> dict:
        # Sample: Return service status ok on GET /v1/health.
        return {'status': 'ok'}

    @app.post('/v1/tickets')
    async def post_ticket(request: Request) -> JSONResponse:
        # Sample: Enforce byte limit, check X-Durai-Signature, reject code or secret leaks, and upsert PUBLIC_SHELL ticket.
        content_length = request.headers.get('content-length')
        if content_length is not None:
            try:
                if int(content_length) > max_bytes:
                    return JSONResponse(status_code=413, content={'error': 'Payload exceeds byte limit'})
            except ValueError:
                return JSONResponse(status_code=400, content={'error': 'Invalid Content-Length header'})
        raw_body = await request.body()
        if len(raw_body) > max_bytes:
            return JSONResponse(status_code=413, content={'error': 'Payload exceeds byte limit'})
        if not raw_body:
            return JSONResponse(status_code=400, content={'error': 'Empty request body'})
        if not _verify_signature(raw_body, request.headers.get('x-durai-signature'), resolved_secret):
            return JSONResponse(status_code=401, content={'error': 'Invalid or missing X-Durai-Signature'})
        try:
            payload = json.loads(raw_body.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return JSONResponse(status_code=400, content={'error': 'Invalid UTF-8 JSON body'})
        record, error = _validate_ticket_payload(payload)
        if error is not None:
            return JSONResponse(status_code=400, content={'error': error})
        stored = store.upsert_ticket(record)
        status_code = 201 if int(stored.get('occurrence_count', 1)) == 1 else 200
        return JSONResponse(status_code=status_code, content=stored)

    return app
