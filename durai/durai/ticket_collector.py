"""Sample: Compute deterministic RL rewards, extract execution friction signals, file deduped tickets, and update route stats."""
import hashlib
import json
import math
import re
import uuid
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from .privacy_sanitizer import PrivacySanitizer

VALID_SIGNAL_KINDS = frozenset({
    'EDIT_MISMATCH',
    'PLAN_GATE_REFUSAL',
    'GET_CHANGES_COOKIE',
    'STALL_NUDGE',
    'CHECK_APP_FAIL',
    'SMELL_HIGH',
    'UI_RUNTIME_ERROR',
})

VALID_SOURCE_MODES = frozenset({'LOCAL_DURAI', 'PUBLIC_SHELL'})
UI_RUNTIME_RE = re.compile(r'(?:React\s*#?441|ErrorBoundary|409\s+Conflict|UI_RUNTIME_ERROR|unhandledRejection|hydration)', re.IGNORECASE)
NON_200_STATUS_RE = re.compile(r'^status:\s*(?!200\b)\S+', re.MULTILINE)


def _detail_digest(sanitizer: PrivacySanitizer, text: str) -> str:
    # Sample: Sanitize detail text before hashing into a 64-character SHA-256 digest.
    clean = sanitizer.sanitize_text(text)
    return hashlib.sha256(clean.encode('utf-8')).hexdigest()


def _check_counts(receipt: Mapping[str, Any]) -> tuple[int, int]:
    # Sample: Read passed and failed check counts from top-level fields or lastRunTests.
    if 'passed_checks' in receipt or 'failed_checks' in receipt:
        return int(receipt.get('passed_checks') or 0), int(receipt.get('failed_checks') or 0)
    if 'passedChecks' in receipt or 'failedChecks' in receipt:
        return int(receipt.get('passedChecks') or 0), int(receipt.get('failedChecks') or 0)
    last_tests = receipt.get('lastRunTests') or receipt.get('last_run_tests') or {}
    if isinstance(last_tests, Mapping):
        return int(last_tests.get('passed') or 0), int(last_tests.get('failed') or 0)
    return 0, 0


def _tool_error_count(receipt: Mapping[str, Any]) -> int:
    # Sample: Sum tool error counts from receipt tool_error_count integer or toolErrors dict.
    if 'tool_error_count' in receipt:
        return int(receipt.get('tool_error_count') or 0)
    if 'toolErrorCount' in receipt:
        return int(receipt.get('toolErrorCount') or 0)
    errors = receipt.get('toolErrors', receipt.get('tool_errors', 0))
    if isinstance(errors, Mapping):
        return sum(int(val or 0) for val in errors.values())
    return int(errors or 0)


def _stall_nudges(receipt: Mapping[str, Any]) -> int:
    # Sample: Read stall_nudges, stallNudges, or nudges count from receipt.
    for key in ('stall_nudges', 'stallNudges', 'nudges'):
        if key in receipt and receipt[key] is not None:
            return int(receipt[key])
    return 0


def _smell_counts(receipt: Mapping[str, Any], smells: Any) -> tuple[int, int]:
    # Sample: Extract high and low smell counts from smell_scorer output dict, list, or receipt fields.
    if isinstance(smells, Mapping):
        summary = smells.get('summary')
        if isinstance(summary, Mapping):
            return int(summary.get('high') or 0), int(summary.get('low') or 0)
        items = smells.get('smells')
        if isinstance(items, Sequence) and not isinstance(items, (str, bytes)):
            smells = items
    if isinstance(smells, Sequence) and not isinstance(smells, (str, bytes)):
        high = sum(1 for item in smells if isinstance(item, Mapping) and str(item.get('severity', '')).upper() == 'HIGH')
        low = sum(1 for item in smells if isinstance(item, Mapping) and str(item.get('severity', '')).upper() == 'LOW')
        return high, low
    high = int(receipt.get('smell_high', receipt.get('smellHigh', 0)) or 0)
    low = int(receipt.get('smell_low', receipt.get('smellLow', 0)) or 0)
    return high, low


def _list_cost(receipt: Mapping[str, Any]) -> float:
    # Sample: Read list_cost_usd or estimatedListCost float from receipt.
    raw = receipt.get('list_cost_usd', receipt.get('estimatedListCost', 0.0))
    return max(0.0, float(raw or 0.0))


