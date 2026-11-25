"""Sample: Validate single declarative app manifest unifying routes, caps, reviewer roles, and workflow."""
import re
from .common import Conflict, digest as content_digest
from .workflow_definition import validate as validate_workflow

FORMAT = 'durai-manifest/v1'
IDENTITY = re.compile(r'[A-Za-z0-9_.-]{1,160}')
KNOWN_REVIEWERS = {'C-REV', 'P-REV', 'S-REV'}


def identity(value: str, label: str) -> str:
    # Sample: Check id trade-pilot. Return it. Check id "a b". Raise Conflict.
    if not isinstance(value, str) or not IDENTITY.fullmatch(value):
        raise Conflict(label + ' requires 1 to 160 letters, digits, dot, dash, or underscore')
    return value


def validate_routes(routes: dict) -> dict:
    # Sample: Check routes dict with builder and c_rev keys. Return normalized routes.
    if not isinstance(routes, dict) or not routes:
        raise Conflict('Manifest routes must be a non-empty dictionary')
    required_roles = {'builder'}
    if not required_roles.issubset(routes.keys()):
        raise Conflict('Manifest routes must specify a builder role')
    for role, route in routes.items():
        if not isinstance(role, str) or not isinstance(route, str):
            raise Conflict('Routes keys and values must be strings')
    return dict(routes)


def validate_caps(caps: dict) -> dict:
    # Sample: Check caps with callsPerComponent and maxCostUSD. Return normalized integer and float caps.
    if not isinstance(caps, dict):
        raise Conflict('Manifest caps must be a dictionary')
    defaults = {
        'callsPerComponent': 80,
        'totalCalls': 400,
        'costEnterpriseUSD': 20.0,
        'stallNudge': 30,
        'stallStop': 50,
    }
    normalized = {}
    for key, default in defaults.items():
        val = caps.get(key, default)
        if isinstance(default, int) and (not isinstance(val, int) or val <= 0):
            raise Conflict(f'Cap {key} must be a positive integer')
        elif isinstance(default, float) and (not isinstance(val, (int, float)) or val <= 0):
            raise Conflict(f'Cap {key} must be a positive number')
        normalized[key] = val
    return normalized


def validate_reviewers(reviewers: list) -> list:
    # Sample: Check list containing C-REV and P-REV. Return normalized reviewer list.
    if not isinstance(reviewers, list) or not reviewers:
        raise Conflict('Manifest reviewers must be a non-empty list')
    for r in reviewers:
        if r not in KNOWN_REVIEWERS:
            raise Conflict(f"Unknown reviewer role '{r}'. Known roles: {', '.join(sorted(KNOWN_REVIEWERS))}")
    return sorted(list(set(reviewers)))


def validate(manifest: dict) -> dict:
    # Sample: Validate full app manifest dict. Return normalized manifest with validated workflow.
    if not isinstance(manifest, dict) or set(manifest) - {'format', 'id', 'version', 'routes', 'caps', 'reviewers', 'components', 'workflow'}:
        raise Conflict('Manifest has unknown fields')
    if manifest.get('format') != FORMAT:
        raise Conflict('Manifest format must be ' + FORMAT)

    app_id = identity(manifest.get('id'), 'Manifest id')
    version = identity(str(manifest.get('version', '1.0')), 'Manifest version')
    routes = validate_routes(manifest.get('routes', {}))
    caps = validate_caps(manifest.get('caps', {}))
    reviewers = validate_reviewers(manifest.get('reviewers', []))

    components = manifest.get('components', [])
    if not isinstance(components, list) or not components:
        raise Conflict('Manifest requires a non-empty list of components')
    for c in components:
        identity(c, 'Component id')

    workflow = validate_workflow(manifest.get('workflow', {}))

    return {
        'format': FORMAT,
        'id': app_id,
        'version': version,
        'routes': routes,
        'caps': caps,
        'reviewers': reviewers,
        'components': components,
        'workflow': workflow,
    }


def digest(manifest: dict) -> str:
    # Sample: Digest manifest dictionary. Return deterministic SHA-256 digest.
    return content_digest(validate(manifest))
