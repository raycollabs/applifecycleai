"""Sample: Normalize 30 trading rules. Keep their original source records unchanged."""
from copy import deepcopy
from .common import Conflict, digest
from .packs import make_record, validate_pack
from .domain_profiles import PROFILES

FORMAT = 'durai-pattern/v1'
NORMALIZER = 'exchange-pattern-normalizer/v1'
AUDIENCES = ('coding-worker', 'code-reviewer', 'support-reviewer')


def validate_pattern(policy):
    # Sample: Remove applicability from rule R-1. Refuse the incomplete pattern.
    required = {'format', 'id', 'rule', 'audience', 'applicability', 'strength', 'invariants',
                'procedure', 'examples', 'checks', 'evidence', 'provenance'}
    if not isinstance(policy, dict) or not required <= policy.keys() or policy['format'] != FORMAT:
        raise Conflict('Incomplete typed ontology pattern')
    if any(not isinstance(policy[k], str) or not policy[k].strip() for k in ('id', 'rule')):
        raise Conflict('Pattern identity and rule must be text')
    if policy['strength'] not in ('REQUIRED', 'DEFAULT', 'REFERENCE'):
        raise Conflict('Unknown pattern strength')
    if (not isinstance(policy['audience'], list) or not policy['audience'] or
            any(a not in AUDIENCES for a in policy['audience']) or not isinstance(policy['applicability'], dict) or
            not isinstance(policy['applicability'].get('profiles'), list) or not policy['applicability']['profiles'] or
            any(p not in (*PROFILES, '*') for p in policy['applicability']['profiles'])):
        raise Conflict('Pattern audience or applicability differs')
    if any(not isinstance(policy[k], list) for k in ('invariants', 'procedure', 'examples', 'checks', 'evidence')) or not isinstance(policy['provenance'], dict):
        raise Conflict('Pattern metadata has an invalid shape')
    if any(k in policy for k in ('allowedTools', 'grants', 'handlers', 'releaseAuthority')):
        raise Conflict('Ontology content cannot grant agent authority')
    if 'preference' in policy:
        from .architecture_defaults import validate_preference
        validate_preference(policy)
    return digest(policy)


def patterns(pack):
    # Sample: Read the pinned exchange catalog. Return 30 typed references with source digests.
    found = {}
    for record in pack['records']:
        if record['type'] != 'KNOWLEDGE':
            continue
        for policy in record['body'].get('policies', []):
            if policy.get('format') != FORMAT:
                continue
            validate_pattern(policy)
            old = found.get(policy['id'])
            if old and digest(old) != digest(policy):
                raise Conflict('Conflicting typed pattern definitions')
            found[policy['id']] = deepcopy(policy)
    if (pack['kind'] != 'APPLICATION' or pack['id'] != 'durai.app.exchange-trading' or
            pack.get('provenance', {}).get('mapperVersion') != 'exchange-pack-adapter/3'):
        return [found[k] for k in sorted(found)]
    records = {r['id']: r for r in pack['records']}
    for record in pack['records']:
        body = record['body']
        if record['type'] != 'KNOWLEDGE' or not record['id'].startswith('rule.') or not {'id', 'text', 'layer', 'profile', 'evidence'} <= body.keys():
            continue
        policy_id = 'exchange-trading.' + body['id']
        if policy_id in found:
            if found[policy_id]['rule'] != body['text'] or found[policy_id]['provenance'].get('sourceRecordDigest') != record['digest']:
                raise Conflict('Typed pattern contradicts its retained source')
            continue
        procedure_id = 'catalog.' + body['layer'].lower() + '.procedure'
        procedure = records.get(procedure_id)
        policy = {'format': FORMAT, 'id': policy_id, 'rule': body['text'], 'audience': list(AUDIENCES),
            'applicability': {'profiles': ['trading-position']}, 'strength': 'REFERENCE',
            'invariants': [], 'examples': [], 'checks': [
                {'recordId': 'rule.' + n, 'recordDigest': records['rule.' + n]['digest'], 'rule': records['rule.' + n]['body']['text']}
                for n in body.get('neighbors', []) if n.startswith('test.') and 'rule.' + n in records],
            'procedure': ([{'recordId': procedure_id, 'recordDigest': procedure['digest'],
                'steps': deepcopy(procedure['body'].get('body', {}).get('steps', []))}] if procedure else []),
            'evidence': list(body['evidence']), 'provenance': {'normalizer': NORMALIZER,
                'sourcePackId': pack['id'], 'sourcePackVersion': pack['version'], 'sourceRecordId': record['id'],
                'sourceRecordDigest': record['digest'], 'sourceProfile': body['profile'],
                'reviewState': pack.get('reviewState', 'UNREVIEWED')}}
        validate_pattern(policy)
        found[policy_id] = policy
    return [found[k] for k in sorted(found)]


def normalized_application_pack(pack):
    # Sample: Normalize application version 1. Return a new version with the original records and 30 typed policies.
    if pack['kind'] != 'APPLICATION':
        raise Conflict('Only APPLICATION packs use this normalization constructor')
    selected = patterns(pack)
    if not selected:
        raise Conflict('No known application patterns are available')
    result = deepcopy(pack)
    source = {'id': pack['id'], 'version': pack['version'], 'digest': validate_pack(pack)}
    known = {r['id'] for r in result['records']}
    for policy in selected:
        identity = 'pattern.' + policy['id']
        if identity not in known:
            result['records'].append(make_record(identity, 'DESIGN', 'KNOWLEDGE', {'policies': [policy]}))
    result['provenance'] = dict(result.get('provenance', {}), normalizedFrom=source, normalizer=NORMALIZER)
    result['reviewState'] = 'IMPLEMENTED_UNREVIEWED'
    result['version'] = '2-' + digest({'source': source, 'patterns': selected, 'normalizer': NORMALIZER})[:12]
    validate_pack(result)
    return result