def _stop_code(receipt: Mapping[str, Any]) -> str:
    # Sample: Read stopReason or stop_reason string from receipt.
    return str(receipt.get('stopReason') or receipt.get('stop_reason') or 'UNKNOWN')


def compute_reward(receipt: Mapping[str, Any], smells: Any = None) -> float:
    # Sample: Compute deterministic RL reward from executed checks, tool errors, nudges, smells, and list cost. Cap at 0 when total checks equal 0.
    stop_code = _stop_code(receipt)
    passed_checks, failed_checks = _check_counts(receipt)
    tool_errors = _tool_error_count(receipt)
    stall_nudges = _stall_nudges(receipt)
    high_smells, low_smells = _smell_counts(receipt, smells)
    list_cost_usd = _list_cost(receipt)
    total_checks = passed_checks + failed_checks
    reward = (
        (1.00 if stop_code == 'FINISHED' else 0.0)
        + 0.50 * (passed_checks / max(total_checks, 1))
        - 0.05 * tool_errors
        - 0.15 * stall_nudges
        - 0.10 * high_smells
        - 0.02 * low_smells
        - 0.25 * math.log1p(list_cost_usd * 10.0)
    )
    if total_checks == 0:
        reward = min(0.0, reward)
    return round(reward, 4)


def _normalize_web_rules(web_rules: Any) -> list[dict]:
    # Sample: Normalize check_web_rules list or check_sign_in dict into rule dictionaries.
    if not web_rules:
        return []
    if isinstance(web_rules, Mapping):
        if isinstance(web_rules.get('violations'), Sequence) and not isinstance(web_rules['violations'], (str, bytes)):
            return _normalize_web_rules(web_rules['violations'])
        if web_rules.get('passed') is False and web_rules.get('rule'):
            return [{'rule': str(web_rules['rule']), 'path': str(web_rules.get('path') or ''), 'message': ''}]
        if web_rules.get('rule'):
            return [{'rule': str(web_rules['rule']), 'path': str(web_rules.get('path') or ''), 'message': str(web_rules.get('message') or '')}]
        return []
    items = []
    if isinstance(web_rules, Sequence) and not isinstance(web_rules, (str, bytes)):
        for entry in web_rules:
            if isinstance(entry, Mapping) and entry.get('rule'):
                items.append({'rule': str(entry['rule']), 'path': str(entry.get('path') or ''), 'message': str(entry.get('message') or '')})
            elif isinstance(entry, str) and entry.strip():
                items.append({'rule': entry.strip(), 'path': '', 'message': ''})
    return items


def _normalize_smells(smells: Any) -> list[dict]:
    # Sample: Extract smell dictionaries from smell_scorer report dict or list.
    if isinstance(smells, Mapping):
        items = smells.get('smells')
        if isinstance(items, Sequence) and not isinstance(items, (str, bytes)):
            return [dict(item) for item in items if isinstance(item, Mapping)]
        return []
    if isinstance(smells, Sequence) and not isinstance(smells, (str, bytes)):
        return [dict(item) for item in smells if isinstance(item, Mapping)]
    return []


def _make_signal(step_index: int, signal_kind: str, tool_name: Optional[str], rule_id: str, detail: str, sanitizer: PrivacySanitizer) -> dict:
    # Sample: Build one sanitized episode_signal dict with SHA-256 detail_digest.
    return {
        'step_index': step_index,
        'signal_kind': signal_kind,
        'tool_name': tool_name,
        'rule_id': rule_id,
        'detail_digest': _detail_digest(sanitizer, detail),
    }


