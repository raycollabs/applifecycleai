"""Sample: Evaluate candidate C-1 against baseline and treatment episodes, replay trace T-1, store policy_evaluation, and promote via human allowlist."""
import hashlib
from pathlib import Path
import sys
from typing import Any, Mapping, Optional, Union

from .build_web_rules import check_web_rules
from .plans_dir import human_reviewers
from .rl_store import RLStore
from .smell_scorer import analyze_code_smells, score_with_smell_guard

ROOT = Path(__file__).resolve().parents[1]


def _resolve_episode(store: RLStore, episode: Union[str, Mapping[str, Any]]) -> dict:
    # Sample: Pass episode id EP-1 or an episode dict. Return a plain episode dict or raise KeyError.
    if isinstance(episode, str):
        found = store.get_episode(episode)
        if not found:
            raise KeyError(f'Unknown episode {episode}')
        return found
    return dict(episode)


def _pass_rate(episode: Mapping[str, Any]) -> float:
    # Sample: Read passed_checks 4 and failed_checks 1. Return 0.8. Return 0.0 when total checks are 0.
    passed = int(episode.get('passed_checks', 0))
    failed = int(episode.get('failed_checks', 0))
    total = passed + failed
    if total <= 0:
        return 0.0
    return passed / total


def _run_replay_trace(replay_trace_path: Union[str, Path]) -> dict:
    # Sample: Replay trace at path P in dry-run mode. Return attemptId, totalSteps, uniqueTools, and matched.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from tools.replay_trace import replay_trace
    return replay_trace(str(replay_trace_path), dry_run=True)


def _decide(
    treatment_checks: int,
    delta_pass_rate: float,
    delta_calls: int,
    delta_tool_errors: int,
    delta_list_cost_usd: float,
    delta_smell_high: int,
    replay_result: Optional[dict],
    web_violations: Optional[list],
    smells_report: Optional[dict],
) -> str:
    # Sample: Treatment fixes checks with positive delta_pass_rate and fewer tool errors. Return PASS_READY.
    if treatment_checks <= 0:
        return 'INCONCLUSIVE'
    if replay_result is not None and not replay_result.get('matched', False):
        return 'REGRESSION'
    if web_violations:
        return 'REGRESSION'
    if smells_report is not None and score_with_smell_guard(4, True, smells_report) < 3:
        return 'REGRESSION'
    if delta_pass_rate < 0 or delta_smell_high > 0:
        return 'REGRESSION'
    if delta_pass_rate > 0:
        if delta_tool_errors > 0:
            return 'INCONCLUSIVE'
        return 'PASS_READY'
    if delta_tool_errors > 0:
        return 'REGRESSION'
    if delta_calls < 0 or delta_tool_errors < 0 or delta_list_cost_usd < 0:
        if delta_calls > 0 or delta_list_cost_usd > 0:
            return 'INCONCLUSIVE'
        return 'PASS_READY'
    if delta_calls > 0 or delta_list_cost_usd > 0:
        return 'REGRESSION'
    return 'INCONCLUSIVE'


def evaluate_candidate(
    store: RLStore,
    candidate_id: str,
    baseline_episode: Union[str, Mapping[str, Any]],
    treatment_episode: Union[str, Mapping[str, Any]],
    replay_trace_path: Optional[Union[str, Path]] = None,
    workspace_path: Optional[Union[str, Path]] = None,
    python_files: Optional[Mapping[str, str]] = None,
) -> dict:
    # Sample: Compare baseline EP-1 and treatment EP-2 for candidate C-1. Record evaluation EV-... with decision PASS_READY.
    baseline = _resolve_episode(store, baseline_episode)
    treatment = _resolve_episode(store, treatment_episode)

    treatment_checks = int(treatment.get('passed_checks', 0)) + int(treatment.get('failed_checks', 0))
    delta_pass_rate = round(_pass_rate(treatment) - _pass_rate(baseline), 4)
    delta_calls = int(treatment.get('calls_used', 0)) - int(baseline.get('calls_used', 0))
    delta_tool_errors = int(treatment.get('tool_error_count', 0)) - int(baseline.get('tool_error_count', 0))
    delta_list_cost_usd = round(
        float(treatment.get('list_cost_usd', 0.0)) - float(baseline.get('list_cost_usd', 0.0)),
        6,
    )
    delta_smell_high = int(treatment.get('smell_high', 0)) - int(baseline.get('smell_high', 0))

    replay_result = _run_replay_trace(replay_trace_path) if replay_trace_path is not None else None
    web_violations = check_web_rules(workspace_path) if workspace_path is not None else None
    smells_report = analyze_code_smells(dict(python_files)) if python_files is not None else None

    decision = _decide(
        treatment_checks=treatment_checks,
        delta_pass_rate=delta_pass_rate,
        delta_calls=delta_calls,
        delta_tool_errors=delta_tool_errors,
        delta_list_cost_usd=delta_list_cost_usd,
        delta_smell_high=delta_smell_high,
        replay_result=replay_result,
        web_violations=web_violations,
        smells_report=smells_report,
    )

    baseline_id = baseline.get('episode_id')
    treatment_id = treatment.get('episode_id')
    eval_seed = (
        f'{candidate_id}:{baseline_id or ""}:{treatment_id or ""}:'
        f'{delta_pass_rate}:{delta_calls}:{delta_tool_errors}:{delta_list_cost_usd}:{decision}'
    )
    eval_id = f"EV-{hashlib.sha256(eval_seed.encode('utf-8')).hexdigest()[:8].upper()}"

    evaluation: dict = {
        'eval_id': eval_id,
        'candidate_id': candidate_id,
        'baseline_episode_id': baseline_id,
        'treatment_episode_id': treatment_id,
        'delta_pass_rate': delta_pass_rate,
        'delta_calls': delta_calls,
        'delta_tool_errors': delta_tool_errors,
        'delta_list_cost_usd': delta_list_cost_usd,
        'decision': decision,
    }
    if replay_result is not None:
        evaluation['replay'] = replay_result
    if web_violations is not None:
        evaluation['web_violations'] = web_violations
    if smells_report is not None:
        evaluation['smells_report'] = smells_report

    return store.record_evaluation(evaluation)


def promote_candidate(
    store: RLStore,
    candidate_id: str,
    reviewer: str,
    require_pass_ready: bool = False,
) -> dict:
    # Sample: Promote candidate C-1 as operator. Raise PermissionError when reviewer is an agent or the proposer.
    candidate = next((c for c in store.list_candidates() if c.get('candidate_id') == candidate_id), None)
    if candidate is None:
        raise KeyError(f'Unknown candidate {candidate_id}')

    allowed = getattr(store, '_humans', None)
    if allowed is None:
        allowed = {name.lower() for name in human_reviewers()}

    normalized = reviewer.strip().lower() if isinstance(reviewer, str) else ''
    proposer = str(candidate.get('proposed_by', '')).strip().lower()
    if not normalized or normalized not in allowed or normalized == proposer:
        raise PermissionError(
            f'Candidate promotion requires an authorized human reviewer distinct from proposer: {reviewer}'
        )

    if require_pass_ready:
        if hasattr(store, 'list_evaluations'):
            matching = list(store.list_evaluations(candidate_id))
        else:
            evaluations = getattr(store, '_evaluations', {})
            matching = [ev for ev in evaluations.values() if ev.get('candidate_id') == candidate_id]
        if not matching or matching[-1].get('decision') != 'PASS_READY':
            raise ValueError(f'Candidate {candidate_id} does not have a PASS_READY evaluation')

    return store.approve_candidate(candidate_id, reviewer.strip())
