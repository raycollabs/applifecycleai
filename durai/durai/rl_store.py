"""Sample: Create InMemoryRLStore. Record verified episode EP-1, append signal, upsert ticket by fingerprint, and block agent self-approval."""
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence

from .plans_dir import human_reviewers


def _utc_iso() -> str:
    # Sample: Return current UTC timestamp in ISO-8601 format.
    return datetime.now(timezone.utc).isoformat()


class RLStore(Protocol):
    def record_episode(self, episode: Mapping[str, Any]) -> dict: ...
    def get_episode(self, episode_id: str) -> Optional[dict]: ...
    def append_signal(self, signal: Mapping[str, Any]) -> dict: ...
    def list_signals(self, episode_id: str) -> Sequence[dict]: ...
    def upsert_ticket(self, ticket: Mapping[str, Any]) -> dict: ...
    def list_tickets(self, status: Optional[str] = None) -> Sequence[dict]: ...
    def create_candidate(self, candidate: Mapping[str, Any]) -> dict: ...
    def approve_candidate(self, candidate_id: str, approver: str) -> dict: ...
    def list_candidates(self, status: Optional[str] = None) -> Sequence[dict]: ...
    def record_evaluation(self, evaluation: Mapping[str, Any]) -> dict: ...
    def list_evaluations(self, candidate_id: Optional[str] = None) -> Sequence[dict]: ...
    def upsert_route_stats(self, stats: Mapping[str, Any]) -> dict: ...
    def get_route_stats(self, component_category: str, shell_profile: str = 'py-fastapi-next') -> Sequence[dict]: ...