def _extract_trace_signal(step_index: int, entry: Mapping[str, Any], call_map: Mapping[str, str], sanitizer: PrivacySanitizer) -> Optional[dict]:
    # Sample: Classify one trace message entry into a valid episode_signal dict or None.
    role = str(entry.get('role') or '')
    content = str(entry.get('content') or '')
    tool_name = entry.get('name') or call_map.get(str(entry.get('tool_call_id') or ''))
    status = str(entry.get('status') or '')
    explicit_kind = entry.get('signal_kind')
    if explicit_kind in VALID_SIGNAL_KINDS:
        return _make_signal(step_index, explicit_kind, tool_name, entry.get('rule_id') or explicit_kind, content or str(explicit_kind), sanitizer)
    if role in ('human', 'user', 'system') and ('without a stage advance' in content or 'STOP_STALL' in content):
        return _make_signal(step_index, 'STALL_NUDGE', None, 'STALL_NUDGE', content, sanitizer)
    is_error = status == 'error' or bool(entry.get('isError')) or content.startswith('ERROR:')
    if role != 'tool' and not is_error:
        return None
    if 'GET_CHANGES_COOKIE' in content or 'PREFETCH_CHANGED_COOKIE' in content:
        rule_id = 'PREFETCH_CHANGED_COOKIE' if 'PREFETCH_CHANGED_COOKIE' in content else 'GET_CHANGES_COOKIE'
        return _make_signal(step_index, 'GET_CHANGES_COOKIE', tool_name or 'check_app', rule_id, content, sanitizer)
    if UI_RUNTIME_RE.search(content):
        return _make_signal(step_index, 'UI_RUNTIME_ERROR', tool_name, 'UI_RUNTIME_ERROR', content, sanitizer)
    if is_error and (tool_name == 'edit_file' or 'Old text must occur exactly once' in content):
        return _make_signal(step_index, 'EDIT_MISMATCH', tool_name or 'edit_file', 'EDIT_EXACT_MATCH', content, sanitizer)
    if is_error and ('DEPLOYED refused' in content or tool_name == 'check_app'):
        return _make_signal(step_index, 'CHECK_APP_FAIL', tool_name or 'check_app', 'DEPLOYED_CHECK_APP_GATE', content, sanitizer)
    if is_error and (tool_name in ('write_file', 'edit_file', 'submit_stage') or 'refused' in content.lower() or 'stage' in content.lower()):
        return _make_signal(step_index, 'PLAN_GATE_REFUSAL', tool_name, 'PLAN_STAGE_GATE', content, sanitizer)
    if tool_name == 'check_app' and ('DEPLOYED is refused until check_app passes' in content or NON_200_STATUS_RE.search(content)):
        return _make_signal(step_index, 'CHECK_APP_FAIL', 'check_app', 'CHECK_APP_HTTP_OR_SIGNIN', content, sanitizer)
    return None


def extract_signals(trace_entries: Any, smells: Any = None, web_rules: Any = None) -> list[dict]:
    # Sample: Extract step-level friction signals from trace entries, HIGH smells, and web rule violations.
    sanitizer = PrivacySanitizer()
    if isinstance(trace_entries, Mapping):
        entries = list(trace_entries.get('messages') or [])
    elif isinstance(trace_entries, Sequence) and not isinstance(trace_entries, (str, bytes)):
        entries = list(trace_entries)
    else:
        entries = []

    call_map: dict[str, str] = {}
    signals: list[dict] = []
    for step_index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            continue
        for call in entry.get('tool_calls') or ():
            if isinstance(call, Mapping) and call.get('id') and call.get('name'):
                call_map[str(call['id'])] = str(call['name'])
        signal = _extract_trace_signal(step_index, entry, call_map, sanitizer)
        if signal is not None:
            signals.append(signal)

    next_step = len(entries)
    for smell in _normalize_smells(smells):
        if str(smell.get('severity', '')).upper() == 'HIGH':
            kind = str(smell.get('kind') or 'SMELL_HIGH')
            detail = f"{kind}:{smell.get('line', 0)}"
            signals.append({
                'step_index': next_step,
                'signal_kind': 'SMELL_HIGH',
                'tool_name': 'smell_scorer',
                'rule_id': kind,
                'detail_digest': _detail_digest(sanitizer, detail),
            })
            next_step += 1

    for rule_item in _normalize_web_rules(web_rules):
        rule_name = rule_item['rule']
        signal_kind = 'GET_CHANGES_COOKIE' if rule_name in ('GET_CHANGES_COOKIE', 'PREFETCH_CHANGED_COOKIE') else 'CHECK_APP_FAIL'
        detail = f"{rule_name}:{rule_item.get('message', '')}"
        signals.append({
            'step_index': next_step,
            'signal_kind': signal_kind,
            'tool_name': 'build_web_rules',
            'rule_id': rule_name,
            'detail_digest': _detail_digest(sanitizer, detail),
        })
        next_step += 1

    return signals


