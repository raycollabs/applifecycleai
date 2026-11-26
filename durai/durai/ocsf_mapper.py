"""Sample: Map an audit row to an OCSF API Activity event. Count OpenShell JSONL events and keep only a digest reference."""
import json
from datetime import datetime

from .common import digest

CLASS_UID = 6003
CATEGORY_UID = 6
ACTIVITY_OTHER = 99
OCSF_VERSION = '1.3.0'
PRODUCT = {'name': 'Durai', 'vendor_name': 'Durai'}


def millis(value):
    # Sample: A datetime 2026-11-26T12:00:00Z returns 1795694400000. An int is returned unchanged.
    return int(value.timestamp() * 1000) if isinstance(value, datetime) else int(value)


def event(uid, name, actor, time, payload):
    # Sample: Build class 6003 with activity Other, the action name, the actor, time in milliseconds, a metadata uid and the payload under unmapped.
    return {'class_uid': CLASS_UID, 'class_name': 'API Activity', 'category_uid': CATEGORY_UID, 'category_name': 'Application Activity',
            'activity_id': ACTIVITY_OTHER, 'activity_name': name, 'type_uid': CLASS_UID * 100 + ACTIVITY_OTHER,
            'severity_id': 1, 'status_id': 1, 'time': millis(time), 'actor': {'user': {'name': actor}},
            'api': {'operation': name}, 'metadata': {'version': OCSF_VERSION, 'product': PRODUCT, 'uid': uid}, 'unmapped': payload}


def from_audit_row(row):
    # Sample: Row id 7 by durai_reviewer with action APPROVE maps to uid audit:7, actor durai_reviewer, operation APPROVE.
    return event('audit:' + str(row['id']), row['action'], row['actor'], row['created_at'], row['payload'])


def from_event_row(row):
    # Sample: Event E-1 of run W-1 with sequence 3 and kind GATE_OPENED maps to uid event:E-1 with the run and sequence under unmapped.
    payload = dict(row['payload'], runId=row['run_id'], sequence=row['sequence'])
    return event('event:' + str(row['id']), row['kind'], 'durai_runtime', row['created_at'], payload)


def to_jsonl(events):
    # Sample: Two events give two lines of JSON with sorted keys, each line ends with a newline.
    return ''.join(json.dumps(item, sort_keys=True, separators=(',', ':')) + '\n' for item in events)


def reference_from_jsonl(data, source):
    # Sample: Read 3 lines of OpenShell JSONL with one DENIED. Return source, digest of the bytes, count 3, denied 1, and no log body.
    count = denied = 0
    classes = {}
    for line in data.splitlines():
        if not line.strip():
            continue
        count += 1
        try:
            item = json.loads(line)
        except ValueError:
            classes['unparsed'] = classes.get('unparsed', 0) + 1
            continue
        key = str(item.get('class_uid', 'unknown'))
        classes[key] = classes.get(key, 0) + 1
        text = json.dumps(item)
        if '"DENIED"' in text or item.get('disposition_id') == 2 or item.get('action_id') == 2:
            denied += 1
    return {'kind': 'openshell-ocsf-jsonl', 'source': source, 'digest': digest(data), 'events': count, 'denied': denied, 'classes': classes}
