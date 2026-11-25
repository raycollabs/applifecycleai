"""Sample: Post debit 100 and credit 100. Reverse both lines and retain the original."""
import re
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from .common import Conflict
from .db import transaction
from .idempotency import execute_request
from .outbox import check_name, enqueue


def insert_journal(db, currency, lines, reverses=None):
    # Sample: Store J-1 and its posted event in the same transaction.
    identity = str(uuid4())
    row = db.execute('INSERT INTO durai_transactions.journal (journal_id,currency,lines,reverses) '
                     'VALUES (%s,%s,%s,%s) ON CONFLICT (application_id,reverses) DO NOTHING RETURNING journal_id',
                     (identity,currency,Jsonb(lines),reverses)).fetchone()
    if row is None:
        raise Conflict('Journal already has a reversal')
    result = {'journalId':identity,'currency':currency,'lines':lines,'reverses':reverses}
    enqueue(db,'journal.'+identity,'journal.posted',result)
    return result


def post_journal(currency, lines, request_key, database='durai_test', role='durai_runtime'):
    # Sample: Post 100 and -100 in USD. Refuse an unbalanced sum.
    if not isinstance(currency,str) or not re.fullmatch(r'[A-Z]{3}',currency) or not isinstance(lines,list) or not 2 <= len(lines) <= 100:
        raise ValueError('Use one currency and 2 to 100 lines')
    total = 0
    for line in lines:
        if not isinstance(line,dict) or set(line) != {'account','signedMinor'}:
            raise ValueError('Invalid journal line')
        check_name(line['account'])
        amount = line['signedMinor']
        if type(amount) is not int or not 1 <= abs(amount) <= 999999999:
            raise ValueError('Use a nonzero amount in minor units')
        total += amount
    if total != 0:
        raise Conflict('Journal does not balance')
    return execute_request('journal.post',request_key,{'currency':currency,'lines':lines},
                           lambda db, body:insert_journal(db,body['currency'],body['lines']),database,role)


def read_row(db, journal_id):
    # Sample: Read J-1 in its application and return its original lines.
    if not isinstance(journal_id,str) or str(UUID(journal_id)) != journal_id:
        raise ValueError('A canonical journal UUID is required')
    row = db.execute('SELECT currency,lines,reverses FROM durai_transactions.journal WHERE journal_id=%s',
                     (journal_id,)).fetchone()
    if row is None:
        raise KeyError('Journal unavailable in this tenant')
    return row


def read_journal(journal_id, database='durai_test', role='durai_runtime'):
    # Sample: Read the persisted values for J-1.
    with transaction(role,database) as db:
        row = read_row(db,journal_id)
        return {'journalId':journal_id,'currency':row['currency'],'lines':row['lines'],
                'reverses':str(row['reverses']) if row['reverses'] else None}


def reverse_journal(journal_id, request_key, database='durai_test', role='durai_runtime'):
    # Sample: Reverse J-1 by writing J-2 with the opposite amounts.
    def reverse(db, body):
        # Sample: Read the two lines and retain the opposite lines in the linked journal.
        row = read_row(db,body['journalId'])
        if row['reverses'] is not None:
            raise Conflict('A reversal is terminal in this blueprint')
        return insert_journal(db,row['currency'],
                              [dict(line,signedMinor=-line['signedMinor']) for line in row['lines']],body['journalId'])
    return execute_request('journal.reverse',request_key,{'journalId':journal_id},reverse,database,role)