def _load_trace_entries(trace_path: Any) -> list[dict]:
    # Sample: Load JSON retained worker trace or JSONL stream trace from trace_path.
    if not trace_path:
        return []
    path = Path(trace_path)
    if not path.is_file():
        return []
    raw = path.read_text(encoding='utf-8', errors='replace').strip()
    if not raw:
        return []
    if path.suffix.lower() == '.jsonl' or ('\n' in raw and not raw.startswith('{')):
        entries = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            parsed = json.loads(line)
            if isinstance(parsed, Mapping):
                entries.append(dict(parsed))
        return entries
    parsed = json.loads(raw)
    if isinstance(parsed, Mapping):
        msgs = parsed.get('messages')
        return [dict(m) for m in msgs if isinstance(m, Mapping)] if isinstance(msgs, Sequence) else []
    if isinstance(parsed, list):
        return [dict(m) for m in parsed if isinstance(m, Mapping)]
    return []


def _choose_ticket_category(stop_code: str, tool_errors: int, signals: Sequence[Mapping[str, Any]], web_items: Sequence[Mapping[str, Any]], receipt: Mapping[str, Any]) -> tuple[str, str]:
    # Sample: Select ticket category and primary rule identifier from friction signals and stop code.
    if web_items or any(s.get('signal_kind') == 'GET_CHANGES_COOKIE' for s in signals):
        rule = web_items[0]['rule'] if web_items else next((str(s.get('rule_id') or 'GET_CHANGES_COOKIE') for s in signals if s.get('signal_kind') == 'GET_CHANGES_COOKIE'), 'GET_CHANGES_COOKIE')
        return 'WEB_RULE_GAP', rule
    if receipt.get('uiUxDefect') or receipt.get('ui_ux_defect') or any(s.get('signal_kind') == 'UI_RUNTIME_ERROR' for s in signals):
        rule = next((str(s.get('rule_id') or 'UI_RUNTIME_ERROR') for s in signals if s.get('signal_kind') == 'UI_RUNTIME_ERROR'), 'UI_RUNTIME_ERROR')
        return 'UI_UX_DEFECT', rule
    if any(s.get('rule_id') == 'RAW_SQL_FORMAT' for s in signals):
        return 'SQL_BLUEPRINT_GAP', 'RAW_SQL_FORMAT'
    if stop_code in ('PROVIDER_ERROR', 'STOP_COST', 'STOP_BUDGET') and tool_errors < 2:
        return 'ROUTING', stop_code
    if stop_code in ('STOP_STALL', 'STOP_IDLE'):
        return 'PROMPT_AMBIGUITY', stop_code
    rule = next((str(s.get('rule_id')) for s in signals if s.get('rule_id')), stop_code)
    return 'TOOL_FRICTION', rule


def _choose_severity(stop_code: str, category: str, tool_errors: int, failed_checks: int, high_smells: int) -> str:
    # Sample: Assign LOW, MEDIUM, HIGH, or CRITICAL severity from failure metrics.
    if stop_code == 'PROVIDER_ERROR' or high_smells >= 2:
        return 'CRITICAL'
    if stop_code != 'FINISHED' or high_smells >= 1 or category in ('WEB_RULE_GAP', 'SQL_BLUEPRINT_GAP', 'UI_UX_DEFECT'):
        return 'HIGH'
    if tool_errors >= 2 or failed_checks > 0:
        return 'MEDIUM'
    return 'LOW'


