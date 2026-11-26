"""Sample: Read 11 blueprint entries. Return 22 typed patterns and a pack that adds them to generic pack 14."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from .architecture_defaults import PREFERENCE_FORMAT, catalog_patterns as architecture_patterns
from .common import Conflict, digest
from .domain_profiles import PROFILES, string_list
from .pack_data import read_design
from .packs import make_record, validate_pack

FORMAT = 'durai-transaction-blueprints/v1'
ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'design/transaction-blueprints-v1.json'
CASES = ('idempotency', 'events', 'seats', 'inventory', 'charge', 'funds', 'settlement', 'journal', 'jobs', 'downloads', 'location')
ENTRY_KEYS = {'id', 'modules', 'preferred', 'rule', 'correctness', 'when', 'notApplicable', 'alternatives', 'exceptionEvidence',
              'invariants', 'procedure', 'checks', 'examples', 'interface', 'requires', 'profiles', 'sources'}


def load_catalog():
    # Sample: Read design/transaction-blueprints-v1.json. Return its 11 entries.
    return read_design('transaction-blueprints-v1.json')


def file_digest(path, root=None):
    # Sample: Read blueprints/payments/v2.sql under the root. Return its 64-character SHA-256 digest.
    return hashlib.sha256((Path(root) if root else ROOT).joinpath(path).read_bytes()).hexdigest()


def validate_interface(case, interface):
    # Sample: Read the funds interface. Refuse a function without a signature, a return shape, or an error list.
    if not isinstance(interface, dict) or set(interface) != {'functions', 'passedIn'} or not interface['functions']:
        raise Conflict('Blueprint interface needs functions and passed-in objects: ' + case)
    for item in interface['functions']:
        if (not isinstance(item, dict) or set(item) != {'name', 'signature', 'returns', 'errors'} or
                any(not isinstance(item[k], str) or not item[k].strip() for k in ('name', 'signature', 'returns')) or
                not isinstance(item['errors'], list) or any(not isinstance(e, str) or not e.strip() for e in item['errors'])):
            raise Conflict('Blueprint function interface differs: ' + case)
    for item in interface['passedIn']:
        if (not isinstance(item, dict) or set(item) != {'name', 'methods'} or not isinstance(item['name'], str) or
                not isinstance(item['methods'], list) or not item['methods']):
            raise Conflict('Blueprint passed-in interface differs: ' + case)
        for method in item['methods']:
            if (not isinstance(method, dict) or set(method) != {'name', 'returns', 'raises'} or
                    any(not isinstance(method[k], str) or not method[k].strip() for k in method)):
                raise Conflict('Blueprint method interface differs: ' + case)


def validate_catalog(catalog, root=None):
    # Sample: Read 11 entries and 36 source digests. Change one byte of blueprints/payments/v2.sql. Refuse the stale catalog.
    if (not isinstance(catalog, dict) or catalog.get('format') != FORMAT or catalog.get('profiles') != list(PROFILES) or
            not isinstance(catalog.get('entries'), list) or not isinstance(catalog.get('sources'), dict)):
        raise Conflict('Blueprint catalog identity or profiles differ')
    ids = [item.get('id') for item in catalog['entries']]
    if len(ids) != len(CASES) or set(ids) != set(CASES):
        raise Conflict('Blueprint catalog requires eleven distinct cases')
    for key, source in catalog['sources'].items():
        if set(source) != {'path', 'sha256'} or file_digest(source['path'], root) != source['sha256']:
            raise Conflict('Blueprint source changed: ' + str(source.get('path')))
    for item in catalog['entries']:
        if set(item) != ENTRY_KEYS:
            raise Conflict('Blueprint entry shape differs: ' + str(item.get('id')))
        if any(not isinstance(item[k], str) or not item[k].strip() for k in ('preferred', 'rule', 'correctness')):
            raise Conflict('Blueprint entry text is missing: ' + item['id'])
        if (any(not string_list(item[k]) for k in ('when', 'notApplicable', 'exceptionEvidence', 'invariants', 'procedure', 'checks',
                                                    'examples', 'requires', 'profiles', 'sources', 'modules')) or
                any(not text.startswith('Sample: ') for text in item['examples'])):
            raise Conflict('Blueprint entry lists differ: ' + item['id'])
        if any(name not in catalog['sources'] for name in item['sources']) or any(p not in PROFILES for p in item['profiles']):
            raise Conflict('Blueprint entry names an unknown source or profile: ' + item['id'])
        validate_interface(item['id'], item['interface'])
    return digest(catalog)


def catalog_patterns(catalog=None, root=None):
    # Sample: Read the funds entry. Return blueprint.funds.default with its preference and blueprint.funds.correctness with its interface.
    from .ontology_patterns import AUDIENCES, validate_pattern
    c = deepcopy(catalog) if catalog is not None else load_catalog()
    validate_catalog(c, root)
    result = []
    for item in c['entries']:
        identity = 'blueprint.' + item['id']
        for strength in ('DEFAULT', 'REQUIRED'):
            pattern = {'format': 'durai-pattern/v1', 'id': identity + ('.default' if strength == 'DEFAULT' else '.correctness'),
                'rule': item['rule'] if strength == 'DEFAULT' else item['correctness'], 'audience': list(AUDIENCES),
                'applicability': {'profiles': list(item['profiles']), 'when': list(item['when'])}, 'strength': strength,
                'invariants': [] if strength == 'DEFAULT' else list(item['invariants']),
                'procedure': list(item['procedure']), 'examples': list(item['examples']), 'checks': list(item['checks']),
                'evidence': [dict(c['sources'][s], checkedAt=c['checkedAt']) for s in item['sources']],
                'provenance': {'catalogId': c['id'], 'catalogDigest': digest(c), 'authority': c['authority'],
                    'sourceKind': 'OWNER_PREFERENCE' if strength == 'DEFAULT' else 'CORRECTNESS_CONTRACT',
                    'sourceDigests': {s: c['sources'][s]['sha256'] for s in item['sources']},
                    'reviewState': 'IMPLEMENTED_UNREVIEWED'},
                'relatedPolicyId': identity + ('.correctness' if strength == 'DEFAULT' else '.default'),
                'requires': list(item['requires']), 'modules': list(item['modules'])}
            if strength == 'DEFAULT':
                pattern['preference'] = dict(format=PREFERENCE_FORMAT, **{k: deepcopy(item[k]) for k in
                    ('preferred', 'when', 'notApplicable', 'alternatives', 'exceptionEvidence')})
            else:
                pattern['interface'] = deepcopy(item['interface'])
            validate_pattern(pattern)
            result.append(pattern)
    return result


def stall_retry_rules(rules):
    # Sample: Read rules [passed, stalled-worker, needs-review]. Return [passed, stalled-retry, stalled-worker, needs-review]. Read rules with stalled-retry. Return them unchanged.
    if any(rule['id'] == 'stalled-retry' for rule in rules):
        return deepcopy(rules)
    retry = {'id': 'stalled-retry', 'when': {'origin': 'guard', 'attemptsLeft': True, 'callsForRetry': True, 'timeLeft': True},
             'choice': 'RETRY_CANDIDATE'}
    return [item for rule in rules for item in ([retry, deepcopy(rule)] if rule['id'] == 'stalled-worker' else [deepcopy(rule)])]


def transaction_pack(base, catalog=None, root=None):
    # Sample: Extend generic pack 14 with 14 architecture and 22 blueprint patterns. Return version 14-tx- and 16 hex digits.
    if base['kind'] != 'GENERIC' or base['id'] != 'durai.software-generation' or base['version'] != '14':
        raise Conflict('Transaction blueprints require generic pack 14')
    source = {'id': base['id'], 'version': base['version'], 'digest': validate_pack(base)}
    selected = architecture_patterns() + catalog_patterns(catalog, root)
    result = deepcopy(base)
    if any(r['id'].startswith('pattern.architecture.') or r['id'].startswith('pattern.blueprint.') for r in result['records']):
        raise Conflict('Base already contains typed patterns')
    result['records'].extend(make_record('pattern.' + p['id'], 'DESIGN', 'KNOWLEDGE', {'policies': [p]}) for p in selected)
    rules = stall_retry_rules(next(r for r in result['records'] if r['id'] == 'decision.failure-triage')['body']['rules'])
    result['records'] = [make_record(r['id'], r['layer'], r['type'], dict(r['body'], rules=rules))
                         if r['id'] == 'decision.failure-triage' else r for r in result['records']]
    result['version'] = '14-tx-' + digest({'base': source, 'patterns': selected, 'triageRules': rules})[:16]
    result['reviewState'] = 'IMPLEMENTED_UNREVIEWED'
    result['provenance'] = dict(result.get('provenance', {}), derivedFrom=source,
        architectureCatalogDigest=selected[0]['provenance']['catalogDigest'],
        blueprintCatalogDigest=selected[-1]['provenance']['catalogDigest'])
    validate_pack(result)
    return result
