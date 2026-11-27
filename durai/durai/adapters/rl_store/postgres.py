"""Sample: Create PostgresRLStore with a connect function. Call ensure_schema. Record verified episode EP-1, append signal, upsert ticket by fingerprint, and block agent self-approval."""
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from psycopg.rows import dict_row

from ...plans_dir import human_reviewers

SCHEMA_PATH = Path(__file__).resolve().parents[3] / 'migrations' / 'improvement' / 'rl_0001.sql.proposed'


def _normalize_row(row: Optional[Mapping[str, Any]]) -> Optional[dict]:
    # Sample: Convert Decimal and datetime values in a database row to float and ISO-8601 strings.
    if row is None:
        return None
    out = {}
    for key, value in row.items():
        if isinstance(value, Decimal):
            out[key] = float(value)
        elif isinstance(value, datetime):
            out[key] = value.isoformat()
        else:
            out[key] = value
    if 'sanitized_payload' in out and isinstance(out['sanitized_payload'], str):
        out['sanitized_payload'] = json.loads(out['sanitized_payload'])
    return out


class PostgresRLStore:
    def __init__(self, connect, human_allowlist: Optional[Sequence[str]] = None):
        # Sample: Hold a connect callable and human reviewer allowlist without opening a connection yet.
        self.connect = connect
        self._humans = {h.lower() for h in (human_allowlist or human_reviewers())}

    def ensure_schema(self):
        # Sample: Check whether durai_improvement tables exist. Skip DDL if present, else apply rl_0001.sql.proposed.
        with self.connect() as conn:
            row = conn.execute("SELECT to_regclass('durai_improvement.model_route_stats') AS rel").fetchone()
            exists = (row['rel'] if isinstance(row, dict) else row[0]) is not None
            if exists:
                return
            sql_text = SCHEMA_PATH.read_text(encoding='utf-8')
            conn.execute(sql_text, prepare=False)

    def record_episode(self, episode: Mapping[str, Any]) -> dict:
        # Sample: Record episode EP-1. Raise ValueError when passed_checks + failed_checks == 0 and reward_score > 0.
        passed = int(episode.get('passed_checks', 0))
        failed = int(episode.get('failed_checks', 0))
        reward = float(episode.get('reward_score', 0.0))
        if (passed + failed) == 0 and reward > 0:
            raise ValueError('Unverified episode with 0 executed checks cannot have positive reward')
        params = {
            'episode_id': episode['episode_id'],
            'run_id': episode['run_id'],
            'sample_id': episode['sample_id'],
            'component_category': episode['component_category'],
            'shell_profile': episode.get('shell_profile', 'py-fastapi-next'),
            'model_id': episode['model_id'],
            'provider': episode['provider'],
            'effective_view_digest': episode['effective_view_digest'],
            'stop_reason': episode['stop_reason'],
            'calls_used': int(episode['calls_used']),
            'input_tokens': int(episode.get('input_tokens', 0)),
            'output_tokens': int(episode.get('output_tokens', 0)),
            'list_cost_usd': episode.get('list_cost_usd', 0),
            'enterprise_cost_usd': episode.get('enterprise_cost_usd', 0),
            'passed_checks': passed,
            'failed_checks': failed,
            'tool_error_count': int(episode.get('tool_error_count', 0)),
            'stall_nudges': int(episode.get('stall_nudges', 0)),
            'smell_high': int(episode.get('smell_high', 0)),
            'smell_low': int(episode.get('smell_low', 0)),
            'reward_score': reward,
            'recorded_at': episode.get('recorded_at'),
        }
        with self.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute(
                """
                INSERT INTO durai_improvement.execution_episode (
                    episode_id, run_id, sample_id, component_category, shell_profile,
                    model_id, provider, effective_view_digest, stop_reason, calls_used,
                    input_tokens, output_tokens, list_cost_usd, enterprise_cost_usd,
                    passed_checks, failed_checks, tool_error_count, stall_nudges,
                    smell_high, smell_low, reward_score, recorded_at
                ) VALUES (
                    %(episode_id)s, %(run_id)s, %(sample_id)s, %(component_category)s, %(shell_profile)s,
                    %(model_id)s, %(provider)s, %(effective_view_digest)s, %(stop_reason)s, %(calls_used)s,
                    %(input_tokens)s, %(output_tokens)s, %(list_cost_usd)s, %(enterprise_cost_usd)s,
                    %(passed_checks)s, %(failed_checks)s, %(tool_error_count)s, %(stall_nudges)s,
                    %(smell_high)s, %(smell_low)s, %(reward_score)s,
                    COALESCE(%(recorded_at)s::timestamptz, clock_timestamp())
                )
                ON CONFLICT (episode_id) DO UPDATE SET
                    run_id = EXCLUDED.run_id,
                    sample_id = EXCLUDED.sample_id,
                    component_category = EXCLUDED.component_category,
                    shell_profile = EXCLUDED.shell_profile,
                    model_id = EXCLUDED.model_id,
                    provider = EXCLUDED.provider,
                    effective_view_digest = EXCLUDED.effective_view_digest,
                    stop_reason = EXCLUDED.stop_reason,
                    calls_used = EXCLUDED.calls_used,
                    input_tokens = EXCLUDED.input_tokens,
                    output_tokens = EXCLUDED.output_tokens,
                    list_cost_usd = EXCLUDED.list_cost_usd,
                    enterprise_cost_usd = EXCLUDED.enterprise_cost_usd,
                    passed_checks = EXCLUDED.passed_checks,
                    failed_checks = EXCLUDED.failed_checks,
                    tool_error_count = EXCLUDED.tool_error_count,
                    stall_nudges = EXCLUDED.stall_nudges,
                    smell_high = EXCLUDED.smell_high,
                    smell_low = EXCLUDED.smell_low,
                    reward_score = EXCLUDED.reward_score,
                    recorded_at = EXCLUDED.recorded_at
                RETURNING *
                """,
                params,
            ).fetchone()
            return _normalize_row(row)

    def get_episode(self, episode_id: str) -> Optional[dict]:
        # Sample: Retrieve episode EP-1 by id. Return dict or None.
        with self.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute(
                'SELECT * FROM durai_improvement.execution_episode WHERE episode_id = %s',
                (episode_id,),
            ).fetchone()
            return _normalize_row(row)

    def append_signal(self, signal: Mapping[str, Any]) -> dict:
        # Sample: Append step signal EDIT_MISMATCH for episode EP-1. Raise KeyError when episode is missing.
        with self.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            exists = cur.execute(
                'SELECT 1 FROM durai_improvement.execution_episode WHERE episode_id = %s',
                (signal['episode_id'],),
            ).fetchone()
            if not exists:
                raise KeyError(f"Unknown episode {signal['episode_id']}")
            row = cur.execute(
                """
                INSERT INTO durai_improvement.episode_signal (
                    episode_id, step_index, signal_kind, tool_name, rule_id, detail_digest, recorded_at
                ) VALUES (
                    %(episode_id)s, %(step_index)s, %(signal_kind)s, %(tool_name)s, %(rule_id)s,
                    %(detail_digest)s, COALESCE(%(recorded_at)s::timestamptz, clock_timestamp())
                )
                RETURNING *
                """,
                {
                    'episode_id': signal['episode_id'],
                    'step_index': int(signal['step_index']),
                    'signal_kind': signal['signal_kind'],
                    'tool_name': signal.get('tool_name'),
                    'rule_id': signal.get('rule_id'),
                    'detail_digest': signal['detail_digest'],
                    'recorded_at': signal.get('recorded_at'),
                },
            ).fetchone()
            return _normalize_row(row)

    def list_signals(self, episode_id: str) -> Sequence[dict]:
        # Sample: Return ordered step signals for episode EP-1.
        with self.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(
                'SELECT * FROM durai_improvement.episode_signal WHERE episode_id = %s ORDER BY signal_id ASC',
                (episode_id,),
            ).fetchall()
            return [_normalize_row(r) for r in rows]

    def upsert_ticket(self, ticket: Mapping[str, Any]) -> dict:
        # Sample: Upsert ticket with fingerprint F-1 twice. Check one ticket row with occurrence_count 2.
        payload = ticket.get('sanitized_payload', {})
        with self.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute(
                """
                INSERT INTO durai_improvement.ticket (
                    ticket_id, fingerprint, source_mode, client_instance_hash,
                    pack_id, pack_version, shell_version, category, severity,
                    summary, sanitized_payload, occurrence_count, status,
                    first_seen_at, last_seen_at
                ) VALUES (
                    %(ticket_id)s, %(fingerprint)s, %(source_mode)s, %(client_instance_hash)s,
                    %(pack_id)s, %(pack_version)s, %(shell_version)s, %(category)s, %(severity)s,
                    %(summary)s, %(sanitized_payload)s::jsonb, %(occurrence_count)s, %(status)s,
                    COALESCE(%(first_seen_at)s::timestamptz, clock_timestamp()),
                    COALESCE(%(last_seen_at)s::timestamptz, clock_timestamp())
                )
                ON CONFLICT (fingerprint) DO UPDATE SET
                    occurrence_count = durai_improvement.ticket.occurrence_count + 1,
                    last_seen_at = COALESCE(%(last_seen_at)s::timestamptz, clock_timestamp())
                RETURNING *
                """,
                {
                    'ticket_id': ticket['ticket_id'],
                    'fingerprint': ticket['fingerprint'],
                    'source_mode': ticket['source_mode'],
                    'client_instance_hash': ticket.get('client_instance_hash'),
                    'pack_id': ticket['pack_id'],
                    'pack_version': ticket['pack_version'],
                    'shell_version': ticket.get('shell_version', '1.0.0'),
                    'category': ticket['category'],
                    'severity': ticket['severity'],
                    'summary': ticket['summary'],
                    'sanitized_payload': json.dumps(payload),
                    'occurrence_count': int(ticket.get('occurrence_count', 1)),
                    'status': ticket.get('status', 'OPEN'),
                    'first_seen_at': ticket.get('first_seen_at'),
                    'last_seen_at': ticket.get('last_seen_at'),
                },
            ).fetchone()
            return _normalize_row(row)

    def list_tickets(self, status: Optional[str] = None) -> Sequence[dict]:
        # Sample: List tickets sorted by occurrence_count descending. Filter by status OPEN when provided.
        with self.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            if status is None:
                rows = cur.execute(
                    'SELECT * FROM durai_improvement.ticket ORDER BY occurrence_count DESC, first_seen_at ASC, ticket_id ASC'
                ).fetchall()
            else:
                rows = cur.execute(
                    'SELECT * FROM durai_improvement.ticket WHERE status = %s ORDER BY occurrence_count DESC, first_seen_at ASC, ticket_id ASC',
                    (status,),
                ).fetchall()
            return [_normalize_row(r) for r in rows]

    def create_candidate(self, candidate: Mapping[str, Any]) -> dict:
        # Sample: Store PROPOSED candidate C-1 drafted by agent S3.
        with self.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute(
                """
                INSERT INTO durai_improvement.policy_candidate (
                    candidate_id, cluster_fingerprint, target_kind, target_ref,
                    proposed_patch, status, proposed_by, approved_by, approved_at, created_at
                ) VALUES (
                    %(candidate_id)s, %(cluster_fingerprint)s, %(target_kind)s, %(target_ref)s,
                    %(proposed_patch)s, %(status)s, %(proposed_by)s, %(approved_by)s,
                    %(approved_at)s::timestamptz, COALESCE(%(created_at)s::timestamptz, clock_timestamp())
                )
                ON CONFLICT (candidate_id) DO UPDATE SET
                    cluster_fingerprint = EXCLUDED.cluster_fingerprint,
                    target_kind = EXCLUDED.target_kind,
                    target_ref = EXCLUDED.target_ref,
                    proposed_patch = EXCLUDED.proposed_patch,
                    status = EXCLUDED.status,
                    proposed_by = EXCLUDED.proposed_by,
                    approved_by = EXCLUDED.approved_by,
                    approved_at = EXCLUDED.approved_at,
                    created_at = EXCLUDED.created_at
                RETURNING *
                """,
                {
                    'candidate_id': candidate['candidate_id'],
                    'cluster_fingerprint': candidate['cluster_fingerprint'],
                    'target_kind': candidate['target_kind'],
                    'target_ref': candidate['target_ref'],
                    'proposed_patch': candidate['proposed_patch'],
                    'status': candidate.get('status', 'PROPOSED'),
                    'proposed_by': candidate['proposed_by'],
                    'approved_by': candidate.get('approved_by'),
                    'approved_at': candidate.get('approved_at'),
                    'created_at': candidate.get('created_at'),
                },
            ).fetchone()
            return _normalize_row(row)

    def approve_candidate(self, candidate_id: str, approver: str) -> dict:
        # Sample: Approve C-1 as operator. Succeed. Approve C-1 as agent proposer. Raise PermissionError.
        with self.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            existing = cur.execute(
                'SELECT proposed_by FROM durai_improvement.policy_candidate WHERE candidate_id = %s',
                (candidate_id,),
            ).fetchone()
            if not existing:
                raise KeyError(f'Unknown candidate {candidate_id}')
            if not approver or approver.lower() not in self._humans or approver == existing['proposed_by']:
                raise PermissionError(f'Candidate approval requires an authorized human reviewer distinct from proposer: {approver}')
            row = cur.execute(
                """
                UPDATE durai_improvement.policy_candidate
                SET status = 'APPROVED',
                    approved_by = %s,
                    approved_at = clock_timestamp()
                WHERE candidate_id = %s
                RETURNING *
                """,
                (approver, candidate_id),
            ).fetchone()
            return _normalize_row(row)

    def list_candidates(self, status: Optional[str] = None) -> Sequence[dict]:
        # Sample: List policy candidates filtered by APPROVED status when given.
        with self.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            if status is None:
                rows = cur.execute(
                    'SELECT * FROM durai_improvement.policy_candidate ORDER BY created_at ASC, candidate_id ASC'
                ).fetchall()
            else:
                rows = cur.execute(
                    'SELECT * FROM durai_improvement.policy_candidate WHERE status = %s ORDER BY created_at ASC, candidate_id ASC',
                    (status,),
                ).fetchall()
            return [_normalize_row(r) for r in rows]

    def record_evaluation(self, evaluation: Mapping[str, Any]) -> dict:
        # Sample: Record shadow-replay evaluation EV-1 for candidate C-1.
        with self.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            exists = cur.execute(
                'SELECT 1 FROM durai_improvement.policy_candidate WHERE candidate_id = %s',
                (evaluation['candidate_id'],),
            ).fetchone()
            if not exists:
                raise KeyError(f"Unknown candidate {evaluation['candidate_id']}")
            row = cur.execute(
                """
                INSERT INTO durai_improvement.policy_evaluation (
                    eval_id, candidate_id, baseline_episode_id, treatment_episode_id,
                    delta_pass_rate, delta_calls, delta_tool_errors, delta_list_cost_usd,
                    decision, evaluated_at
                ) VALUES (
                    %(eval_id)s, %(candidate_id)s, %(baseline_episode_id)s, %(treatment_episode_id)s,
                    %(delta_pass_rate)s, %(delta_calls)s, %(delta_tool_errors)s, %(delta_list_cost_usd)s,
                    %(decision)s, COALESCE(%(evaluated_at)s::timestamptz, clock_timestamp())
                )
                ON CONFLICT (eval_id) DO UPDATE SET
                    candidate_id = EXCLUDED.candidate_id,
                    baseline_episode_id = EXCLUDED.baseline_episode_id,
                    treatment_episode_id = EXCLUDED.treatment_episode_id,
                    delta_pass_rate = EXCLUDED.delta_pass_rate,
                    delta_calls = EXCLUDED.delta_calls,
                    delta_tool_errors = EXCLUDED.delta_tool_errors,
                    delta_list_cost_usd = EXCLUDED.delta_list_cost_usd,
                    decision = EXCLUDED.decision,
                    evaluated_at = EXCLUDED.evaluated_at
                RETURNING *
                """,
                {
                    'eval_id': evaluation['eval_id'],
                    'candidate_id': evaluation['candidate_id'],
                    'baseline_episode_id': evaluation.get('baseline_episode_id'),
                    'treatment_episode_id': evaluation.get('treatment_episode_id'),
                    'delta_pass_rate': evaluation['delta_pass_rate'],
                    'delta_calls': int(evaluation['delta_calls']),
                    'delta_tool_errors': int(evaluation['delta_tool_errors']),
                    'delta_list_cost_usd': evaluation['delta_list_cost_usd'],
                    'decision': evaluation['decision'],
                    'evaluated_at': evaluation.get('evaluated_at'),
                },
            ).fetchone()
            return _normalize_row(row)

    def list_evaluations(self, candidate_id: Optional[str] = None) -> Sequence[dict]:
        # Sample: Query policy_evaluation rows filtered by candidate C-1 ordered by evaluated_at ascending.
        with self.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            if candidate_id is None:
                rows = cur.execute(
                    'SELECT * FROM durai_improvement.policy_evaluation ORDER BY evaluated_at ASC, eval_id ASC'
                ).fetchall()
            else:
                rows = cur.execute(
                    'SELECT * FROM durai_improvement.policy_evaluation WHERE candidate_id = %s ORDER BY evaluated_at ASC, eval_id ASC',
                    (candidate_id,),
                ).fetchall()
            return [_normalize_row(r) for r in rows]

    def upsert_route_stats(self, stats: Mapping[str, Any]) -> dict:
        # Sample: Upsert route statistics for (ui, py-fastapi-next, DeepSeek-V4.1-Flash, deepinfra, 1.0.0).
        with self.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute(
                """
                INSERT INTO durai_improvement.model_route_stats (
                    component_category, shell_profile, model_id, provider, pack_version,
                    episodes_count, finish_rate, avg_calls, avg_list_cost_usd, mean_reward, updated_at
                ) VALUES (
                    %(component_category)s, %(shell_profile)s, %(model_id)s, %(provider)s, %(pack_version)s,
                    %(episodes_count)s, %(finish_rate)s, %(avg_calls)s, %(avg_list_cost_usd)s, %(mean_reward)s,
                    COALESCE(%(updated_at)s::timestamptz, clock_timestamp())
                )
                ON CONFLICT (component_category, shell_profile, model_id, provider, pack_version) DO UPDATE SET
                    episodes_count = EXCLUDED.episodes_count,
                    finish_rate = EXCLUDED.finish_rate,
                    avg_calls = EXCLUDED.avg_calls,
                    avg_list_cost_usd = EXCLUDED.avg_list_cost_usd,
                    mean_reward = EXCLUDED.mean_reward,
                    updated_at = EXCLUDED.updated_at
                RETURNING *
                """,
                {
                    'component_category': stats['component_category'],
                    'shell_profile': stats.get('shell_profile', 'py-fastapi-next'),
                    'model_id': stats['model_id'],
                    'provider': stats['provider'],
                    'pack_version': stats['pack_version'],
                    'episodes_count': int(stats.get('episodes_count', 0)),
                    'finish_rate': stats.get('finish_rate', 0),
                    'avg_calls': stats.get('avg_calls', 0),
                    'avg_list_cost_usd': stats.get('avg_list_cost_usd', 0),
                    'mean_reward': stats.get('mean_reward', 0),
                    'updated_at': stats.get('updated_at'),
                },
            ).fetchone()
            return _normalize_row(row)

    def get_route_stats(self, component_category: str, shell_profile: str = 'py-fastapi-next') -> Sequence[dict]:
        # Sample: Return route stats for component_category ordered by mean_reward descending.
        with self.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(
                """
                SELECT * FROM durai_improvement.model_route_stats
                WHERE component_category = %s AND shell_profile = %s
                ORDER BY mean_reward DESC, model_id ASC
                """,
                (component_category, shell_profile),
            ).fetchall()
            return [_normalize_row(r) for r in rows]
