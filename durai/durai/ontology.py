"""Fixed AGE projection and canonical relational rule lookup."""
import json
from psycopg import sql
from .common import digest
from .db import transaction


class RelationalOntology:
    def __init__(self, database='durai_local', role='durai_runtime'):
        self.database, self.role = database, role

    def get_rule(self, rule_id, version):
        with transaction(self.role, self.database) as db:
            row = db.execute('SELECT document FROM dev_trading.rule WHERE id=%s AND version=%s', (rule_id, version)).fetchone()
        if not row:
            raise KeyError('Rule unavailable in this scope')
        return row['document']


def project_trading_rule(database='durai_local'):
    rule = RelationalOntology(database).get_rule('quantity-unit', '1')
    params = json.dumps({'key': 'trading:quantity-unit:1', 'digest': digest(rule)})
    with transaction('postgres', database) as db:
        db.execute('SET search_path=ag_catalog,public')
        # Only this fixed query is allowed. Values are bound through an agtype map.
        row = db.execute("""SELECT value::text AS digest FROM ag_catalog.cypher('durai_trading_ontology', $$
          MERGE(n:Rule {key:$key}) SET n.canonicalDigest=$digest
          RETURN n.canonicalDigest $$, %s::ag_catalog.agtype) AS(value ag_catalog.agtype)""", (params,), prepare=True).fetchone()
    if row['digest'] != digest(rule):
        raise ValueError('AGE projection differs from canonical rule')
    return {'graph': 'durai_trading_ontology', 'key': 'trading:quantity-unit:1',
            'canonicalDigest': row['digest'], 'authority': 'dev_trading.rule'}
