"""Sample: Read record sandbox.egress from an effective view. Get the allow-list for tier balanced. The starter body allows only api.deepinfra.com:443."""
import copy
import re

from .common import digest
from .egress_policy import MODEL_HOST, EgressPolicyError, parse_allow

RECORD_ID = 'sandbox.egress'
CONTENT_KIND = 'SANDBOX_EGRESS'
TIERS = ('restricted', 'balanced', 'open')
PRESET_NAME = re.compile(r'^[a-z][a-z0-9-]{0,62}$')


def starter_body():
    # Sample: Return revision 1 with tier restricted empty, tier balanced holding model-host, and preset model-host for api.deepinfra.com port 443.
    return {'contentKind': CONTENT_KIND, 'revision': 1,
            'tiers': {'restricted': [], 'balanced': ['model-host']},
            'presets': {'model-host': {'host': MODEL_HOST['host'], 'port': MODEL_HOST['port']}}}


def validate_body(body):
    # Sample: Starter body passes. A tier that names a missing preset, an unknown tier, a bad preset name or an unsafe host raises EgressPolicyError.
    if not isinstance(body, dict) or body.get('contentKind') != CONTENT_KIND:
        raise EgressPolicyError('Body is not a sandbox egress record')
    if not isinstance(body.get('revision'), int) or body['revision'] < 1:
        raise EgressPolicyError('Revision must be a whole number from 1')
    presets, tiers = body.get('presets'), body.get('tiers')
    if not isinstance(presets, dict) or not isinstance(tiers, dict):
        raise EgressPolicyError('Presets and tiers must be objects')
    for name, entry in presets.items():
        if not PRESET_NAME.match(name):
            raise EgressPolicyError('Preset name is invalid: ' + str(name))
        parse_allow(entry)
    for tier, names in tiers.items():
        if tier not in TIERS:
            raise EgressPolicyError('Tier is unknown: ' + str(tier))
        missing = [name for name in names if name not in presets]
        if missing:
            raise EgressPolicyError('Tier ' + tier + ' names missing presets: ' + ', '.join(missing))
    return body


def make_record(body):
    # Sample: Wrap a valid body as a pack record with id sandbox.egress, layer DESIGN, type KNOWLEDGE and the body digest.
    validate_body(body)
    return {'id': RECORD_ID, 'layer': 'DESIGN', 'type': 'KNOWLEDGE', 'body': body, 'digest': digest(body)}


def from_view(view):
    # Sample: A view with record sandbox.egress returns its checked body. A view without it returns the starter body, which is the strictest allowed baseline.
    record = view.get_record(RECORD_ID) if view is not None else None
    return validate_body(record['body'] if record else starter_body())


def allow_list(body, tier='restricted'):
    # Sample: Tier balanced of the starter body returns one parsed entry for api.deepinfra.com:443. Tier restricted returns []. An unlisted tier raises EgressPolicyError.
    if tier not in TIERS:
        raise EgressPolicyError('Tier is unknown: ' + str(tier))
    return [parse_allow(body['presets'][name]) for name in body['tiers'].get(tier, [])]


def with_rules(body, rules, tier='balanced'):
    # Sample: Add preset github-read to tier balanced. Return a new body with revision 2. The old body stays unchanged.
    new = copy.deepcopy(validate_body(body))
    for name, entry in rules:
        parse_allow(entry)
        new['presets'][name] = entry
        listed = new['tiers'].setdefault(tier, [])
        if name not in listed:
            listed.append(name)
    new['revision'] = body['revision'] + 1
    return validate_body(new)


def gate_question(entry):
    # Sample: Entry api.github.com:443 read-only for /usr/bin/curl returns a question that names host, port, access, protocol and binaries.
    entry = parse_allow(entry)
    return ('Allow ' + ', '.join(entry['binaries']) + ' to reach ' + entry['host'] + ':' + str(entry['port']) +
            ' with access ' + entry['access'] + ' over ' + entry['protocol'] + ' (' + entry['enforcement'] + ')?')
