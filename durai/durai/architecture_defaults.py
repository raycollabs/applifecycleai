"""Sample: Add seven preferences to G-10. Keep G-10 unchanged and return a new candidate."""
from copy import deepcopy
import json
from pathlib import Path
from .common import Conflict, digest
from .domain_profiles import PROFILES, string_list
from .packs import make_record, validate_pack

PREFERENCE_FORMAT = 'durai-owner-preference/v1'
CATALOG = Path(__file__).resolve().parents[1] / 'design/architecture-defaults-v1.json'


def validate_preference(policy):
    # Sample: Remove the exception evidence list. Refuse the incomplete preference.
    p = policy.get('preference')
    keys = {'format', 'preferred', 'when', 'notApplicable', 'alternatives', 'exceptionEvidence'}
    if not isinstance(p, dict) or set(p) != keys or p['format'] != PREFERENCE_FORMAT or policy.get('strength') != 'DEFAULT':
        raise Conflict('Owner preference shape or strength differs')
    if not isinstance(policy.get('relatedPolicyId'), str) or not policy['relatedPolicyId'].strip():
        raise Conflict('Owner preference requires a paired correctness policy')
    if not isinstance(p['preferred'], str) or not p['preferred'].strip():
        raise Conflict('Owner preference needs an explicit choice')
    if any(not string_list(p[k]) for k in ('when', 'notApplicable', 'exceptionEvidence')):
        raise Conflict('Owner preference needs conditions and exception evidence')
    alternatives = p['alternatives']
    if not isinstance(alternatives, list) or not alternatives:
        raise Conflict('Owner preference needs permitted alternatives')
    names = []
    for item in alternatives:
        if (not isinstance(item, dict) or set(item) != {'id', 'when'} or
                any(not isinstance(item[k], str) or not item[k].strip() for k in ('id', 'when'))):
            raise Conflict('Alternative needs an identity and use condition')
        names.append(item['id'])
    if len(set(names)) != len(names) or p['preferred'] in names:
        raise Conflict('Alternative identity is repeated')
    return digest(p)


def catalog_patterns(catalog=None):
    # Sample: Read storage and concurrency defaults. Return each preference and its separate correctness rule.
    from .ontology_patterns import AUDIENCES, validate_pattern
    c = deepcopy(catalog) if catalog is not None else json.loads(CATALOG.read_text(encoding='utf-8'))
    if c.get('format') != 'durai-architecture-defaults/v1' or c.get('profiles') != list(PROFILES):
        raise Conflict('Architecture catalog identity or profiles differ')
    ids = [item['id'] for item in c['patterns']]
    if set(ids) != {'storage', 'concurrency', 'idempotency', 'publication', 'consumption', 'jobs', 'workflows'} or len(ids) != 7:
        raise Conflict('Architecture catalog requires seven distinct concerns')
    result = []
    for item in c['patterns']:
        identity = 'architecture.' + item['id']
        for strength in ('DEFAULT', 'REQUIRED'):
            pattern = {'format': 'durai-pattern/v1', 'id': identity + ('.default' if strength == 'DEFAULT' else '.correctness'),
                'rule': item['rule'] if strength == 'DEFAULT' else item['correctness'], 'audience': list(AUDIENCES),
                'applicability': {'profiles': list(c['profiles']), 'when': list(item['when'])}, 'strength': strength,
                'invariants': [] if strength == 'DEFAULT' else list(item['invariants']),
                'procedure': list(item['procedure']), 'examples': list(item['examples']), 'checks': list(item['checks']),
                'evidence': [dict(c['sources'][s], checkedAt=c['checkedAt']) for s in item['sources']],
                'provenance': {'catalogId': c['id'], 'catalogDigest': digest(c), 'authority': c['authority'],
                    'sourceKind': 'OWNER_PREFERENCE' if strength == 'DEFAULT' else 'CORRECTNESS_CONTRACT',
                    'reviewState': 'IMPLEMENTED_UNREVIEWED'},
                'relatedPolicyId': identity + ('.correctness' if strength == 'DEFAULT' else '.default')}
            if strength == 'DEFAULT':
                pattern['preference'] = dict(format=PREFERENCE_FORMAT, **{k: deepcopy(item[k]) for k in
                    ('preferred', 'when', 'notApplicable', 'alternatives', 'exceptionEvidence')})
            validate_pattern(pattern)
            result.append(pattern)
    return result


def architecture_defaults_pack(base, catalog=None):
    # Sample: Extend G-10 with 14 typed records. Check all original records and gates remain equal.
    if base['kind'] != 'GENERIC' or base['id'] != 'durai.software-generation':
        raise Conflict('Architecture defaults require the software GENERIC base')
    source = {'id': base['id'], 'version': base['version'], 'digest': validate_pack(base)}
    selected = catalog_patterns(catalog)
    result = deepcopy(base)
    if any(r['id'].startswith('pattern.architecture.') for r in result['records']):
        raise Conflict('Base already contains architecture defaults')
    result['records'].extend(make_record('pattern.' + p['id'], 'DESIGN', 'KNOWLEDGE', {'policies': [p]}) for p in selected)
    result['version'] = '11-' + digest({'base': source, 'patterns': selected})[:16]
    result['reviewState'] = 'IMPLEMENTED_UNREVIEWED'
    result['provenance'] = dict(result.get('provenance', {}), derivedFrom=source,
        architectureCatalogDigest=selected[0]['provenance']['catalogDigest'])
    validate_pack(result)
    return result
