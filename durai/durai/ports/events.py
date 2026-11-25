"""Sample: Publish 3 events with key P-1. Consume. Get the 3 events in publish order. Ack 1. Consume. Get 2."""
from typing import Protocol


class EventPort(Protocol):
    def publish(self, topic: str, key: str, payload: dict) -> str:
        # Sample: Publish to topic T with key P-1 and payload {"n": 1}. Return the event id.
        ...

    def consume(self, topic: str, group: str, max_events: int = 10) -> list[dict]:
        # Sample: Consume topic T for group G with max 2. Return up to 2 events not yet acked by G, oldest first.
        ...

    def ack(self, group: str, event_id: str) -> None:
        # Sample: Ack event E-1 for group G. Consume again. E-1 is not returned for G. Ack E-99. Raise NotFound.
        ...
