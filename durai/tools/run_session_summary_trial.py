"""Sample: Build 48 public turns. Run at most 10 summary calls. Write a receipt with no key and no turn text."""
from datetime import datetime, timezone
from pathlib import Path
import json
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from durai import provider_config
from durai.common import digest, uid
from durai.db import connect
from durai.session_context import SessionContext
from durai.session_host import ConversationGrant, SessionHost, SessionIdentity
from durai.plans_dir import plans_dir
from durai.session_settings import load_session_settings
from durai.session_summarizer import DEEPSEEK_FLASH, DeepSeekSummaryProvider, SummaryCompletion

PLANS = plans_dir()
SOURCES = ('plan_durai_session_summaries_2026-11-20.md', 'plan_durai_conversation_summary_2026-11-20.md',
           'plan_durai_session_context_phase_a_2026-11-20.md', 'plan_durai_session_context_phase_b_2026-11-20.md',
           'plan_durai_session_context_host_2026-11-20.md', 'durai_session_context_phase_a_report_2026-11-20.md')
COMMIT = '93a7a7b'
PATH = 'durai/session_summarizer.py'
RECEIPT = 'session-context-phase-a-20261120.json'
REFERENCES = (COMMIT, PATH, RECEIPT)
FORBIDDEN = re.compile(r'sk-|apiKey|private\\|private/|password|Bearer', re.IGNORECASE)
CALL_LIMIT = 12
DATABASE = 'durai_local'
PIN_TURNS = {5: f'Remember commit {COMMIT}. It gates session summaries on trusted host conversation access.',
             21: f'Remember the file {PATH}. It holds the summary adapter and the output checks.',
             37: f'Remember the receipt {RECEIPT}. It records the Phase A proof on a disposable database.'}