class InMemoryRLStore:
    def __init__(self, human_allowlist: Optional[Sequence[str]] = None):
        # Sample: Initialize empty in-memory tables for episodes, signals, tickets, candidates, evaluations, and route stats.
        self._episodes: Dict[str, dict] = {}
        self._signals: List[dict] = []
        self._tickets: Dict[str, dict] = {}
        self._candidates: Dict[str, dict] = {}
        self._evaluations: Dict[str, dict] = {}
        self._route_stats: Dict[tuple, dict] = {}
        self._humans = {h.lower() for h in (human_allowlist or human_reviewers())}

    def record_episode(self, episode: Mapping[str, Any]) -> dict:
        # Sample: Record episode EP-1. Raise ValueError when passed_checks + failed_checks == 0 and reward_score > 0.
        passed = int(episode.get('passed_checks', 0))
        failed = int(episode.get('failed_checks', 0))
        reward = float(episode.get('reward_score', 0.0))
        if (passed + failed) == 0 and reward > 0:
            raise ValueError('Unverified episode with 0 executed checks cannot have positive reward')
        row = dict(episode)
        row.setdefault('recorded_at', _utc_iso())
        self._episodes[row['episode_id']] = row
        return dict(row)

    def get_episode(self, episode_id: str) -> Optional[dict]:
        # Sample: Retrieve episode EP-1 by id. Return dict or None.
        row = self._episodes.get(episode_id)
        return dict(row) if row else None

    def append_signal(self, signal: Mapping[str, Any]) -> dict:
        # Sample: Append step signal EDIT_MISMATCH for episode EP-1.
        if signal['episode_id'] not in self._episodes:
            raise KeyError(f"Unknown episode {signal['episode_id']}")
        row = dict(signal)
        row['signal_id'] = len(self._signals) + 1
        row.setdefault('recorded_at', _utc_iso())
        self._signals.append(row)
        return dict(row)

    def list_signals(self, episode_id: str) -> Sequence[dict]:
        # Sample: Return ordered step signals for episode EP-1.
        return [dict(s) for s in self._signals if s['episode_id'] == episode_id]

    def upsert_ticket(self, ticket: Mapping[str, Any]) -> dict:
        # Sample: Upsert ticket with fingerprint F-1 twice. Check one ticket row with occurrence_count 2.
        fp = ticket['fingerprint']
        now = _utc_iso()
        if fp in self._tickets:
            existing = self._tickets[fp]
            existing['occurrence_count'] = int(existing['occurrence_count']) + 1
            existing['last_seen_at'] = now
            return dict(existing)
        row = dict(ticket)
        row.setdefault('occurrence_count', 1)
        row.setdefault('status', 'OPEN')
        row.setdefault('first_seen_at', now)
        row.setdefault('last_seen_at', now)
        self._tickets[fp] = row
        return dict(row)

    def list_tickets(self, status: Optional[str] = None) -> Sequence[dict]:
        # Sample: List tickets sorted by occurrence_count descending. Filter by status OPEN when provided.
        items = list(self._tickets.values())
        if status is not None:
            items = [t for t in items if t.get('status') == status]
        items.sort(key=lambda t: int(t.get('occurrence_count', 1)), reverse=True)
        return [dict(t) for t in items]

    def create_candidate(self, candidate: Mapping[str, Any]) -> dict:
        # Sample: Store PROPOSED candidate C-1 drafted by agent S3.
        row = dict(candidate)
        row.setdefault('status', 'PROPOSED')
        row.setdefault('approved_by', None)
        row.setdefault('approved_at', None)
        row.setdefault('created_at', _utc_iso())
        self._candidates[row['candidate_id']] = row
        return dict(row)

    def approve_candidate(self, candidate_id: str, approver: str) -> dict:
        # Sample: Approve C-1 as operator. Succeed. Approve C-1 as agent proposer. Raise PermissionError.
        row = self._candidates.get(candidate_id)
        if not row:
            raise KeyError(f'Unknown candidate {candidate_id}')
        if not approver or approver.lower() not in self._humans or approver == row.get('proposed_by'):
            raise PermissionError(f'Candidate approval requires an authorized human reviewer distinct from proposer: {approver}')
        row['status'] = 'APPROVED'
        row['approved_by'] = approver
        row['approved_at'] = _utc_iso()
        return dict(row)

    def list_candidates(self, status: Optional[str] = None) -> Sequence[dict]:
        # Sample: List policy candidates filtered by APPROVED status when given.
        items = list(self._candidates.values())
        if status is not None:
            items = [c for c in items if c.get('status') == status]
        return [dict(c) for c in items]

    def record_evaluation(self, evaluation: Mapping[str, Any]) -> dict:
        # Sample: Record shadow-replay evaluation EV-1 for candidate C-1.
        if evaluation['candidate_id'] not in self._candidates:
            raise KeyError(f"Unknown candidate {evaluation['candidate_id']}")
        row = dict(evaluation)
        row.setdefault('evaluated_at', _utc_iso())
        self._evaluations[row['eval_id']] = row
        return dict(row)

    def list_evaluations(self, candidate_id: Optional[str] = None) -> Sequence[dict]:
        # Sample: List evaluation rows for candidate C-1 in insertion order.
        items = list(self._evaluations.values())
        if candidate_id is not None:
            items = [e for e in items if e.get('candidate_id') == candidate_id]
        return [dict(e) for e in items]

    def upsert_route_stats(self, stats: Mapping[str, Any]) -> dict:
        # Sample: Upsert route statistics for (ui, py-fastapi-next, DeepSeek-V4.1-Flash, deepinfra, 1.0.0).
        key = (
            stats['component_category'],
            stats.get('shell_profile', 'py-fastapi-next'),
            stats['model_id'],
            stats['provider'],
            stats['pack_version'],
        )
        row = dict(stats)
        row['shell_profile'] = key[1]
        row.setdefault('updated_at', _utc_iso())
        self._route_stats[key] = row
        return dict(row)

    def get_route_stats(self, component_category: str, shell_profile: str = 'py-fastapi-next') -> Sequence[dict]:
        # Sample: Return route stats for component_category ordered by mean_reward descending.
        rows = [
            dict(r) for k, r in self._route_stats.items()
            if k[0] == component_category and k[1] == shell_profile
        ]
        rows.sort(key=lambda r: float(r.get('mean_reward', 0.0)), reverse=True)
        return rows
