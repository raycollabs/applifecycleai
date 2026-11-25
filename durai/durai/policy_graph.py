"""Sample: Project generic packs G-3 and G-4 into graph durai_pack_graph. Link one changed rule with one SUPERSEDES edge."""
import json
from .db import transaction
from .policy_catalog import extract_policies, policy_key, supersession_edges


def project_policies(scope, pack, database='durai_test', role='durai_runtime'):
    # Sample: Project G-4 with three policies. Return three Policy keys in key order.
    if scope != '*':
        raise ValueError('Policy graph projection accepts only scope *')
    keys = []
    with transaction(role, database) as connection:
        connection.execute('SET search_path=ag_catalog,public')
        for record in extract_policies(scope, pack):
            key = policy_key(record)
            parameters = {
                'key': key,
                'digest': record['digest'],
                'scope': record['scope'],
                'packId': record['packId'],
                'packVersion': record['packVersion'],
                'policyId': record['policyId'],
            }
            connection.execute(
                "SELECT * FROM ag_catalog.cypher('durai_pack_graph', $$"
                "MERGE (p:Policy {key: $key}) "
                "SET p.digest = $digest, p.scope = $scope, p.packId = $packId, "
                "p.packVersion = $packVersion, p.policyId = $policyId "
                "RETURN p.key"
                "$$, %s::agtype) AS (key agtype)",
                (json.dumps(parameters),))
            keys.append(key)
    return sorted(keys)


def project_supersession(scope, previous_pack, current_pack, database='durai_test', role='durai_runtime'):
    # Sample: Project G-3 and G-4 with one changed rule. Return one SUPERSEDES edge from version 4 to version 3.
    if scope != '*':
        raise ValueError('Policy graph projection accepts only scope *')
    project_policies(scope, previous_pack, database, role)
    project_policies(scope, current_pack, database, role)
    edges = supersession_edges(scope, previous_pack, current_pack)
    with transaction(role, database) as connection:
        connection.execute('SET search_path=ag_catalog,public')
        for edge in edges:
            connection.execute(
                "SELECT * FROM ag_catalog.cypher('durai_pack_graph', $$"
                "MATCH (a:Policy {key: $source}), (b:Policy {key: $target}) "
                "MERGE (a)-[e:SUPERSEDES]->(b) "
                "RETURN id(e)"
                "$$, %s::agtype) AS (edge agtype)",
                (json.dumps({'source': edge['from'], 'target': edge['to']}),))
    return edges


def graph_policies(pack_id, version, database='durai_test', role='durai_runtime'):
    # Sample: Read Policy nodes of pack durai.software-generation version 4. Return three keys with digests in key order.
    with transaction(role, database) as connection:
        connection.execute('SET search_path=ag_catalog,public')
        rows = connection.execute(
            "SELECT * FROM ag_catalog.cypher('durai_pack_graph', $$"
            "MATCH (p:Policy) WHERE p.packId = $packId AND p.packVersion = $packVersion "
            "RETURN p.key, p.digest"
            "$$, %s::agtype) AS (key agtype, digest agtype)",
            (json.dumps({'packId': pack_id, 'packVersion': version}),)).fetchall()
    policies = [{'key': json.loads(str(row['key'])), 'digest': json.loads(str(row['digest']))} for row in rows]
    return sorted(policies, key=lambda policy: policy['key'])
