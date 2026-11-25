"""Sample: Project binding B-A as trading. Read P-G and P-A. Read B-B as foreign. Check P-G and P-B."""
import json
from psycopg import sql
from .common import Conflict
from .db import transaction
from .policy_catalog import policy_key
from .policy_store import worker_view


def project_binding(binding_id, database='durai_test', role='durai_runtime'):
    # Sample: Call project_binding('B-A') as trading on durai_test. Project binding B-A into graph durai_app_<md5>. Return 18.
    with transaction(role, database) as db:
        db.execute('SET LOCAL search_path=pg_catalog,ag_catalog,pg_temp')
        row = db.execute(
            'SELECT durai_ontology.project_application_binding(%s) AS projected',
            (binding_id,)).fetchone()
    return int(row['projected'])


def application_policy_view(binding_id, database='durai_test', role='durai_runtime'):
    # Sample: Read binding B-A as trading. Read P-1 from durai_pack_graph and P-2 from durai_app_<md5>. Return two entries with key, digest, packDigest, sourceReference in key order.
    view = worker_view(binding_id, 'coding-worker', database, role)
    with transaction(role, database) as db:
        db.execute('SET LOCAL search_path=pg_catalog,ag_catalog,pg_temp')
        registry = db.execute(
            'SELECT application_id AS application_id, graph_name AS graph_name '
            'FROM durai_ontology.application_graph').fetchone()
        if not registry:
            raise Conflict('Application graph is not provisioned for this login')
        binding = db.execute(
            'SELECT document AS document FROM durai_ontology.binding '
            'WHERE application_id=%s AND id=%s ORDER BY revision DESC LIMIT 1',
            (registry['application_id'], binding_id)).fetchone()
        if not binding:
            raise Conflict('Binding is unavailable: ' + str(binding_id))
        pins = {}
        for reference in binding['document']['packs']:
            pins[str(reference['scope']) + ':' + str(reference['id']) + '@' + str(reference['version'])] = reference['digest']
        entries = []
        for policy in view['policies']:
            key = policy_key(policy)
            graph = 'durai_pack_graph' if policy['scope'] == '*' else registry['graph_name']
            statement = sql.SQL(
                'SELECT key AS key, digest AS digest, pack_digest AS pack_digest, source_reference AS source_reference '
                'FROM ag_catalog.cypher({}, $q$ MATCH (p:Policy {{key: $key}}) '
                'RETURN p.key, p.digest, p.packDigest, p.sourceReference $q$, %s::ag_catalog.agtype) '
                'AS (key ag_catalog.agtype, digest ag_catalog.agtype, pack_digest ag_catalog.agtype, '
                'source_reference ag_catalog.agtype)').format(sql.Literal(graph))
            rows = db.execute(statement, (json.dumps({'key': key}),)).fetchall()
            if len(rows) != 1:
                raise Conflict('Projected policy is missing or duplicated: ' + key)
            node = {}
            for field in ('key', 'digest', 'pack_digest', 'source_reference'):
                item = rows[0][field]
                node[field] = json.loads(item) if isinstance(item, str) and len(item) > 1 and item[0] == '"' and item[-1] == '"' else item
            if node['key'] != key or node['digest'] != policy['digest']:
                raise Conflict('Projected policy differs from the canonical policy: ' + key)
            pin = pins.get(str(policy['scope']) + ':' + str(policy['packId']) + '@' + str(policy['packVersion']))
            if pin is None or node['pack_digest'] != pin:
                raise Conflict('Pack pin differs from the projected policy: ' + key)
            entries.append({'key': key, 'digest': node['digest'], 'packDigest': node['pack_digest'],
                            'sourceReference': node['source_reference']})
    return sorted(entries, key=lambda entry: entry['key'])
