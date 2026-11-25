"""Sample: Resolve G-2 and trading rules. Seal one worker contract."""
import copy
from .common import digest, Conflict
from .packs import PackStore, generic_pack
from .generation import DEFAULT_SPEC as SPEC


def resolve_contract(binding_id, specification=None, database='durai_local', revision=None):
    # Sample: Resolve G-2 for app A. Include its exact policy text and pack digests.
    specification = dict(specification or SPEC)
    from .engineering import validate_domain
    rules = validate_domain(specification)
    from .domain_profiles import profile_id
    profile = profile_id(specification)
    specification.setdefault('profile', profile)
    store = PackStore(database)
    binding = store.resolve(binding_id, revision)
    if binding['document']['mode'] != 'SYNTHETIC_LOCAL':
        raise Conflict('Reference-only policy cannot dispatch a worker')
    policies, domain, seen = [], [], {}
    for reference in binding['document']['packs']:
        pack = store.get(reference)
        for record in pack['records']:
            if record['type'] != 'KNOWLEDGE':
                continue
            if pack['kind'] == 'GENERIC':
                for policy in record['body'].get('policies', []):
                    if policy.get('format') == 'durai-pattern/v1':
                        continue
                    if policy['id'] in seen and seen[policy['id']] != policy['rule']:
                        raise Conflict('Conflicting engineering policy rules')
                    if policy['id'] not in seen:
                        if policy.get('audience') and 'coding-worker' not in policy['audience']:
                            continue
                        policies.append(copy.deepcopy(policy))
                        seen[policy['id']] = policy['rule']
            else:
                body = record['body']
                if profile != 'trading-position':
                    continue
                for key in ('quantityUnit', 'signedLots', 'correctionDirection'):
                    if key in body and body[key] != specification.get(key):
                        raise Conflict('Application ontology contradicts the domain specification')
                if record['id'] == 'sample.position-domain':
                    domain.append({'pack': reference, 'recordId': record['id'], 'recordDigest': record['digest'], 'body': body})
    mandatory = next(p['rule'] for p in generic_pack()['records'][0]['body']['policies'] if p['id'] == 'sample-comments-only')
    if seen.get('sample-comments-only') != mandatory or 'comment-purpose' in seen:
        raise Conflict('Upgrade the generic binding to the approved sample-only comment policy')
    from .ontology_delivery import agent_view
    view = agent_view(binding_id, 'coding-worker', database, specification, revision=binding['revision'])
    for entry in view['policies']:
        if 'pattern' in entry:
            policies.append(dict(copy.deepcopy(entry), id=entry['policyId']))
    body = {'format': 'durai-worker-contract/v2', 'bindingId': binding_id, 'bindingRevision': binding['revision'],
            'bindingDigest': binding['digest'], 'packs': binding['document']['packs'], 'policies': policies,
            'applicationKnowledge': domain, 'specification': specification, 'domainRules': rules,
            'allowedFiles': ['/workspace/program.py'], 'allowedTools': ['read_file', 'edit_file', 'write_file'],
            'delegation': False, 'releaseAuthority': False, 'commentPolicy': 'sample-only-asd-ste100/v2',
            'agentViewDigest': view['digest'], 'profile': profile}
    return dict(body, digest=digest(body))


def verify_contract(contract, database='durai_local'):
    # Sample: Change one rule byte or binding revision. Refuse the stale contract.
    body = {k: v for k, v in contract.items() if k != 'digest'}
    if digest(body) != contract.get('digest'):
        raise Conflict('Worker contract digest differs')
    current = resolve_contract(contract['bindingId'], contract['specification'], database)
    if current != contract:
        raise Conflict('Worker contract is stale or differs from effective policy')
    return contract


def repair_contract(database='durai_local', binding_id=None):
    # Sample: Use an explicit repair binding. Create a separate G-2 binding if none is supplied.
    if binding_id is None:
        store = PackStore(database)
        generic = store.install(generic_pack())
        binding_id = 'repair-policy-' + generic['digest'][:16]
        store.bind(binding_id, [generic], 'SYNTHETIC_LOCAL')
    return resolve_contract(binding_id, SPEC, database)
