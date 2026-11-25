"""Sample: Bind the trading catalog. Deliver its applicable rules to coding, review, or support."""
from copy import deepcopy
from .common import Conflict, digest
from .db import transaction
from .domain_profiles import LEGACY_SPEC, profile_id, validate_domain
from .ontology_patterns import AUDIENCES, patterns
from .packs import validate_pack
from .policy_catalog import extract_policies, policy_digest

TOOLS = {'coding-worker': ['read_file', 'edit_file', 'write_file'],
         'code-reviewer': ['read_policy', 'read_evidence', 'propose_finding'],
         'support-reviewer': ['read_policy', 'read_evidence', 'propose_action']}


def agent_view(binding_id, audience, database='durai_local', specification=None, role='durai_runtime', revision=None, require_projection=False):
    # Sample: Select support guidance for the trading profile. Keep source memory and refund tools out.
    if audience not in AUDIENCES:
        raise Conflict('Unsupported agent audience')
    specification = deepcopy(LEGACY_SPEC if specification is None else specification)
    validate_domain(specification)
    profile = profile_id(specification)
    chosen = {}
    with transaction(role, database) as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        binding = db.execute('SELECT * FROM durai_ontology.binding WHERE id=%s AND (%s::bigint IS NULL OR revision=%s) ORDER BY revision DESC LIMIT 1',
            (binding_id, revision, revision)).fetchone()
        if not binding:
            raise KeyError('Binding unavailable in this scope')
        if digest(binding['document']) != binding['digest']:
            raise Conflict('Binding digest differs')
        for reference in binding['document']['packs']:
            stored = db.execute('SELECT document,digest FROM durai_ontology.pack WHERE scope=%s AND id=%s AND version=%s',
                (reference['scope'], reference['id'], reference['version'])).fetchone()
            if not stored or stored['digest'] != reference['digest'] or validate_pack(stored['document']) != reference['digest']:
                raise Conflict('Pinned pack is unavailable or changed')
            pack = stored['document']
            legacy = extract_policies(reference['scope'], pack)
            if require_projection:
                rows = db.execute('SELECT policy_id,rule,audience,digest FROM durai_ontology.policy WHERE scope=%s AND pack_id=%s AND version=%s ORDER BY policy_id',
                    (reference['scope'], reference['id'], reference['version'])).fetchall()
                if [(r['policy_id'], r['rule'], r['audience'], r['digest']) for r in rows] != [(r['policyId'], r['rule'], r['audience'], r['digest']) for r in legacy]:
                    raise Conflict('Stored policies differ from the pinned pack projection')
            typed = {p['id']: p for p in patterns(pack)}
            entries = [r for r in legacy if r['policyId'] not in typed]
            for policy in typed.values():
                if profile not in policy['applicability']['profiles'] and '*' not in policy['applicability']['profiles']:
                    continue
                entries.append({'scope': reference['scope'], 'packId': pack['id'], 'packVersion': pack['version'],
                    'policyId': policy['id'], 'rule': policy['rule'], 'audience': policy['audience'],
                    'digest': policy_digest(policy), 'pattern': policy})
            for entry in entries:
                eligible = audience in entry['audience'] or (audience == 'code-reviewer' and 'pattern' not in entry and
                    (not entry['audience'] or 'coding-worker' in entry['audience']))
                if not eligible:
                    continue
                old = chosen.get(entry['policyId'])
                if old and (old['digest'] != entry['digest'] or old['rule'] != entry['rule']):
                    raise Conflict('Conflicting applicable policy definitions')
                chosen.setdefault(entry['policyId'], deepcopy(entry))
    selected = [chosen[key] for key in sorted(chosen)]
    return {'bindingId': binding_id, 'bindingRevision': binding['revision'], 'bindingDigest': binding['digest'],
        'profile': profile, 'audience': audience, 'policies': selected, 'digest': policy_digest(selected),
        'allowedTools': list(TOOLS[audience]), 'releaseAuthority': False, 'dataOnly': True}


def worker_view(binding_id, audience='coding-worker', database='durai_local', role='durai_runtime', specification=None):
    # Sample: Read a generic-only binding. Keep its legacy worker view and digest unchanged.
    view = agent_view(binding_id, audience, database, specification, role, require_projection=True)
    return {k: view[k] for k in ('bindingId', 'audience', 'policies', 'digest')}


def support_view(binding_id, database='durai_local', specification=None, role='durai_runtime', run_id=None):
    # Sample: Review run R-1. Return evidence digests and stages without its private document values.
    view = agent_view(binding_id, 'support-reviewer', database, specification, role)
    view['evidence'] = []
    if run_id is not None:
        with transaction(role, database) as db:
            db.execute('SET TRANSACTION READ ONLY')
            run = db.execute('SELECT document FROM durai_engineering.run WHERE id=%s', (run_id,)).fetchone()
            if not run or run['document']['bindingId'] != binding_id or run['document']['bindingRevision'] != view['bindingRevision']:
                raise KeyError('Run unavailable for this scoped binding')
            view['evidence'] = db.execute('SELECT sequence,stage,digest FROM durai_engineering.evidence WHERE run_id=%s ORDER BY sequence', (run_id,)).fetchall()
    return view