def estimate_tokens(text):
    # Sample: Count 400 UTF-8 bytes as 100 estimated tokens.
    return max(1, -(-len(text.encode('utf-8')) // 4))


def source_lines():
    # Sample: Read the plan files. Return lines of 40 to 420 characters with no key pattern and no private path.
    lines = []
    for name in SOURCES:
        for line in (PLANS / name).read_text(encoding='utf-8').splitlines():
            text = line.strip().lstrip('-*# ').strip()
            if 40 <= len(text) <= 420 and not FORBIDDEN.search(text) and text not in lines:
                lines.append(text)
    return lines


def build_turns(count=48):
    # Sample: Build 48 turns. Check turns 5, 21, and 37 carry the 3 references and turn 1 is a user turn.
    lines = source_lines()
    turns, cursor = [], 0
    for number in range(1, count + 1):
        role = 'user' if number % 2 else 'assistant'
        if number in PIN_TURNS:
            text = PIN_TURNS[number]
        elif role == 'user':
            text = 'Please record this point from the session summaries work: ' + lines[cursor % len(lines)][:200]
            cursor += 1
        else:
            text = lines[cursor % len(lines)]
            cursor += 1
        turns.append((role, text))
    return turns


def resolve_refs(claim):
    # Sample: Claim turns 1 to 12 with the commit in turn 5. Return only the commit.
    seen = ' '.join(turn['text'] or '' for turn in claim['turns'])
    return [ref for ref in REFERENCES if ref in seen]


class TrialStore(SessionContext):
    def __init__(self, database, role, max_turns):
        # Sample: Claim at most 12 turns per call.
        super().__init__(database, role)
        self.max_turns = max_turns

    def claim(self):
        # Sample: Claim turns 1 to 12 with a 16000 token source limit.
        return super().claim(max_turns=self.max_turns, max_tokens=16000)


def call_record(number, content, completion):
    # Sample: Read call 2 with required commit and path. Record the missing path and the finish reason.
    required = set(json.loads(content)['requiredRefs'])
    record = {'call': number, 'finishReason': completion.finish_reason, 'inputTokens': completion.input_tokens,
              'outputTokens': completion.output_tokens,
              'visibleBytes': len((completion.text or '').encode()), 'estimatedVisibleTokens': estimate_tokens(completion.text or '')}
    try:
        body = json.loads(completion.text)
        record.update(missingRefs=sorted(required - set(body['refs'])), extraRefs=sorted(set(body['refs']) - required),
                      fields=sorted(body))
    except (TypeError, ValueError, KeyError):
        record['json'] = 'invalid'
    return record


class LimitedProvider:
    def __init__(self, inner, limit):
        # Sample: Allow 10 provider calls. Count each call before it is sent.
        self.inner, self.limit, self.calls = inner, limit, 0
        self.input_tokens = self.output_tokens = 0
        self.log = []

    def complete(self, system, content, profile):
        # Sample: Refuse call 11 before any request leaves the process.
        if self.calls >= self.limit:
            raise TimeoutError('The trial call limit is reached')
        self.calls += 1
        completion = self.inner.complete(system, content, profile)
        self.log.append(call_record(self.calls, content, completion))
        self.input_tokens += completion.input_tokens
        self.output_tokens += completion.output_tokens
        return completion


def drain(worker, provider, limit, retry_wait=35, max_waits=3):
    # Sample: Run claims until the queue is empty or 10 calls are used. After a failed range, wait 35 seconds for its retry, at most 3 times. Return the stop reason and results.
    results, waits = [], 0
    while True:
        if provider.calls >= limit:
            return 'call-limit', results
        result = worker.run_once()
        if result is None:
            if results and results[-1]['state'] != 'succeeded':
                if waits >= max_waits:
                    return 'attempt-failed-waiting-retry', results
                waits += 1
                time.sleep(retry_wait)
                continue
            return 'queue-empty', results
        results.append(result)


def price_row(model):
    # Sample: Read the DeepSeek Flash price row. Return null when no row exists.
    with connect('postgres', DATABASE) as db:
        row = db.execute("SELECT model,input_per_million,output_per_million,verification FROM durai_bench.price "
                         "WHERE provider='deepseek' AND model=%s ORDER BY effective_from DESC LIMIT 1",
                         (model,)).fetchone()
    return {key: str(value) for key, value in row.items()} if row else None


def run(database=DATABASE, limit=CALL_LIMIT, max_turns=12, provider=None):
    # Sample: Append 48 turns for one owner. Run the worker. Return the receipt body.
    cfg = load_session_settings()
    store = SessionContext(database)
    app_id = store.application_id()
    identity = SessionIdentity(app_id, 'trial-owner')
    conversation = 'summary-trial-' + uid()
    grant = ConversationGrant(identity, conversation, 1)
    host = SessionHost(store, {'trial-session': identity}.get, lambda who, cid: grant, estimate_tokens)
    for number, (role, text) in enumerate(build_turns(), 1):
        host.append_turn('trial-session', conversation, f'm-{number}', role, text, flush=True)
    if provider is None:
        config = provider_config.load('deepseek')
        provider = DeepSeekSummaryProvider.from_key(config['apiKey'], client_retries=cfg.clientRetries)
    limited = LimitedProvider(provider, limit)
    controller = TrialStore(database, 'durai_evaluator', max_turns)
    worker = host.summary_worker(controller, None, {grant.storage_id(): grant}.get, limited, DEEPSEEK_FLASH)
    pinned = worker.resolve_refs
    worker.resolve_refs = lambda claim: pinned(claim) + resolve_refs(claim)
    stop, results = drain(worker, limited, limit)
    context = host.get_context('trial-session', conversation, tail_tokens=2000, tail_turns=20)
    with connect('postgres', database) as db:
        spend = db.execute('SELECT calls,input_tokens,output_tokens,cost_usd FROM durai_context.session_spend '
                           'WHERE conversation_id=%s', (grant.storage_id(),)).fetchall()
        day = db.execute("SELECT coalesce(sum(calls),0) AS calls,coalesce(sum(cost_usd),0) AS cost FROM "
                         "durai_context.session_spend WHERE day=(clock_timestamp() AT TIME ZONE 'UTC')::date").fetchone()
        caps = db.execute('SELECT cap FROM durai_context.session_cap_event').fetchall()
        attempts = db.execute('SELECT state,count(*) AS n FROM durai_context.session_attempt WHERE conversation_id=%s '
                              'GROUP BY state', (grant.storage_id(),)).fetchall()
    summary = context['summary'] if context else None
    nodes = controller.read_nodes(grant.storage_id())['nodes']
    node_rows = [{'level': node['level'], 'first': node['first'], 'last': node['last'],
                  'digest': digest(json.dumps(node['body'], sort_keys=True).encode()),
                  'visibleTokens': estimate_tokens(json.dumps(node['body'], sort_keys=True))} for node in nodes]
    head_visible = estimate_tokens(json.dumps(summary, sort_keys=True)) if summary else None
    kept = [ref for ref in REFERENCES if summary and ref in summary['refs']]
    text = json.dumps(summary, sort_keys=True) if summary else ''
    in_tokens = sum(row['input_tokens'] for row in spend)
    out_tokens = sum(row['output_tokens'] for row in spend)
    price = price_row(DEEPSEEK_FLASH.model)
    real = None
    if price:
        real = round((in_tokens * float(price['input_per_million']) + out_tokens * float(price['output_per_million'])) / 1e6, 6)
    tail = context['turns'] if context else []
    tail_tokens = sum(row['token_count'] for row in tail)
    resumed = {'coveredTurn': context['coveredTurn'], 'latestTurn': context['latestTurn'],
               'omittedRange': context['omittedRange'], 'tailTurns': [row['turn_number'] for row in tail],
               'tailEstimatedTokens': tail_tokens, 'tailBudgetTokens': 2000} if context else None
    states = {row['state']: row['n'] for row in attempts}
    passed = bool(context and summary and len(kept) == 3 and stop == 'queue-empty'
                  and set(states) == {'succeeded'} and context['coveredTurn'] == context['latestTurn'] == 48
                  and head_visible is not None and head_visible <= cfg.maxVisibleTokens
                  and all(row['visibleTokens'] <= cfg.maxNodeVisibleTokens for row in node_rows)
                  and tail_tokens <= 2000 and limited.calls <= limit
                  and sum(row['cost_usd'] for row in spend) < cfg.maxCostUsdPerDay and not caps)
    return {
        'state': 'PASS' if passed else 'FAIL', 'checkedAt': datetime.now(timezone.utc).isoformat(),
        'conversationDigest': digest(conversation.encode()), 'turns': 48, 'stopReason': stop,
        'providerCalls': limited.calls, 'callLog': limited.log, 'callLimit': limit, 'attemptStates': states,
        'tokens': {'input': in_tokens, 'output': out_tokens, 'sdkReported': [limited.input_tokens, limited.output_tokens]},
        'costUsd': {'enterpriseRate': float(sum(row['cost_usd'] for row in spend)),
                    'enterprisePerMillion': [cfg.inputUsdPerMillionTokens, cfg.outputUsdPerMillionTokens],
                    'deepseekPriceRow': price, 'deepseekPriceCost': real},
        'profile': DEEPSEEK_FLASH.record(), 'tokenCounter': 'utf-8 bytes divided by 4, rounded up',
        'nodes': node_rows, 'headEstimatedTokens': head_visible,
        'summaryDigest': digest(text.encode()) if summary else None, 'summaryVisibleBytes': len(text.encode()),
        'summaryRevision': context['revision'] if context and 'revision' in context else None,
        'referenceChecks': {ref: ref in kept for ref in REFERENCES}, 'referencesKept': len(kept),
        'resumeRead': resumed,
        'capState': {'enabled': cfg.enabled, 'maxCallsPerConversationPerDay': cfg.maxCallsPerConversationPerDay,
                     'maxCallsPerDay': cfg.maxCallsPerDay, 'maxCostUsdPerDay': cfg.maxCostUsdPerDay,
                     'dayCalls': int(day['calls']), 'dayCostUsd': float(day['cost']),
                     'capEvents': [row['cap'] for row in caps]},
    }


def main(limit=CALL_LIMIT):
    # Sample: Run the live trial against durai_local. Write the receipt. Exit 0 on PASS.
    receipt = run(limit=limit)
    path = ROOT / 'receipts/session-summary-trial-20261122-e.json'
    path.write_text(json.dumps(receipt, indent=2, default=str) + '\n', encoding='utf-8')
    print(receipt['state'], receipt['providerCalls'], receipt['referencesKept'])
    return 0 if receipt['state'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
