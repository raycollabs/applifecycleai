"""Sample: Append message M-1. Claim the turn. Publish and read a summary."""
from hashlib import sha256

import psycopg
from psycopg.types.json import Jsonb

from .common import Conflict
from .context_store import clean_value
from .db import ROOT, connect, transaction
from .session_settings import load_session_settings

MIGRATIONS = (ROOT / 'migrations/session_context/session_context_0001.sql',
              ROOT / 'migrations/session_context/session_context_0002.sql')
MIGRATION = MIGRATIONS[0]


def prepare_session_context(database='durai_test'):
    # Sample: Apply session_context_0001.sql and session_context_0002.sql to a test database. Apply again. Check 2 receipts.
    applied = []
    with connect('postgres', database) as db:
        db.execute("SELECT pg_advisory_xact_lock(hashtext('durai-migration'))")
        for path in MIGRATIONS:
            source = path.read_bytes()
            digest = sha256(source).hexdigest()
            previous = db.execute('SELECT digest FROM durai_core.migration WHERE name=%s', (path.name,)).fetchone()
            if previous:
                if previous['digest'] != digest:
                    raise Conflict('Stored session context migration digest differs')
            else:
                db.execute(source.decode('utf-8'), prepare=False)
                db.execute('INSERT INTO durai_core.migration(name,digest) VALUES(%s,%s)', (path.name, digest))
            applied.append({'name': path.name, 'sha256': digest})
    return {'name': applied[0]['name'], 'sha256': applied[0]['sha256'], 'migrations': applied}


def node_text(value):
    # Sample: Flatten goal "Book lots" and refs ["93a7a7b"] into the text "Book lots 93a7a7b".
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ' '.join(node_text(item) for item in value)
    if isinstance(value, dict):
        return ' '.join(node_text(item) for key, item in value.items() if key != 'sourceTurns')
    return ''


