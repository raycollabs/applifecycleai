"""Sample: Retain a MySQL customer constraint. Record an evidence-backed proposal without approval authority."""
import re
from .common import Conflict, digest
from .domain_profiles import PROFILES
from .ontology_patterns import patterns
from .packs import validate_pack


def verify_choice_evidence(store, plan_id, references):
    # Sample: Read plan P-1 evidence E-1. Refuse a different digest or an unavailable artifact.
    if not isinstance(references, list) or not references:
        raise Conflict('An architecture exception requires scoped evidence')
    for reference in references:
        if (not isinstance(reference, dict) or set(reference) != {'kind', 'id', 'digest'} or
                not isinstance(reference['digest'], str) or not re.fullmatch(r'[a-f0-9]{64}', reference['digest'])):
            raise Conflict('Architecture evidence reference shape differs')
        if reference['kind'] == 'record':
            row = store.read(plan_id, reference['id'])
            if row['kind'] == 'status' or row['digest'] != reference['digest']:
                raise Conflict('Architecture evidence is mutable or its digest differs')
        elif reference['kind'] in ('receipt', 'verdict', 'commit'):
            store.resolve_artifact(reference)
        else:
            raise Conflict('Architecture evidence requires an immutable scoped record or verified artifact')


def record_architecture_proposal(store, plan_id, pack, profile, constraints, request_key):
    # Sample: Select PostgreSQL by default. Supply a MySQL constraint and evidence. Store EXCEPTION_PROPOSED.
    if profile not in PROFILES or not isinstance(constraints, dict):
        raise Conflict('Architecture proposal needs a supported profile and constraints')
    all_patterns = {p['id']: p for p in patterns(pack)}
    selected = {p['id']: p for p in all_patterns.values() if 'preference' in p and
                profile in p['applicability']['profiles']}
    if not selected or set(constraints) - selected.keys():
        raise Conflict('Architecture proposal has no defaults or an unknown pattern')
    for p in selected.values():
        required = all_patterns.get(p['relatedPolicyId'])
        if not required or required['strength'] != 'REQUIRED' or profile not in required['applicability']['profiles']:
            raise Conflict('Architecture preference has no applicable required correctness policy')
    choices, evidence = [], []
    with store.atomic():
        store.read(plan_id)
        for identity, pattern in sorted(selected.items()):
            preference = pattern['preference']
            choice = {'policyId': identity, 'policyDigest': digest(pattern),
                'selected': preference['preferred'], 'state': 'DEFAULT_PROPOSED',
                'when': preference['when'], 'notApplicable': preference['notApplicable'],
                'correctnessPolicyId': pattern['relatedPolicyId']}
            if identity in constraints:
                supplied = constraints[identity]
                if not isinstance(supplied, dict) or set(supplied) != {'selected', 'reason', 'evidence'}:
                    raise Conflict('Customer constraint shape differs')
                allowed = {a['id']: a['when'] for a in preference['alternatives']}
                if not isinstance(supplied['selected'], str) or supplied['selected'] not in allowed or not isinstance(supplied['reason'], str) or not supplied['reason'].strip():
                    raise Conflict('Customer constraint needs a permitted alternative and reason')
                verify_choice_evidence(store, plan_id, supplied['evidence'])
                evidence.extend(supplied['evidence'])
                choice.update(selected=supplied['selected'], state='EXCEPTION_PROPOSED', reason=supplied['reason'],
                    useCondition=allowed[supplied['selected']], evidence=supplied['evidence'],
                    requiredEvidence=preference['exceptionEvidence'])
            choices.append(choice)
        value = {'decision': 'Propose architecture choices for ' + profile + '.', 'alternatives': choices,
            'authority': 'PROPOSED', 'evidence': evidence, 'profile': profile,
            'sourcePackId': pack['id'], 'sourcePackVersion': pack['version'], 'sourcePackDigest': validate_pack(pack),
            'proposalFormat': 'durai-architecture-proposal/v1', 'executionAuthority': 'NONE'}
        return store.append(plan_id, 'decision', value, request_key)
