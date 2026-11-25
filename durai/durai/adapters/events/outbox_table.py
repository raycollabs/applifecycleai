"""Sample: Make OutboxEventBus with a connect function. Publish 3 events. Consume. Get the same answer as MemoryEventBus."""
import json

from ...ports.errors import NotFound

SCHEMA = (
    'CREATE TABLE IF NOT EXISTS outbox_event (id bigserial PRIMARY KEY, topic text NOT NULL, key text NOT NULL, payload jsonb NOT NULL)',
    'CREATE TABLE IF NOT EXISTS outbox_ack (group_name text NOT NULL, event_id bigint NOT NULL, PRIMARY KEY (group_name, event_id))',
)


class OutboxEventBus:
    def __init__(self, connect):
        # Sample: Hold a function that returns a new psycopg connection.
        self.connect = connect

    def ensure_schema(self):
        # Sample: Create both tables. Run it twice. Check no error.
        with self.connect() as conn:
            for statement in SCHEMA:
                conn.execute(statement)

    def publish(self, topic, key, payload):
        # Sample: Publish to topic T with key P-1. Return E-1. Publish again. Return E-2.
        with self.connect() as conn:
            row = conn.execute('INSERT INTO outbox_event (topic, key, payload) VALUES (%s, %s, %s) RETURNING id',
                               (topic, key, json.dumps(payload))).fetchone()
            return 'E-%d' % row[0]

    def consume(self, topic, group, max_events=10):
        # Sample: Consume topic T for group G. Return events of T that G has not acked, oldest first, at most max_events.
        with self.connect() as conn:
            rows = conn.execute('SELECT e.id, e.topic, e.key, e.payload FROM outbox_event e '
                                'WHERE e.topic = %s AND NOT EXISTS (SELECT 1 FROM outbox_ack a WHERE a.group_name = %s AND a.event_id = e.id) '
                                'ORDER BY e.id LIMIT %s', (topic, group, max_events)).fetchall()
            return [{'id': 'E-%d' % r[0], 'topic': r[1], 'key': r[2], 'payload': r[3] if isinstance(r[3], dict) else json.loads(r[3])} for r in rows]

    def ack(self, group, event_id):
        # Sample: Ack E-1 for group G. Consume. E-1 is gone for G and still there for group H. Ack E-99. Raise NotFound.
        number = int(str(event_id).split('-')[-1])
        with self.connect() as conn:
            if not conn.execute('SELECT 1 FROM outbox_event WHERE id = %s', (number,)).fetchone():
                raise NotFound(event_id)
            conn.execute('INSERT INTO outbox_ack (group_name, event_id) VALUES (%s, %s) ON CONFLICT DO NOTHING', (group, number))