class SessionContext:
    def __init__(self, database='durai_local', role='durai_runtime'):
        # Sample: Open a handle for the trading runtime. Make no connection until a turn arrives.
        self.database = database
        self.role = role

    def _one(self, query, args):
        # Sample: Try to finish a stale lease. Return a conflict and keep the current summary.
        try:
            with transaction(self.role, self.database) as db:
                return db.execute(query, args).fetchone()
        except (psycopg.errors.CheckViolation, psycopg.errors.InvalidParameterValue,
                psycopg.errors.ForeignKeyViolation, psycopg.errors.NotNullViolation):
            raise Conflict('Session input, reference, or lease differs') from None

    def append_turn(self, conversation_id, host_message_id, role, text, token_count, plan_id=None, *, flush=False):
        # Sample: Append message M-1 twice. Check that both replies show turn 1.
        clean_value({'text': text})
        return self._one('SELECT * FROM durai_context.append_session_turn(%s,%s,%s,%s,%s,%s,%s)',
                         (conversation_id, host_message_id, role, text, token_count, plan_id, flush))

    def application_id(self):
        # Sample: Connect as the trading runtime. Read the trading application ID.
        return self._one('SELECT durai_core.application_id() AS application_id', ())['application_id']

    def claim(self, lease_seconds=120, max_turns=64, max_tokens=2000):
        # Sample: Claim turns 1 to 4. Check that a second controller gets no claim.
        cfg = load_session_settings()
        if not cfg.enabled:
            return None
        return self._one('SELECT durai_context.claim_session_summary(%s,%s,%s,%s,%s,%s::numeric) AS claim',
                         (lease_seconds, max_turns, max_tokens, cfg.maxCallsPerConversationPerDay,
                          cfg.maxCallsPerDay, cfg.maxCostUsdPerDay))['claim']

    def record_spend(self, conversation_id, calls, input_tokens, output_tokens, cost_usd):
        # Sample: Record 1 retry call with 900 input tokens, 120 output tokens, and 0.0004 USD for C-1.
        return self._one('SELECT durai_context.record_session_spend(%s,%s,%s,%s,%s::numeric) AS spend',
                         (conversation_id, calls, input_tokens, output_tokens, cost_usd))

    def finish(self, claim, outcome, body=None, profile=None):
        # Sample: Publish turns 1 to 4 at generation 1. Check summary revision 1.
        body_value = Jsonb(clean_value(body)) if body is not None else None
        profile_value = Jsonb(clean_value(profile)) if profile is not None else None
        return self._one('SELECT durai_context.finish_session_summary(%s,%s,%s,%s,%s,%s,%s) AS result',
                         (claim['conversationId'], claim['generation'], claim['revision'],
                          outcome, body_value, profile_value, load_session_settings().maxAttemptsPerRange))['result']

    def publish_segment(self, claim, node, profile, head):
        # Sample: Publish segment 1 to 12 with a head of the same text. Check revision 1.
        return self._one('SELECT durai_context.publish_session_segment(%s,%s,%s,%s,%s,%s) AS result',
                         (claim['conversationId'], claim['generation'], claim['revision'], Jsonb(clean_value(node)),
                          Jsonb(clean_value(profile)), Jsonb(clean_value(head))))['result']

    def read_nodes(self, conversation_id):
        # Sample: Read 4 level-0 nodes for turns 1 to 48 and the head revision 4.
        return self._one('SELECT durai_context.read_session_nodes(%s) AS nodes', (conversation_id,))['nodes']

    def publish_merge(self, conversation_id, expected_revision, level, first, last, node, profile, head):
        # Sample: Merge 4 level-0 nodes into level 1 for turns 1 to 48 at revision 4. Check revision 5.
        return self._one('SELECT durai_context.publish_session_merge(%s,%s,%s,%s,%s,%s,%s,%s) AS result',
                         (conversation_id, expected_revision, level, first, last, Jsonb(clean_value(node)),
                          Jsonb(clean_value(profile)), Jsonb(clean_value(head))))['result']

    def read_spend(self, conversation_id):
        # Sample: Read 3 calls for C-1 today and 7 calls for all conversations today.
        return self._one('SELECT durai_context.read_session_spend(%s) AS spend', (conversation_id,))['spend']

    def withdraw_turn(self, conversation_id, turn_number):
        # Sample: Withdraw turn 2. Check that the new context has no summary with its text.
        return self._one('SELECT durai_context.withdraw_session_turn(%s,%s) AS withdrawn',
                         (conversation_id, turn_number))['withdrawn']

    def get_context(self, conversation_id, *, tail_tokens=2000, tail_turns=200):
        # Sample: Read S-1 with a two-turn limit. Check the two latest turns and the omitted range.
        if not 1 <= tail_tokens <= 65536 or not 1 <= tail_turns <= 200:
            raise ValueError('Invalid session tail budget')
        with transaction(self.role, self.database) as db:
            db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            head = db.execute('SELECT durai_context.read_session_head(%s) AS head', (conversation_id,)).fetchone()['head']
            if head is None:
                return None
            covered, latest = head['coveredTurn'], head['latestTurn']
            rows = db.execute('WITH tail AS (SELECT turn_number,role,eligible_text,token_count,plan_id,digest '
                              'FROM durai_context.session_turn WHERE application_id=durai_core.application_id() '
                              'AND conversation_id=%s AND turn_number>%s AND turn_number<=%s '
                              'ORDER BY turn_number DESC LIMIT %s) '
                              'SELECT * FROM (SELECT *,sum(token_count) OVER(ORDER BY turn_number DESC) AS total '
                              'FROM tail) AS sized WHERE total<=%s ORDER BY turn_number',
                              (conversation_id, covered, latest, tail_turns, tail_tokens)).fetchall()
        first = rows[0]['turn_number'] if rows else latest + 1
        return dict(head, turns=[{key: value for key, value in row.items() if key != 'total'} for row in rows],
                    omittedRange=[covered + 1, first - 1] if first > covered + 1 else None)

    def read_turns(self, conversation_id, first, last):
        # Sample: Fetch omitted turns 1 to 3 after reading the latest two turns.
        if not 1 <= first <= last or last - first >= 200:
            raise ValueError('Session range must contain 1 to 200 turns')
        with transaction(self.role, self.database) as db:
            return db.execute('SELECT turn_number,role,eligible_text,plan_id,digest FROM durai_context.session_turn '
                              'WHERE application_id=durai_core.application_id() AND conversation_id=%s '
                              'AND turn_number BETWEEN %s AND %s ORDER BY turn_number',
                              (conversation_id, first, last)).fetchall()

    def search(self, conversation_id, text, limit=5):
        # Sample: Search "93a7a7b" in C-1. Return the node for turns 1 to 12 first, with its exact references.
        if not isinstance(text, str) or not text.strip() or type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError('Invalid session search')
        nodes = self.read_nodes(conversation_id)
        if nodes is None:
            return []
        documents = [{'kind': 'node', 'level': node['level'], 'first': node['first'], 'last': node['last'],
                      'text': node_text(node['body']), 'refs': node['body'].get('refs', [])} for node in nodes['nodes']]
        with transaction(self.role, self.database) as db:
            db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            turns = db.execute('SELECT turn_number,eligible_text FROM durai_context.session_turn '
                               'WHERE application_id=durai_core.application_id() AND conversation_id=%s '
                               'AND eligible_text IS NOT NULL', (conversation_id,)).fetchall()
            documents += [{'kind': 'turn', 'level': None, 'first': row['turn_number'], 'last': row['turn_number'],
                           'text': row['eligible_text'], 'refs': []} for row in turns]
            rows = db.execute(
                "SELECT d.kind,d.level,d.first,d.last,d.text,d.refs,"
                "ts_rank(to_tsvector('simple',d.text),plainto_tsquery('simple',%s)) AS rank "
                "FROM jsonb_to_recordset(%s::jsonb) AS d(kind text,level int,first bigint,last bigint,text text,refs jsonb) "
                "WHERE to_tsvector('simple',d.text) @@ plainto_tsquery('simple',%s) "
                "ORDER BY rank DESC,(d.kind='node') DESC,d.first,d.level DESC NULLS LAST LIMIT %s",
                (text, Jsonb(documents), text, limit)).fetchall()
        return [{'id': f"node:{row['level']}:{row['first']}-{row['last']}" if row['kind'] == 'node'
                 else f"turn:{row['first']}", 'kind': row['kind'], 'level': row['level'],
                 'turns': [row['first'], row['last']], 'excerpt': row['text'][:200], 'refs': row['refs'],
                 'rank': float(row['rank'])} for row in rows]
