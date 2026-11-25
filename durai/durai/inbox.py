"""Sample: Deliver E-1 twice to inventory. Keep one receipt and one inventory change."""
from uuid import UUID

from .common import Conflict
from .idempotency import execute_request
from .outbox import FORMAT, check_name

FIELDS = {'format', 'applicationId', 'eventId', 'streamId', 'sequence', 'eventType', 'payload'}


def consume(consumer, event, handler, database='durai_test', role='durai_runtime'):
    # Sample: Give E-1 to inventory. Retry E-1 and read the retained result.
    check_name(consumer, maximum=110)
    if not isinstance(event, dict) or set(event) != FIELDS or event['format'] != FORMAT:
        raise ValueError('Invalid event envelope')
    check_name(event['streamId'])
    check_name(event['eventType'])
    event_id = event['eventId']
    if (not isinstance(event_id, str) or str(UUID(event_id)) != event_id
            or type(event['sequence']) is not int or not 1 <= event['sequence'] < 2**63
            or not isinstance(event['applicationId'], str) or not isinstance(event['payload'], dict)
            or not callable(handler)):
        raise ValueError('Invalid event identity, body, or handler')

    def apply(db, body):
        # Sample: Receive a trading event under the foreign login. Refuse it before the handler runs.
        tenant = db.execute('SELECT durai_core.application_id() AS id').fetchone()['id']
        if tenant is None or tenant != body['applicationId']:
            raise Conflict('Event tenant does not match the consumer login')
        return handler(db, body)

    return execute_request('event.consume:' + consumer, event_id, event, apply, database, role)
