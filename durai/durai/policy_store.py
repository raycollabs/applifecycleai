"""Sample: Record the 18 policies of G-4 in durai_ontology.policy. Read the coding-worker view of binding foundation-generic-v4."""
from psycopg.types.json import Jsonb
from .common import Conflict
from .db import transaction
from .policy_catalog import extract_policies, policy_digest


def record_policies(reference, database='durai_test', role='durai_runtime'):
    # Sample: Record pack * durai.software-generation 4. Return its 18 policy records. Record it again. Return the same 18 records.
    with transaction(role, database) as db:
        pack = db.execute(
            'SELECT scope,id,version,digest,document FROM durai_ontology.pack WHERE scope=%s AND id=%s AND version=%s',
            (reference['scope'], reference['id'], reference['version'])).fetchone()
        if not pack:
            raise KeyError('Pack is unavailable: ' + str(reference['id']) + '@' + str(reference['version']))
        if pack['digest'] != reference['digest']:
            raise Conflict('Pack digest differs: ' + str(reference['id']) + '@' + str(reference['version']))
        expected = extract_policies(pack['scope'], pack['document'])
        for entry in expected:
            db.execute(
                'INSERT INTO durai_ontology.policy(scope,pack_id,version,policy_id,rule,audience,digest) '
                'VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                (entry['scope'], entry['packId'], entry['packVersion'], entry['policyId'], entry['rule'],
                 Jsonb(entry['audience']), entry['digest']))
    stored = stored_policies(reference, database, role)
    if stored != expected:
        raise Conflict('Stored policies differ from pack ' + str(reference['id']) + '@' + str(reference['version']))
    return stored


def stored_policies(reference, database='durai_test', role='durai_runtime'):
    # Sample: Read the stored policies of pack * durai.software-generation 4. Return 18 records in policy ID order.
    with transaction(role, database) as db:
        rows = db.execute(
            'SELECT scope,pack_id,version,policy_id,rule,audience,digest FROM durai_ontology.policy '
            'WHERE scope=%s AND pack_id=%s AND version=%s',
            (reference['scope'], reference['id'], reference['version'])).fetchall()
    records = [
        {
            'scope': row['scope'],
            'packId': row['pack_id'],
            'packVersion': row['version'],
            'policyId': row['policy_id'],
            'rule': row['rule'],
            'audience': list(row['audience']),
            'digest': row['digest'],
        }
        for row in rows
    ]
    return sorted(records, key=lambda record: record['policyId'])


def worker_view(binding_id, audience, database='durai_test', role='durai_runtime'):
    # Sample: Read binding foundation-generic-v4 for app trading. Return four coding-worker policies and one view digest.
    with transaction(role, database) as db:
        binding = db.execute(
            'SELECT document FROM durai_ontology.binding WHERE id=%s ORDER BY revision DESC LIMIT 1',
            (binding_id,)).fetchone()
    if not binding:
        raise KeyError('Binding is unavailable: ' + str(binding_id))
    chosen = {}
    for reference in binding['document']['packs']:
        entries = stored_policies(reference, database, role)
        if not entries:
            raise Conflict('Pack has no stored policies: ' + str(reference['id']) + '@' + str(reference['version']))
        for entry in entries:
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
