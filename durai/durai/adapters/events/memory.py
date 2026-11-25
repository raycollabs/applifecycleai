"""Sample: Make MemoryEventBus. Publish 3 events. Consume. Get 3 in publish order. Ack one. Consume. Get 2."""
from ...ports.errors import NotFound


class MemoryEventBus:
    def __init__(self):
        # Sample: Start with no event and no ack.
        self.events = []
        self.acked = set()

    def publish(self, topic, key, payload):
        # Sample: Publish to topic T with key P-1. Return E-1. Publish again. Return E-2.
        event_id = 'E-%d' % (len(self.events) + 1)
        self.events.append({'id': event_id, 'topic': topic, 'key': key, 'payload': dict(payload)})
        return event_id

    def consume(self, topic, group, max_events=10):
        # Sample: Consume topic T for group G. Return events of T that G has not acked, oldest first, at most max_events.
        pending = [dict(e) for e in self.events if e['topic'] == topic and (group, e['id']) not in self.acked]
        return pending[:max_events]

    def ack(self, group, event_id):
        # Sample: Ack E-1 for group G. Consume. E-1 is gone for G and still there for group H. Ack E-99. Raise NotFound.
        if not any(e['id'] == event_id for e in self.events):
            raise NotFound(event_id)
        self.acked.add((group, event_id))
