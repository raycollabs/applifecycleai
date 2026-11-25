"""Sample: Read pack G-4 with policies P-1 and P-2. Return two policy records and a coding-worker view with one digest."""
import hashlib
import json


def policy_digest(policy):
    # Sample: Hash {"id": "P-1", "rule": "DO NOT X."}. Return one 64-character sha256 hex value.
    text = json.dumps(policy, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def policy_key(record):
    # Sample: Read P-1 from scope *, pack durai.software-generation, version 4. Return *:durai.software-generation@4#P-1.
    return (
        str(record['scope']) + ':' + str(record['packId']) + '@'
        + str(record['packVersion']) + '#' + str(record['policyId'])
    )


def extract_policies(scope, pack):
    # Sample: Read pack G-4 with six KNOWLEDGE records that each hold P-1 and P-2. Return two policy records.
    found = {}
    for record in pack['records']:
        if record.get('type') != 'KNOWLEDGE':
            continue
        for policy in record['body'].get('policies', []):
            entry = {
                'scope': scope,
                'packId': pack['id'],
                'packVersion': pack['version'],
                'policyId': policy['id'],
                'rule': policy['rule'],
                'audience': list(policy.get('audience', [])),
                'digest': policy_digest(policy),
            }
            existing = found.get(policy['id'])
            if existing is None:
                found[policy['id']] = entry
            elif existing['digest'] != entry['digest']:
                raise ValueError('policy ' + str(policy['id']) + ' has different content in pack ' + str(pack['id']))
    return [found[policy_id] for policy_id in sorted(found)]


def worker_view(binding_id, packs, audience):
    # Sample: Bind G-4 with three policies, two for coding-worker. Return those two policies and one view digest.
    chosen = {}
    for scope, pack in packs:
        for entry in extract_policies(scope, pack):
            existing = chosen.get(entry['policyId'])
            if existing is None:
                chosen[entry['policyId']] = entry
            elif existing['rule'] != entry['rule']:
                raise ValueError('policy ' + str(entry['policyId']) + ' has different rule text in two packs')
    policies = [
        chosen[policy_id] for policy_id in sorted(chosen)
        if audience in chosen[policy_id]['audience']
    ]
    return {'bindingId': binding_id, 'audience': audience, 'policies': policies, 'digest': policy_digest(policies)}


def supersession_edges(scope, previous_pack, current_pack):
    # Sample: Compare G-3 and G-4 with one changed rule P-1. Return one SUPERSEDES edge from version 4 to version 3.
    previous = {entry['policyId']: entry for entry in extract_policies(scope, previous_pack)}
    current = {entry['policyId']: entry for entry in extract_policies(scope, current_pack)}
    edges = []
    for policy_id, entry in current.items():
        old = previous.get(policy_id)
        if old is None or old['digest'] == entry['digest']:
            continue
        edges.append({'from': policy_key(entry), 'to': policy_key(old), 'relation': 'SUPERSEDES'})
    return sorted(edges, key=lambda edge: edge['from'])
