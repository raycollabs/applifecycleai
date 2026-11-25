"""Sample: Seed nine resolved findings. Run nine queries and record ranked results."""
import argparse
import json
import math
import os
from pathlib import Path
import sys
from time import perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from durai.blueprint_patterns import transaction_pack
from durai.common import digest
from durai.context_delivery import attempt_memory
from durai.context_store import ContextStore
from durai.db import use_server
from durai.db_sandbox import EphemeralDatabase, docker
from durai.execution_events import replay
from durai.packs import generic_pack
from durai.prompt_quality import BINDING, REVISION
from durai.qualification import fixture_spec, run_fixture
from tools.build_transaction_patterns import install
from tools.verify_prompt_quality import DATABASE, FIXTURE, scripted

PLAN = 'prompt-memory-fixture'
OTHER = 'prompt-memory-other-plan'
DATA = ROOT / 'qualification/phase4/memory-retrieval-v1.json'


def add_finding(store, plan, item, outcome=None, evidence=True):
    # Sample: Add F-1 and a fixed resolution with evidence from plan P-1.
    key = item['id']
    row = store.append(plan, 'finding', {'finding': item['finding'], 'severity': 'MEDIUM',
        'files': [], 'proposedCorrection': item['correction']}, key + '-finding')
    if outcome:
        references = [{'kind': 'record', 'id': row['key'], 'digest': row['digest']}] if evidence else []
        store.append(plan, 'resolution', {'findingDigest': row['digest'], 'outcome': outcome,
                     'evidence': references}, key + '-resolution')
    return row


def evaluate(store, fixture):
    # Sample: Query a list with two expected results. Record both result ranks and the read time.
    store.open_plan(PLAN, PLAN)
    store.open_plan(OTHER, OTHER)
    labels = {add_finding(store, PLAN, item, 'FIXED')['key']: item['id']
              for item in fixture['findings']}
    example = fixture['findings'][0]
    add_finding(store, OTHER, example, 'FIXED')
    for label, outcome, evidence in [('unresolved', None, False), ('no-evidence', 'FIXED', False)]:
        decoy = dict(example, id=label, finding=example['finding'] + ' Case ' + label + '.')
        add_finding(store, PLAN, decoy, outcome, evidence)
    rejected = add_finding(store, PLAN, dict(example, id='superseded',
                           finding=example['finding'] + ' Case superseded.'), 'FIXED')
    store.append(PLAN, 'resolution', {'findingDigest': rejected['digest'], 'outcome': 'REJECTED',
                 'evidence': []}, 'reject-superseded')
    rows = []
    for item in fixture['queries']:
        start = perf_counter()
        matches = store.search_resolved_findings(PLAN, item['text'], limit=3)
        elapsed = (perf_counter() - start) * 1000
        actual = [labels.get(row['key'], 'unexpected') if row['plan_id'] == PLAN else 'unexpected' for row in matches]
        expected = set(item['expected'])
        recall = len(expected.intersection(actual)) / len(expected) if expected else None
        reciprocal = next((1 / rank for rank, name in enumerate(actual, 1) if name in expected), 0)
        rows.append({'case': item['id'], 'expected': item['expected'], 'actual': actual,
                     'challenge': item.get('challenge', False),
                     'recallAt3': recall, 'reciprocalRank': reciprocal, 'milliseconds': round(elapsed, 3)})
    positive = [row for row in rows if row['expected']]
    lexical = [row for row in positive if not row['challenge']]
    times = sorted(row['milliseconds'] for row in rows)
    return {'queries': rows, 'recallAt3': sum(row['recallAt3'] for row in positive) / len(positive),
            'meanReciprocalRank': sum(row['reciprocalRank'] for row in positive) / len(positive),
            'lexicalRecallAt3': sum(row['recallAt3'] for row in lexical) / len(lexical),
            'p95Milliseconds': times[math.ceil(.95 * len(times)) - 1],
            'negativePassed': all(not row['actual'] for row in rows if not row['expected']),
            'scopePassed': all('unexpected' not in row['actual'] for row in rows) and
                not ContextStore(DATABASE, 'durai_foreign').search_resolved_findings(PLAN, 'seat sequence')}


def delivery_proof(target):
    # Sample: Run one scripted seats task. Check that its delivery log has the finding and resolution.
    install(generic_pack('14'), DATABASE, binding=BINDING)
    installed = install(transaction_pack(generic_pack('14')), DATABASE, rebind=True)
    if installed['bindingRevision'] != REVISION:
        raise ValueError('Unexpected qualification binding revision')
    memory_options = {'contextPlanId': PLAN, 'contextMemoryQuery': 'seat sequence'}
    context, references = attempt_memory(memory_options, DATABASE, 'durai_runtime')
    if len(references) != 2 or 'seat event sequence' not in context:
        raise ValueError('Unexpected scoped memory')
    with patch('durai.qualification.fixture_spec', side_effect=lambda *args: dict(fixture_spec(*args), **memory_options)):
        result = run_fixture(FIXTURE, scripted, target.folder / 'memory-proof', DATABASE,
                             candidate_root=target.folder / 'candidates')
    events = replay(result['runId'], database=DATABASE)
    delivery = next(event['payload'] for event in events if event['kind'] == 'CONTEXT_VIEW_DELIVERED')
    audit = next(event['payload'] for event in events if event['kind'] == 'PROMPT_FACT_AUDIT')
    record = ContextStore(DATABASE).read(PLAN, delivery['recordKey'])
    if (result['outcome'] != 'PASSED' or audit['state'] != 'PASS' or
            any(reference not in record['value']['records'] for reference in references)):
        raise ValueError('Scripted result or delivery check failed')
    return {'outcome': result['outcome'], 'audit': audit['state'], 'recordReferences': references,
            'runId': result['runId'], 'deliveryDigest': delivery['recordDigest']}


def main():
    # Sample: Start a disposable PostgreSQL server. Record recall, rank, and scripted delivery.
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    fixture = json.loads(DATA.read_text(encoding='utf-8'))
    private = ROOT / 'private'
    private.mkdir(exist_ok=True)
    with EphemeralDatabase(private) as target:
        if target.port == 54410 or not target.name.startswith('durai-eval-db-'):
            raise ValueError('Refuse an operating database')
        docker('network', 'connect', 'bridge', target.name)
        target.wait()
        environment = {'DURAI_DB_HOST': '127.0.0.1', 'DURAI_DB_PORT': str(target.port),
                       'DURAI_SETTINGS': str(target.settings_path)}
        with patch.dict(os.environ, environment), use_server('127.0.0.1', target.port, target.settings_path):
            metrics = evaluate(ContextStore(DATABASE), fixture)
            delivery = delivery_proof(target)
    complete = metrics['lexicalRecallAt3'] == 1 and metrics['scopePassed'] and metrics['negativePassed']
    result = {'state': 'PASS' if complete else 'FAIL', 'fixtureDigest': digest(DATA.read_bytes()),
              'paidModelCalls': 0, 'isolated': True, 'delivery': delivery, **metrics}
    with args.output.open('x', encoding='utf-8', newline='\n') as output:
        output.write(json.dumps(result, indent=2) + '\n')
    print(json.dumps({key: result[key] for key in ('state', 'recallAt3', 'meanReciprocalRank', 'p95Milliseconds')}))
    return 0 if complete else 1


if __name__ == '__main__':
    raise SystemExit(main())
