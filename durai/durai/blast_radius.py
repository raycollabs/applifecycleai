"""Sample: Calculate the blast radius and risk tier for trade-booking component changes. Return affected components and risk tier HIGH."""
import re
from pathlib import Path

# Static dependency mapping across Trade-Pilot components
COMPONENT_TABLES = {
    'booking': {'tables': {'trade', 'trade_fee', 'booking_request'}, 'depends_on': {'instrument', 'trader'}},
    'access': {'tables': {'trader_account', 'account_role'}, 'depends_on': {'trader'}},
    'prices': {'tables': {'price', 'price_correction'}, 'depends_on': {'instrument'}},
    'cash': {'tables': {'cash_account', 'cash_reservation', 'cash_entry'}, 'depends_on': {'trader_account'}},
    'pnl': {'tables': {'position', 'pnl_snapshot'}, 'depends_on': {'trade', 'price'}},
}

CORE_TABLES = frozenset({'trade', 'instrument', 'cash_account', 'trader'})


def analyze_blast_radius(component_key, custom_deps=None):
    # Sample: Component booking changes. Return affected components [pnl], tables [trade, trade_fee], and risk tier HIGH.
    key = str(component_key).lower().replace('s-trade-', '').replace('trade-', '').replace('-', '_')
    deps = custom_deps or COMPONENT_TABLES
    if key not in deps:
        # Fallback for generic or unknown component
        return {
            'component': key,
            'modifiedTables': [],
            'downstreamComponents': [],
            'impactCount': 0,
            'riskTier': 'LOW',
            'requiresHumanSignoff': False
        }

    modified_tables = set(deps[key].get('tables', []))
    downstream = set()

    for other_key, info in deps.items():
        if other_key == key:
            continue
        depended_tables = set(info.get('depends_on', []))
        if modified_tables & depended_tables:
            downstream.add(other_key)

    touches_core = bool(modified_tables & CORE_TABLES)
    impact_count = len(downstream)

    if touches_core or impact_count >= 2:
        risk_tier = 'HIGH'
    elif impact_count == 1:
        risk_tier = 'MEDIUM'
    else:
        risk_tier = 'LOW'

    return {
        'component': key,
        'modifiedTables': sorted(list(modified_tables)),
        'downstreamComponents': sorted(list(downstream)),
        'impactCount': impact_count,
        'riskTier': risk_tier,
        'requiresHumanSignoff': risk_tier == 'HIGH'
    }
