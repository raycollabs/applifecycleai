"""Sample: Read OPEN tickets from RLStore, cluster by category and fingerprint, draft PROPOSED policy_candidate rows, and mark tickets CANDIDATE_LINKED."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from durai.lesson_proposals import cluster_failures, draft_proposal  # noqa: E402
from durai.policy_eval import evaluate_candidate, promote_candidate  # noqa: E402
from durai.rl_store import InMemoryRLStore, RLStore  # noqa: E402

SEVERITY_RANK = {'CRITICAL': 4, 'HIGH': 3, 'MEDIUM': 2, 'LOW': 1}

CATEGORY_TO_TARGET_KIND = {
    'WEB_RULE_GAP': 'WEB_STATIC_RULE',
    'UI_UX_DEFECT': 'GUIDANCE_TOPIC',
    'SQL_BLUEPRINT_GAP': 'SHELL_TEMPLATE',
    'ROUTING': 'MODEL_ROUTE',
    'TOOL_FRICTION': 'LESSON_RULE',
    'PROMPT_AMBIGUITY': 'LESSON_RULE',
}


def _ticket_to_receipt(ticket: Mapping[str, Any]) -> dict:
    # Sample: Convert an RLStore ticket into a lesson_proposals receipt dict with sampleId and stopReason.
    payload = ticket.get('sanitized_payload') or {}
    if not isinstance(payload, Mapping):
        payload = {}
    sample_id = payload.get('sampleId') or payload.get('sample_id') or payload.get('component_category') or 'UNKNOWN'
    stop_reason = payload.get('stopReason') or payload.get('stop_reason') or ticket.get('category') or 'UNKNOWN'
    last_tests = payload.get('lastRunTests') or payload.get('last_run_tests') or {}
    tool_errors = payload.get('toolErrors') or payload.get('tool_errors') or {}
    if not tool_errors and payload.get('signal_kinds'):
        tool_errors = {str(kind): 1 for kind in payload.get('signal_kinds', [])}
    return {
        'ticketId': ticket.get('ticket_id'),
        'fingerprint': ticket.get('fingerprint'),
        'category': ticket.get('category', 'TOOL_FRICTION'),
        'sampleId': sample_id,
        'stopReason': stop_reason,
        'lastRunTests': last_tests if isinstance(last_tests, Mapping) else {},
        'toolErrors': tool_errors if isinstance(tool_errors, Mapping) else {},
        'ruleId': payload.get('rule_id') or payload.get('ruleId'),
    }


def _rule_and_target_for_cluster(
    category: str,
    cluster_key: str,
    tickets: Sequence[Mapping[str, Any]],
) -> tuple[str, str, str]:
    # Sample: Map category WEB_RULE_GAP and rule GET_CHANGES_COOKIE to rule text, enforcement text, and target_ref.
    first_payload = tickets[0].get('sanitized_payload') or {}
    rule_id = first_payload.get('rule_id') or first_payload.get('ruleId') or ''
    summary = str(tickets[0].get('summary') or cluster_key)

    if 'COOKIE' in str(rule_id).upper() or 'COOKIE' in summary.upper():
        rule_text = (
            'DO NOT set or delete cookies inside Next.js GET route handlers; '
            'use a Server Action or POST form so prefetching cannot change session state.'
        )
        enforcement = 'Static check via durai/build_web_rules.py (GET_CHANGES_COOKIE) and guidance topic update.'
        target_ref = f"durai/build_web_rules.py#{rule_id or 'GET_CHANGES_COOKIE'}"
        return rule_text, enforcement, target_ref

    if category == 'WEB_RULE_GAP':
        rule_text = f'DO NOT violate web static rule {rule_id or summary}; follow Next.js server/client boundary guards.'
        enforcement = f"Static check via durai/build_web_rules.py ({rule_id or cluster_key})."
        target_ref = f"durai/build_web_rules.py#{rule_id or cluster_key}"
        return rule_text, enforcement, target_ref

    if category == 'UI_UX_DEFECT':
        rule_text = (
            f'DO NOT throw unhandled 4xx domain errors for {summary}; '
            'return structured validation state via useActionState inline alerts.'
        )
        enforcement = 'Review UI error handling via durai/build_web_rules.py and product review gate.'
        target_ref = f"guidance/ui-ux/{(rule_id or cluster_key).lower()}"
        return rule_text, enforcement, target_ref

    if category == 'SQL_BLUEPRINT_GAP':
        rule_text = (
            f'DO NOT interpolate raw SQL strings ({summary}); '
            'use parameterized queries aligned with the relational blueprint.'
        )
        enforcement = 'Static guard via durai/smell_scorer.py (RAW_SQL_FORMAT) and SQL blueprint check.'
        target_ref = f"templates/sql-blueprint/{(rule_id or cluster_key).lower()}"
        return rule_text, enforcement, target_ref

    if category == 'ROUTING':
        rule_text = (
            f'DO NOT route low-finish categories ({summary}) to underperforming model profiles; '
            'select route from model_route_stats above finish threshold.'
        )
        enforcement = 'Contextual route threshold check in durai/model_router.py.'
        target_ref = 'durai/model_router.py'
        return rule_text, enforcement, target_ref

    if cluster_key == 'STOP_BUDGET':
        rule_text = 'DO NOT re-read unchanged workspace files; keep the pinned workspace manifest before editing.'
        enforcement = 'Build driver manifest guard and call-budget telemetry.'
    elif cluster_key == 'STOP_STALL':
        rule_text = 'DO NOT repeat stalled tool calls without inspecting symbol exports and exact line ranges first.'
        enforcement = 'Stall nudge guard in durai/build_driver.py and pre-edit read rule.'
    elif 'EDIT_MISMATCH' in str(rule_id).upper() or 'EDIT_MISMATCH' in summary.upper():
        rule_text = 'DO NOT guess edit_file targets; read exact file lines and symbol signatures before patching.'
        enforcement = 'Edit mismatch lesson rule in durai/build_tools.py.'
    else:
        rule_text = f'DO NOT repeat failure pattern ({summary}); verify target preconditions and executed checks first.'
        enforcement = f'Lesson rule and static check for {category}.'

    target_ref = f"lessons/{category.lower()}/{(rule_id or cluster_key).lower()}"
    return rule_text, enforcement, target_ref


def _mark_tickets_linked(store: RLStore, tickets: Sequence[Mapping[str, Any]]) -> None:
    # Sample: Update each triaged ticket status from OPEN to CANDIDATE_LINKED on store.
    now = datetime.now(timezone.utc).isoformat()
    in_memory_tickets = getattr(store, '_tickets', None)
    for ticket in tickets:
        if hasattr(store, 'update_ticket_status'):
            store.update_ticket_status(ticket['ticket_id'], 'CANDIDATE_LINKED')
        elif isinstance(in_memory_tickets, dict) and ticket.get('fingerprint') in in_memory_tickets:
            row = in_memory_tickets[ticket['fingerprint']]
            row['status'] = 'CANDIDATE_LINKED'
            row['last_seen_at'] = now


def triage_open_tickets(store: RLStore, proposed_by: str = 'durai-triage-agent') -> List[dict]:
    # Sample: Read OPEN tickets from store, cluster by category and failure key, create PROPOSED candidates, and update ticket status.
    open_tickets = list(store.list_tickets(status='OPEN'))
    if not open_tickets:
        return []

    open_tickets.sort(
        key=lambda t: (
            int(t.get('occurrence_count', 1)),
            SEVERITY_RANK.get(str(t.get('severity', 'LOW')).upper(), 0),
        ),
        reverse=True,
    )

    by_category: Dict[str, List[dict]] = {}
    for ticket in open_tickets:
        cat = str(ticket.get('category') or 'TOOL_FRICTION')
        by_category.setdefault(cat, []).append(ticket)

    existing_ids = {c['candidate_id'] for c in store.list_candidates()}
    created_candidates: List[dict] = []

    for category, cat_tickets in by_category.items():
        receipts = [_ticket_to_receipt(t) for t in cat_tickets]
        receipt_clusters = cluster_failures(receipts)
        receipt_to_ticket = {id(rec): t for rec, t in zip(receipts, cat_tickets)}

        grouped: Dict[str, List[dict]] = {}
        for cluster_name, cluster_receipts in receipt_clusters.items():
            for rec in cluster_receipts:
                ticket = receipt_to_ticket[id(rec)]
                sub_key = cluster_name
                if cluster_name == 'OTHER':
                    sub_key = str(rec.get('ruleId') or rec.get('fingerprint') or 'OTHER')
                elif rec.get('ruleId'):
                    sub_key = f"{cluster_name}:{rec['ruleId']}"
                grouped.setdefault(sub_key, []).append(ticket)

        for sub_key, cluster_tickets in grouped.items():
            cluster_receipts = [_ticket_to_receipt(t) for t in cluster_tickets]
            rule_text, enforcement, target_ref = _rule_and_target_for_cluster(category, sub_key, cluster_tickets)
            proposal = draft_proposal(f'{category}:{sub_key}', cluster_receipts, rule_text, enforcement)

            fingerprints = sorted({str(t['fingerprint']) for t in cluster_tickets})
            if len(fingerprints) == 1:
                cluster_fp = fingerprints[0]
            else:
                cluster_fp = hashlib.sha256('|'.join(fingerprints).encode('utf-8')).hexdigest()[:16]

            candidate_id = proposal['id']
            if candidate_id in existing_ids:
                candidate_id = f"{candidate_id}-{cluster_fp[:6].upper()}"
            existing_ids.add(candidate_id)

            occurrence_total = sum(int(t.get('occurrence_count', 1)) for t in cluster_tickets)
            proposed_patch = (
                f"RULE: {proposal['rule']}\n"
                f"ENFORCEMENT: {proposal['enforcement']}\n"
                f"EVIDENCE: {proposal['evidence']} Total occurrences: {occurrence_total}."
            )

            candidate_row = store.create_candidate({
                'candidate_id': candidate_id,
                'cluster_fingerprint': cluster_fp,
                'target_kind': CATEGORY_TO_TARGET_KIND.get(category, 'LESSON_RULE'),
                'target_ref': target_ref,
                'proposed_patch': proposed_patch,
                'status': 'PROPOSED',
                'proposed_by': proposed_by,
                'ticket_ids': [t.get('ticket_id') for t in cluster_tickets],
                'occurrence_total': occurrence_total,
                'proposal': proposal,
            })
            _mark_tickets_linked(store, cluster_tickets)
            created_candidates.append(candidate_row)

    return created_candidates


def _load_json(path: Optional[str]) -> Any:
    # Sample: Read JSON file at path and return parsed data. Return None when path is omitted.
    if not path:
        return None
    return json.loads(Path(path).read_text(encoding='utf-8'))


def main(argv: Optional[Sequence[str]] = None) -> int:
    # Sample: Load tickets JSON, triage OPEN tickets into PROPOSED candidates, optionally evaluate or promote, and print JSON summary.
    parser = argparse.ArgumentParser()
    parser.add_argument('--tickets-json', help='Path to JSON file containing ticket list or {"tickets": [...]}')
    parser.add_argument('--proposed-by', default='durai-triage-agent')
    parser.add_argument('--baseline-json', help='Optional baseline episode JSON path for evaluation')
    parser.add_argument('--treatment-json', help='Optional treatment episode JSON path for evaluation')
    parser.add_argument('--replay-trace', help='Optional worker trace JSON path for dry-run replay')
    parser.add_argument('--approve-as', help='Optional human reviewer name to promote created candidates')
    args = parser.parse_args(argv)

    store = InMemoryRLStore()
    raw_tickets = _load_json(args.tickets_json)
    if isinstance(raw_tickets, dict):
        raw_tickets = raw_tickets.get('tickets', [])
    if isinstance(raw_tickets, list):
        for ticket in raw_tickets:
            store.upsert_ticket(ticket)

    candidates = triage_open_tickets(store, proposed_by=args.proposed_by)

    evaluations = []
    baseline = _load_json(args.baseline_json)
    treatment = _load_json(args.treatment_json)
    if baseline and treatment:
        for cand in candidates:
            evaluations.append(
                evaluate_candidate(
                    store,
                    cand['candidate_id'],
                    baseline,
                    treatment,
                    replay_trace_path=args.replay_trace,
                )
            )

    promoted = []
    if args.approve_as:
        try:
            for cand in candidates:
                promoted.append(promote_candidate(store, cand['candidate_id'], args.approve_as))
        except PermissionError as err:
            print(json.dumps({'error': str(err)}, indent=2), file=sys.stderr)
            return 1

    output = {
        'proposedBy': args.proposed_by,
        'candidatesCreated': len(candidates),
        'candidates': candidates,
        'evaluations': evaluations,
        'promoted': promoted,
    }
    print(json.dumps(output, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
