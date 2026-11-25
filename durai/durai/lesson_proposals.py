"""Sample: Cluster failed run receipts, draft structured prompt lessons, and gate approval on human reviewers."""
import hashlib
import json
from pathlib import Path

from .plans_dir import human_reviewers

VALID_STATUSES = ('DRAFT', 'PROPOSED', 'APPROVED', 'LOADED')
HUMAN_REVIEWERS = human_reviewers()


def cluster_failures(receipts: list) -> dict:
    # Sample: Read list of run receipts. Group by stop reason such as STOP_STALL or STOP_BUDGET.
    clusters = {}
    for receipt in receipts:
        reason = receipt.get('stopReason') or 'UNKNOWN'
        if receipt.get('lastRunTests', {}).get('failed', 0) > 0:
            key = 'TEST_FAILURES'
        elif reason in ('STOP_STALL', 'STOP_BUDGET', 'STOP_CALLS'):
            key = reason
        elif any(receipt.get('toolErrors', {}).values()):
            key = 'TOOL_ERRORS'
        else:
            key = 'OTHER'

        clusters.setdefault(key, []).append(receipt)
    return clusters


def draft_proposal(cluster_name: str, runs: list, rule_text: str, enforcement: str) -> dict:
    # Sample: Given stall cluster with 2 runs, draft a lesson with PROPOSED status and stable id.
    if not runs:
        raise ValueError('Cannot draft lesson proposal from empty run cluster')
    if not rule_text or not rule_text.strip():
        raise ValueError('Lesson rule text cannot be empty')
    if not enforcement or not enforcement.strip():
        raise ValueError('Enforcement description cannot be empty')

    components = sorted(list({r.get('sampleId', 'UNKNOWN') for r in runs}))
    fingerprint = hashlib.sha256(f'{cluster_name}:{rule_text}:{",".join(components)}'.encode('utf-8')).hexdigest()[:8]
    lesson_id = f'L-PROP-{fingerprint.upper()}'

    evidence = (
        f"Cluster {cluster_name} observed across {len(runs)} run(s) on components {', '.join(components)}. "
        f"Sample stop reasons: {', '.join(sorted(list({str(r.get('stopReason')) for r in runs})))}."
    )

    return {
        'id': lesson_id,
        'cluster': cluster_name,
        'status': 'PROPOSED',
        'rule': rule_text.strip(),
        'evidence': evidence,
        'enforcement': enforcement.strip(),
        'affectedComponents': components,
        'runCount': len(runs),
        'reviewer': None,
        'approvalNote': None,
    }


def approve_proposal(proposal: dict, reviewer: str, note: str = 'Approved for prompt enhancement') -> dict:
    # Sample: Approve proposal as operator. Return proposal with status APPROVED. Reject agent names.
    if proposal.get('status') != 'PROPOSED':
        raise ValueError(f"Only PROPOSED lessons can be approved (current: {proposal.get('status')})")
    if not reviewer or reviewer.lower() not in HUMAN_REVIEWERS:
        raise PermissionError(f"Reviewer '{reviewer}' is not an authorized human reviewer. Agent auto-approval forbidden.")

    updated = dict(proposal)
    updated['status'] = 'APPROVED'
    updated['reviewer'] = reviewer
    updated['approvalNote'] = note
    return updated
