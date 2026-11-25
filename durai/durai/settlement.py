"""Sample: Compare a provider refund of 600 units with its local result. Record the match."""
from uuid import UUID

from psycopg.types.json import Jsonb

from .common import Conflict, digest
from .db import transaction
from .outbox import check_name

KINDS = ('CHARGE', 'CAPTURE', 'REFUND')
LOCAL = """
SELECT 'CHARGE' AS kind, i.operation_id, f.account, i.currency, i.amount_minor, i.provider_reference
FROM durai_payments.intent i JOIN durai_payments.funds f
  ON (f.application_id,f.payment_id)=(i.application_id,i.operation_id)
WHERE i.state='SUCCEEDED' AND i.operation_id=ANY(%s::uuid[])
UNION ALL
SELECT o.kind, o.operation_id, f.account, i.currency, o.amount_minor, o.provider_reference
FROM durai_payments.funds_operation o JOIN durai_payments.funds f
  ON (f.application_id,f.payment_id)=(o.application_id,o.payment_id)
JOIN durai_payments.intent i ON (i.application_id,i.operation_id)=(o.application_id,o.payment_id)
WHERE o.state='SUCCEEDED' AND o.operation_id=ANY(%s::uuid[])
ORDER BY kind,operation_id
"""


def operation_key(kind, identity):
    # Sample: Read a refund kind and a canonical operation UUID.
    if kind not in KINDS or not isinstance(identity, str) or str(UUID(identity)) != identity:
        raise ValueError('Invalid settlement operation')
    return kind, identity


def reconcile_statement(account, statement_id, operations, statement, database='durai_test', role='durai_runtime'):
    # Sample: Match R-1 and report R-2 as missing. Keep the payment balances.
    check_name(account)
    check_name(statement_id)
    if not isinstance(operations, list) or not isinstance(statement, list) or max(len(operations), len(statement)) > 500:
        raise ValueError('Use at most 500 local operations and 500 statement lines')
    expected = {operation_key(kind, identity) for kind, identity in operations}
    if len(expected) != len(operations):
        raise ValueError('Local operation appears twice')
    actual = {}
    provider_refs = set()
    for line in statement:
        if not isinstance(line, dict) or set(line) != {
            'kind','operationId','currency','amountMinor','feeMinor','netMinor','providerReference',
        }:
            raise ValueError('Invalid settlement line')
        key = operation_key(line['kind'], line['operationId'])
        check_name(line['providerReference'])
        numbers = [line[field] for field in ('amountMinor','feeMinor','netMinor')]
        check_name(line['currency'], maximum=3)
        if (len(line['currency']) != 3 or not line['currency'].isalpha() or not line['currency'].isupper()
                or any(type(n) is not int or abs(n)>1999999998 for n in numbers)):
            raise ValueError('Invalid settlement amount or currency')
        amount, fee, net = numbers
        signed = -amount if key[0] == 'REFUND' else amount
        if amount <= 0 or fee < 0 or net != signed - fee:
            raise ValueError('Settlement net amount differs')
        ref = (key[0], line['providerReference'])
        if key in actual or ref in provider_refs:
            raise ValueError('Duplicate settlement line')
        actual[key] = line
        provider_refs.add(ref)
    source_digest = digest({'operations':sorted(expected),'lines':[actual[key] for key in sorted(actual)]})
    with transaction(role, database) as db:
        ids = [identity for _,identity in expected]
        local = {(row['kind'],str(row['operation_id'])):row for row in db.execute(LOCAL,(ids,ids))}
        if local.keys() != expected or any(row['account'] != account for row in local.values()):
            raise Conflict('Local settlement scope contains an unavailable operation')
        findings = []
        for kind, identity in sorted(expected | actual.keys()):
            key = kind, identity
            row, line = local.get(key), actual.get(key)
            if row is None:
                state = 'UNEXPECTED'
            elif line is None:
                state = 'MISSING'
            elif (row['currency'],row['amount_minor'],row['provider_reference']) != (
                    line['currency'],line['amountMinor'],line['providerReference']):
                state = 'MISMATCH'
            else:
                state = 'MATCHED'
            expected_fields = None if row is None else {
                'currency':row['currency'], 'amountMinor':row['amount_minor'], 'providerReference':row['provider_reference'],
            }
            findings.append({'kind':kind,'operationId':identity,'state':state,'local':expected_fields,'statement':line})
        report = {'statementId':statement_id,'account':account,'findings':findings}
        db.execute('INSERT INTO durai_transactions.settlement (statement_id,account,source_digest,report) '
                   'VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING', (statement_id,account,source_digest,Jsonb(report)))
        saved = db.execute('SELECT source_digest,report FROM durai_transactions.settlement WHERE account=%s AND statement_id=%s',
                           (account,statement_id)).fetchone()
        if saved['source_digest'] != source_digest:
            raise Conflict('Statement identity already has different inputs')
        return saved['report']