def _update_route_stats(
    store: Any,
    component_category: str,
    shell_profile: str,
    model_id: str,
    provider: str,
    pack_version: str,
    stop_code: str,
    calls_used: int,
    list_cost_usd: float,
    reward_score: float,
) -> dict:
    # Sample: Refresh rolling model_route_stats row for (component_category, shell_profile, model_id, provider, pack_version).
    existing = store.get_route_stats(component_category, shell_profile=shell_profile)
    prev = next(
        (
            row for row in existing
            if row.get('model_id') == model_id and row.get('provider') == provider and row.get('pack_version') == pack_version
        ),
        None,
    )
    finished = 1.0 if stop_code == 'FINISHED' else 0.0
    if prev:
        prev_n = int(prev.get('episodes_count') or 0)
        new_n = prev_n + 1
        finish_rate = round((float(prev.get('finish_rate') or 0.0) * prev_n + finished) / new_n, 4)
        avg_calls = round((float(prev.get('avg_calls') or 0.0) * prev_n + calls_used) / new_n, 2)
        avg_list_cost = round((float(prev.get('avg_list_cost_usd') or 0.0) * prev_n + list_cost_usd) / new_n, 6)
        mean_reward = round((float(prev.get('mean_reward') or 0.0) * prev_n + reward_score) / new_n, 4)
    else:
        new_n = 1
        finish_rate = finished
        avg_calls = round(float(calls_used), 2)
        avg_list_cost = round(float(list_cost_usd), 6)
        mean_reward = round(float(reward_score), 4)

    return store.upsert_route_stats({
        'component_category': component_category,
        'shell_profile': shell_profile,
        'model_id': model_id,
        'provider': provider,
        'pack_version': pack_version,
        'episodes_count': new_n,
        'finish_rate': finish_rate,
        'avg_calls': avg_calls,
        'avg_list_cost_usd': avg_list_cost,
        'mean_reward': mean_reward,
    })


def _build_ticket_row(
    sanitizer: PrivacySanitizer,
    receipt: Mapping[str, Any],
    episode: Mapping[str, Any],
    signals: Sequence[Mapping[str, Any]],
    smells: Any,
    web_items: Sequence[Mapping[str, Any]],
    pack_id: str,
    pack_version: str,
    source_mode: str,
) -> dict:
    # Sample: Build and sanitize a friction ticket row with deterministic SHA-256 fingerprint.
    stop_code = str(episode['stop_reason'])
    tool_errors = int(episode['tool_error_count'])
    failed_checks = int(episode['failed_checks'])
    high_smells = int(episode['smell_high'])
    component_category = str(episode['component_category'])
    shell_profile = str(episode['shell_profile'])

    category, rule_id = _choose_ticket_category(stop_code, tool_errors, signals, web_items, receipt)
    severity = _choose_severity(stop_code, category, tool_errors, failed_checks, high_smells)
    shell_version = str(receipt.get('shellVersion') or receipt.get('shell_version') or '1.0.0')
    fingerprint = hashlib.sha256(f'{category}:{rule_id}:{component_category}:{shell_version}'.encode('utf-8')).hexdigest()
    smell_kinds = sorted({str(s.get('kind')) for s in _normalize_smells(smells) if s.get('kind')})
    signal_kinds = sorted({str(s['signal_kind']) for s in signals})
    rule_ids = sorted({str(s['rule_id']) for s in signals if s.get('rule_id')} | {rule_id})
    summary = sanitizer.sanitize_text(f'{category} ({rule_id}) on {component_category} [{stop_code}]')

    raw_payload = dict(receipt)
    raw_payload.update({
        'fingerprint': fingerprint,
        'pack_id': pack_id,
        'pack_version': pack_version,
        'shell_version': shell_version,
        'shell_profile': shell_profile,
        'component_category': component_category,
        'category': category,
        'severity': severity,
        'rule_id': rule_id,
        'stop_reason': stop_code,
        'signal_kinds': signal_kinds,
        'rule_ids': rule_ids,
        'smell_kinds': smell_kinds,
        'tags': [component_category, shell_profile, stop_code],
        'effective_view_digest': episode['effective_view_digest'],
        'calls_used': episode['calls_used'],
        'passed_checks': episode['passed_checks'],
        'failed_checks': failed_checks,
        'tool_error_count': tool_errors,
        'stall_nudges': episode['stall_nudges'],
        'list_cost_usd': episode['list_cost_usd'],
    })
    ticket_row = {
        'ticket_id': f'TKT-{fingerprint[:12].upper()}',
        'fingerprint': fingerprint,
        'source_mode': source_mode,
        'client_instance_hash': receipt.get('client_instance_hash') or receipt.get('clientInstanceHash'),
        'pack_id': pack_id,
        'pack_version': pack_version,
        'shell_version': shell_version,
        'category': category,
        'severity': severity,
        'summary': summary,
        'sanitized_payload': sanitizer.sanitize_payload(raw_payload),
    }
    return sanitizer.sanitize_ticket(ticket_row) if source_mode == 'PUBLIC_SHELL' else ticket_row


