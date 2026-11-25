"""Sample: Read a trace with 6 read calls, 1 refused write, and 1 edit. Return callsBeforeFirstWrite 7 and refusedWrites 1."""
import json
from pathlib import Path

READ_TOOLS = frozenset({'read_file', 'ls', 'glob', 'grep'})
WRITE_TOOLS = frozenset({'write_file', 'edit_file'})
QUERY_TOOLS = frozenset({'describe_table', 'find_function', 'show_lines', 'search_context'})


def load_trace(path):
    # Sample: Read trace file A-1.json with 41 messages. Return its body.
    return json.loads(Path(path).read_text(encoding='utf-8'))


def trace_metrics(trace):
    # Sample: Read AI call 1 with two read_file calls and AI call 2 with one successful edit_file. Return modelCalls 2 and callsBeforeFirstWrite 1.
    messages = trace['messages']
    status = {m.get('tool_call_id'): m.get('status') for m in messages if m.get('role') == 'tool'}
    calls = [m for m in messages if m.get('role') == 'ai']
    result = {'modelCalls': len(calls), 'toolCalls': 0, 'readCalls': 0, 'queryCalls': 0, 'editCalls': 0,
              'refusedWrites': 0, 'otherCalls': 0, 'callsBeforeFirstWrite': None, 'callsBeforeFirstWriteAttempt': None,
              'inputCount': 0, 'outputCount': 0, 'reasoningCount': 0, 'firstCallInputCount': None,
              'stagesAccepted': [], 'stageCount': 0, 'callsPerStage': {}, 'resubmissions': 0, 'reworkCalls': 0,
              'costEnterprise': 0.0, 'costList': 0.0}
    files = set()
    stages = []
    stage_call_counts = {}
    current_stage = 'START'
    stage_call_counts[current_stage] = 0

    for index, message in enumerate(calls):
        usage = message.get('completion') or {}
        result['inputCount'] += usage.get('inputCount', 0)
        result['outputCount'] += usage.get('outputCount', 0)
        result['reasoningCount'] += usage.get('reasoningCount', 0)
        if index == 0:
            result['firstCallInputCount'] = usage.get('inputCount')
        for call in message.get('tool_calls') or []:
            name, args = call.get('name'), call.get('args') or {}
            result['toolCalls'] += 1
            if name in READ_TOOLS:
                result['readCalls'] += 1
                if name == 'read_file' and args.get('file_path'):
                    files.add(args['file_path'])
            elif name in QUERY_TOOLS:
                result['queryCalls'] += 1
            elif name in WRITE_TOOLS:
                if result['callsBeforeFirstWriteAttempt'] is None:
                    result['callsBeforeFirstWriteAttempt'] = index
                tool_st = status.get(call.get('id'))
                if tool_st == 'success' or (tool_st is None and name in WRITE_TOOLS):
                    result['editCalls'] += 1
                    if result['callsBeforeFirstWrite'] is None:
                        result['callsBeforeFirstWrite'] = index
                else:
                    result['refusedWrites'] += 1
                    result['reworkCalls'] += 1
            elif name == 'submit_stage':
                stage_name = args.get('stage')
                tool_st = status.get(call.get('id'))
                if tool_st == 'success' or tool_st is None:
                    if stage_name in stages:
                        result['resubmissions'] += 1
                        result['reworkCalls'] += 1
                    else:
                        stages.append(stage_name)
                        stage_call_counts[stage_name] = index + 1
                        current_stage = stage_name
                else:
                    result['resubmissions'] += 1
                    result['reworkCalls'] += 1
            else:
                result['otherCalls'] += 1
    result['distinctFilesRead'] = len(files)
    result['stagesAccepted'] = stages
    result['stageCount'] = len(stages)
    result['callsPerStage'] = stage_call_counts
    result['costEnterprise'] = round(result['inputCount'] * (2.0 / 1e6) + result['outputCount'] * (10.0 / 1e6), 6)
    result['costList'] = round(result['inputCount'] * (0.14 / 1e6) + result['outputCount'] * (0.28 / 1e6), 6)
    return result


def pack_cost(metrics, pack_characters):
    # Sample: Read 4 model calls and a 1000-character pack. Return packCharacters 1000 and packCallCharacters 4000.
    return {'packCharacters': pack_characters,
            'packCallCharacters': pack_characters * metrics['modelCalls'] if pack_characters is not None else None}


def record_efficiency(worker, task, attempt_id, run_id, database, role, pack_digest=None, pack_characters=None):
    # Sample: Export the A-1 trace with 6 model calls. Record WORKER_EFFICIENCY with modelCalls 6 and pack digest D-1.
    from .db import transaction
    from .execution_events import record
    from .runtime_adapter import invoke
    evidence = invoke(worker, 'export_evidence', task)
    retained = evidence.get('workerEvidence') if isinstance(evidence, dict) else None
    if not retained:
        return None
    metrics = trace_metrics(load_trace(retained['path']))
    payload = {'attemptId': attempt_id, 'traceDigest': retained['digest'], 'packDigest': pack_digest,
               'metrics': dict(metrics, **pack_cost(metrics, pack_characters))}
    with transaction(role, database) as db:
        record(db, run_id, 'WORKER_EFFICIENCY', payload)
    return payload
