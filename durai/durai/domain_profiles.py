"""Sample: Validate 2 reserved seats. Return seat units without a lot conversion."""
from copy import deepcopy
from .common import Conflict

PROFILES = ('trading-position', 'transaction', 'finance', 'retail-order', 'reservation')
LEGACY_SPEC = {'applicationName': 'Exchange positions', 'quantityUnit': 'shares',
               'signedLots': True, 'correctionDirection': 'REMOVE_LOTS'}


def profile_id(specification):
    # Sample: Read the historical signed-lot specification. Select trading-position.
    if not isinstance(specification, dict):
        raise Conflict('NEEDS_CLARIFICATION: domain specification')
    selected = specification.get('profile')
    if selected is None and ('signedLots' in specification or 'correctionDirection' in specification):
        selected = 'trading-position'
    if selected not in PROFILES:
        raise Conflict('NEEDS_CLARIFICATION: explicit supported domain profile')
    return selected


def string_list(value):
    # Sample: Check states HELD and CONFIRMED. Refuse an empty or repeated state.
    return (isinstance(value, list) and bool(value) and
            all(isinstance(item, str) and bool(item.strip()) for item in value) and len(set(value)) == len(value))


def validate_domain(specification):
    # Sample: Declare seats, hold expiry 600, and one confirmation transition. Return those explicit rules.
    selected = profile_id(specification)
    required = {'applicationName', 'quantityUnit'}
    required |= ({'signedLots', 'correctionDirection'} if selected == 'trading-position' else
                 {'identityFields', 'invariants', 'lifecycle'})
    required |= {'finance': {'money'}, 'retail-order': {'inventoryPolicy'},
                 'reservation': {'holdTtlSeconds', 'capacityPolicy'}}.get(selected, set())
    missing = sorted(required - specification.keys())
    if missing:
        raise Conflict('NEEDS_CLARIFICATION: ' + ', '.join(missing))
    if any(not isinstance(specification[k], str) or not specification[k].strip() for k in ('applicationName', 'quantityUnit')):
        raise Conflict('NEEDS_CLARIFICATION: applicationName and quantityUnit')
    if selected == 'trading-position':
        if specification['quantityUnit'] != 'shares' or specification['signedLots'] is not True or specification['correctionDirection'] != 'REMOVE_LOTS':
            raise Conflict('NEEDS_CLARIFICATION: this qualified adapter requires signed lots, shares, and REMOVE_LOTS')
        return {'quantity': 'lots * lot_size', 'correction': 'current_shares - delta_lots * lot_size',
                'scope': 'synthetic position application; no order routing'}
    if not string_list(specification['identityFields']) or not string_list(specification['invariants']):
        raise Conflict('NEEDS_CLARIFICATION: identityFields and invariants')
    lifecycle = specification['lifecycle']
    if not isinstance(lifecycle, dict) or not {'states', 'initialState', 'terminalStates', 'transitions'} <= lifecycle.keys():
        raise Conflict('NEEDS_CLARIFICATION: lifecycle')
    states = lifecycle['states']
    if (not string_list(states) or not string_list(lifecycle['terminalStates']) or
            lifecycle['initialState'] not in states or not set(lifecycle['terminalStates']) <= set(states) or
            not isinstance(lifecycle['transitions'], list) or not lifecycle['transitions']):
        raise Conflict('NEEDS_CLARIFICATION: lifecycle states and transitions')
    for transition in lifecycle['transitions']:
        if (not isinstance(transition, dict) or transition.get('from') not in states or transition.get('to') not in states or
                transition['from'] in lifecycle['terminalStates']):
            raise Conflict('NEEDS_CLARIFICATION: lifecycle transition')
    if selected == 'finance':
        money = specification['money']
        if (not isinstance(money, dict) or not {'currency', 'representation', 'scale'} <= money.keys() or
                not isinstance(money['currency'], str) or len(money['currency']) != 3 or not money['currency'].isupper() or
                money['representation'] not in ('MINOR_UNITS', 'DECIMAL') or type(money['scale']) is not int or not 0 <= money['scale'] <= 18):
            raise Conflict('NEEDS_CLARIFICATION: money currency, representation, and scale')
    if selected == 'retail-order' and (not isinstance(specification['inventoryPolicy'], str) or not specification['inventoryPolicy'].strip()):
        raise Conflict('NEEDS_CLARIFICATION: inventoryPolicy')
    if selected == 'reservation' and (type(specification['holdTtlSeconds']) is not int or specification['holdTtlSeconds'] <= 0 or
            not isinstance(specification['capacityPolicy'], str) or not specification['capacityPolicy'].strip()):
        raise Conflict('NEEDS_CLARIFICATION: holdTtlSeconds and capacityPolicy')
    return {'profile': selected, 'quantityUnit': specification['quantityUnit'],
            **{k: deepcopy(specification[k]) for k in required - {'applicationName', 'quantityUnit'}}}