def collect_run_feedback(
    store: Any,
    receipt: Mapping[str, Any],
    trace_path: Any = None,
    smells: Any = None,
    web_rules: Any = None,
    component_category: str = 'general',
    shell_profile: str = 'py-fastapi-next',
    effective_view_digest: str = 'none',
    pack_id: str = 'durai.starter',
    pack_version: str = '1.0.0',
    source_mode: str = 'LOCAL_DURAI',
) -> dict:
    # Sample: Record execution episode, append extracted signals, file or dedup friction ticket, and update model_route_stats.
    if source_mode not in VALID_SOURCE_MODES:
        raise ValueError(f'Invalid source_mode: {source_mode}')

    sanitizer = PrivacySanitizer()
    trace_entries = _load_trace_entries(trace_path if trace_path is not None else receipt.get('tracePath'))
    sample_id = str(receipt.get('sampleId') or receipt.get('sample_id') or 'UNKNOWN')
    run_id = str(receipt.get('runId') or receipt.get('run_id') or receipt.get('label') or 'local-run')
    episode_id = str(receipt.get('episodeId') or receipt.get('episode_id') or f'ep-{run_id}-{sample_id}-{uuid.uuid4().hex[:8]}')
    model_id = str(receipt.get('model') or receipt.get('model_id') or 'unknown')
    provider = str(receipt.get('provider') or 'unknown')
    stop_code = _stop_code(receipt)
    calls_used = int(receipt.get('calls', receipt.get('calls_used', 0)) or 0)
    list_cost_usd = _list_cost(receipt)
    passed_checks, failed_checks = _check_counts(receipt)
    tool_errors = _tool_error_count(receipt)
    high_smells, low_smells = _smell_counts(receipt, smells)
    reward_score = compute_reward(receipt, smells=smells)
    signals = extract_signals(trace_entries, smells=smells, web_rules=web_rules)

    episode = store.record_episode({
        'episode_id': episode_id,
        'run_id': run_id,
        'sample_id': sample_id,
        'component_category': component_category,
        'shell_profile': shell_profile,
        'model_id': model_id,
        'provider': provider,
        'effective_view_digest': effective_view_digest,
        'stop_reason': stop_code,
        'calls_used': calls_used,
        'input_tokens': int(receipt.get('inputTokens', receipt.get('input_tokens', 0)) or 0),
        'output_tokens': int(receipt.get('outputTokens', receipt.get('output_tokens', 0)) or 0),
        'list_cost_usd': list_cost_usd,
        'enterprise_cost_usd': float(receipt.get('estimatedEnterpriseCost', receipt.get('enterprise_cost_usd', 0.0)) or 0.0),
        'passed_checks': passed_checks,
        'failed_checks': failed_checks,
        'tool_error_count': tool_errors,
        'stall_nudges': _stall_nudges(receipt),
        'smell_high': high_smells,
        'smell_low': low_smells,
        'reward_score': reward_score,
    })
    recorded_signals = [store.append_signal({'episode_id': episode_id, **sig}) for sig in signals]

    web_items = _normalize_web_rules(web_rules)
    has_friction = (
        stop_code != 'FINISHED' or tool_errors >= 2 or failed_checks > 0 or high_smells >= 1
        or bool(web_items) or bool(signals) or bool(receipt.get('uiUxDefect') or receipt.get('ui_ux_defect'))
    )
    ticket = None
    if has_friction:
        ticket_row = _build_ticket_row(sanitizer, receipt, episode, signals, smells, web_items, pack_id, pack_version, source_mode)
        ticket = store.upsert_ticket(ticket_row)

    route_stats = _update_route_stats(
        store, component_category, shell_profile, model_id, provider, pack_version,
        stop_code, calls_used, list_cost_usd, reward_score,
    )
    return {'episode': episode, 'signals': recorded_signals, 'ticket': ticket, 'route_stats': route_stats}
