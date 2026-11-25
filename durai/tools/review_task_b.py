"""Sample: Read the live Task B receipt at WAITING_FOR_REVIEW. Resume run R-1 with APPROVE. Record one approval event."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from langgraph.types import Command
from durai.candidate_store import CandidateStore
from durai.common import canonical, digest, Conflict
from durai.development_loop import CAPABILITY, build_loop, development_graph
from durai.execution_events import replay
from durai.providers import Providers
from durai.qualification import ROOT, FixtureSources
from durai.runtime_deepagents import DeepAgentsWorker
from durai.task_evaluator import SandboxEvaluator
from durai.task_ledger import TaskLedger


def write_review_receipt(receipt, state, attempt, decision, reviewer, events, root=ROOT):
    # Sample: Review package P-1 as U-1. Save its exact digest with releaseAuthority false.
    if decision not in ('APPROVE', 'REJECT') or not reviewer.strip():
        raise ValueError('Review requires a decision and reviewer')
    if state['task']['digest'] != receipt['taskDigest'] or state['package']['digest'] != receipt['package']['digest']:
        raise Conflict('Review candidate differs from the run receipt')
    body = {'runId': receipt['runId'], 'taskDigest': receipt['taskDigest'], 'attemptId': state['attempt_id'],
            'package': dict(receipt['package'], path=Path(receipt['package']['path']).name),
            'verdictDigest': state['verdict']['digest'], 'decision': decision, 'reviewer': reviewer,
            'outcome': state['outcome'], 'attemptState': attempt['state'], 'events': events,
            'releaseAuthority': False}
    record = dict(body, digest=digest(body))
    archive = Path(root) / 'private/reviews' / (record['digest'] + '.json')
    archive.parent.mkdir(parents=True, exist_ok=True)
    content = canonical(record)
    if archive.exists() and archive.read_bytes() != content:
        raise Conflict('Saved review receipt differs')
    if not archive.exists():
        with archive.open('xb') as output:
            output.write(content)
    public = Path(root) / 'receipts' / (Path(receipt.get('receiptName', 'dev-loop-task-b-live.json')).stem.removesuffix('-live') + '-review.json')
    public.parent.mkdir(parents=True, exist_ok=True)
    public.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    return public


def main():
    # Sample: Parse APPROVE and reviewer U-1. Resume the receipt run. Print WAITING_FOR_REVIEW and the approval event.
    parser = argparse.ArgumentParser()
    parser.add_argument('decision', choices=['APPROVE', 'REJECT'])
    parser.add_argument('--reviewer', required=True)
    parser.add_argument('--receipt', default='dev-loop-task-b-live.json')
    args = parser.parse_args()
    receipt = json.loads((ROOT / 'receipts' / args.receipt).read_text(encoding='utf-8'))
    receipt['receiptName'] = args.receipt
    if not args.reviewer.strip():
        raise ValueError('Reviewer must not be empty')
    durable_store = CandidateStore(ROOT / 'private/candidates')
    durable_store.restore(ROOT / 'private/candidates' / Path(receipt['package']['path']).name, receipt['package']['digest'])
    scratch = Path(tempfile.mkdtemp(prefix='durai-review-'))
    ledger = TaskLedger(receipt['database'])
    providers = Providers()
    providers.register(CAPABILITY, 'deepagents@' + DeepAgentsWorker.version,
                       lambda model, folder, attempt_id: DeepAgentsWorker(model, folder, attempt_id))
    builder = build_loop(ledger, providers, SandboxEvaluator(scratch), durable_store,
                         FixtureSources(receipt['database']), lambda attempt: None, scratch / 'work')
    with development_graph(builder, receipt['database']) as graph:
        config = {'configurable': {'thread_id': receipt['runId']}}
        saved = graph.get_state(config)
        prior = saved.values
        if not prior or prior.get('task', {}).get('digest') != receipt['taskDigest'] or prior.get('package', {}).get('digest') != receipt['package']['digest']:
            raise Conflict('Review receipt differs from the checkpoint candidate')
        if prior.get('decision'):
            if prior['decision'] != {'decision': args.decision, 'reviewer': args.reviewer}:
                raise Conflict('This run already has a different review decision')
            state = prior
        else:
            state = graph.invoke(Command(resume={'decision': args.decision, 'reviewer': args.reviewer}), config)
    events = [row['kind'] for row in replay(receipt['runId'], database=receipt['database'])]
    path = write_review_receipt(receipt, state, ledger.get(state['attempt_id']), args.decision, args.reviewer, events, root=ROOT)
    print(json.dumps({'outcome': state['outcome'], 'attemptState': ledger.get(state['attempt_id'])['state'], 'events': events, 'reviewReceipt': str(path)}))


if __name__ == '__main__':
    main()
