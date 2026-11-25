"""Sample: Run T-1. Attempt 1 writes 199 and fails. Attempt 2 writes 200 and passes. Stop at review."""
from contextlib import contextmanager
import threading
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from .common import Conflict, digest
from .execution_budget import BudgetedModel
from .failure_triage import FailureTriage, TRIAGE_RULES, control_fault, resume_package, triage_procedure
from .runtime_adapter import invoke
from .scope_guard import unrequested_edits
from .task_contract import build_task, verify_task

CAPABILITY = 'coding-worker'
RESUME_NOTE = (' The workspace holds the files of your previous attempt. Keep the parts that work. '
               'Change only what the reasons above need.')
PROTECTED_FEEDBACK = 'A protected acceptance goal failed. Recheck every rule in the objective, including the valid cases it must accept.'


def setup_feedback(output):
    # Sample: Read a setup error with graph name G-1. Return a fixed error class and SQLSTATE without G-1.
    from .database_feedback import safe_database_feedback
    return safe_database_feedback(output, 'setup')


def database_error_feedback(output):
    # Sample: Read a candidate InvalidParameterValue error. Return its fixed parser diagnosis without hidden caller values.
    from .database_feedback import safe_database_feedback
    if '/workspace/durai/' not in output or 'psycopg.errors.' not in output:
        return None
    return safe_database_feedback(output, 'evaluation')


def recorded_error_feedback(result):
    # Sample: Read a unittest result with databaseErrorLines ['psycopg.errors.UndefinedObject: type "agtype" does not exist']. Return SQLSTATE 42704 feedback.
    from .database_feedback import safe_database_feedback
    lines = result.get('databaseErrorLines') or []
    return safe_database_feedback('\n'.join(lines) + '\n' + str(result.get('output') or ''), 'evaluation') if lines else None


def suite_goal_names(suite):
    # Sample: Read a protected suite with test_two_lots and helper make_shares. Return {'test_two_lots'}.
    import ast
    names = set()
    for path, content in suite.items():
        if path.endswith('.py'):
            tree = ast.parse(content.decode('utf-8') if isinstance(content, bytes) else content)
            names.update(node.name for node in ast.walk(tree)
                         if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('test_'))
    return names


class ExternalRuns:
    def __init__(self):
        # Sample: Keep the shared workspace and lease triple of run R-1 until the run ends.
        self.lock, self.runs = threading.Lock(), {}

    def put(self, run_id, workspace, leases):
        # Sample: Store workspace W-1 and leases (L-1, W-1, M-1) under run R-1.
        with self.lock:
            self.runs[run_id] = {'workspace': workspace, 'leases': leases}

    def get(self, run_id):
        # Sample: Read run R-1. Return its workspace and leases, or None when R-1 has none.
        with self.lock:
            return self.runs.get(run_id)

    def drop(self, run_id):
        # Sample: Remove run R-1 after it ends. Ignore an unknown run.
        with self.lock:
            self.runs.pop(run_id, None)


class LoopState(TypedDict, total=False):
    run_id: str
    spec: dict
    current: dict
    task: dict
    attempt_id: str
    generation: int
    package: dict | None
    verdict: dict
    history: list
    outcome: str
    detail: str
    decision: dict
    budget_id: str
    stage: str
    control: str | None
    evaluator_retries: int
    triage: dict
    stop: dict
    resume_package: dict | None
    external: bool


def build_loop(ledger, providers, evaluator, store, sources, model_factory, scratch, advisor=None, external=None):
    # Sample: Connect ledger L-1, worker registry P-1, evaluator E-1, and store S-1. Return a graph builder. With external runs X-1, a run marked external passes its shared workspace and leases to the worker.
    def feedback(state):
        # Sample: Read attempt 1 with finding "module docstring changed". Return the objective and that finding for attempt 2.
        notes = [entry['feedback'] for entry in state.get('history', []) if entry.get('feedback')]
        if not notes:
            return state['task']['objective']
        return state['task']['objective'] + ' Previous attempts were rejected for these reasons: ' + ' | '.join(notes)

    def advise(state, task, attempt_id, files):
        # Sample: Advise attempt A-1 at PLAN. Record ADVICE_DELIVERED with 2 steps. Return the advice text for the prompt.
        from . import db, execution_events
        result = advisor.advise(state['run_id'], task, files, state.get('history') or [])
        if result is None:
            return ''
        payload = {key: result.get(key) for key in ('turn', 'state', 'reason', 'briefDigest', 'selectionDigest', 'patternIds',
                                                    'digest', 'briefCharacters')}
        payload.update(attemptId=attempt_id, profileId=(result.get('profile') or {}).get('id'), dataOnly=True)
        if result['state'] == 'ADVICE':
            payload['advice'] = result['advice']
        with db.transaction(ledger.role, ledger.database) as cursor:
            execution_events.record(cursor, state['run_id'], 'ADVICE_DELIVERED' if result['state'] == 'ADVICE' else 'ADVICE_SKIPPED',
                                    payload)
        return '\n\n' + result['text'] if result['state'] == 'ADVICE' else ''

    def owner(state):
        # Sample: Name the owner of run R-1. Return loop:R-1.
        return 'loop:' + state['run_id']

    def readiness(state):
        # Sample: Build T-1 without failingAcceptanceGoal. Return NEEDS_CLARIFICATION and no attempt.
        built = build_task(state['spec'])
        if built['state'] != 'READY':
            return {'outcome': 'NEEDS_CLARIFICATION', 'detail': ', '.join(built['missing'])}
        try:
            verify_task(built['task'], state['current'])
        except Conflict as error:
            return {'outcome': 'STALE', 'detail': str(error)}
        if hasattr(sources, 'policies'):
            try:
                sources.policies(built['task'])
            except (Conflict, KeyError, ValueError) as error:
                return {'task': built['task'], 'outcome': 'BLOCKED', 'detail': str(error)}
        if hasattr(sources, 'suite'):
            from .error_contract import enforced, unnamed_errors
            missing = unnamed_errors(built['task']['objective'], sources.suite(built['task']))
            if missing and enforced(built['task'], ledger.database, ledger.role):
                return {'task': built['task'], 'outcome': 'NEEDS_CLARIFICATION',
                        'detail': 'Name the expected error contract in the objective: ' + ', '.join(missing)}
        return {'task': built['task'], 'history': [], 'outcome': 'READY'}

    def implement(state):
        # Sample: Create attempt 2 for T-1. Claim it. Run the pinned deepagents worker. Retain its candidate.
        task = state['task']
        shared = None
        if state.get('external'):
            shared = external.get(state['run_id']) if external is not None else None
            if shared is None:
                return {'outcome': 'BLOCKED', 'detail': 'LEASE_REQUIRED: the shared workspace and leases of this run are unavailable',
                        'package': None, 'stage': 'worker'}
        try:
            attempt = ledger.create_attempt(task, state['run_id'], state['current'])
        except Conflict as error:
            return {'outcome': 'BUDGET_EXHAUSTED', 'detail': str(error), 'package': None, 'stage': 'worker',
                    'control': 'BUDGET'}
        generation = ledger.claim(attempt['id'], owner(state), seconds=task['budget']['seconds'])
        update = {'attempt_id': attempt['id'], 'generation': generation, 'package': None, 'budget_id': attempt['budget_id'],
                  'stage': 'worker', 'control': None, 'evaluator_retries': 0}

        def worker_policies(attempt_id):
            # Sample: Read view V-1 with policy P-1 and digest D-1 for attempt A-1. Record POLICY_VIEW_DELIVERED with D-1. Return P-1 with id P-1.
            from . import db, execution_events
            if not hasattr(sources, 'policies'):
                return []
            view = sources.policies(task)
            if not isinstance(view, dict):
                return view
            payload = {'attemptId': attempt_id, 'bindingId': view['bindingId'], 'audience': view['audience'],
                       'policyViewDigest': view['digest'], 'policyIds': [item['policyId'] for item in view['policies']]}
            with db.transaction(ledger.role, ledger.database) as cursor:
                execution_events.record(cursor, state['run_id'], 'POLICY_VIEW_DELIVERED', payload)
            return [dict(item, id=item['policyId']) for item in view['policies']]

        resumed = state.get('resume_package')
        start = store.restore(**resumed) if resumed else sources.files(task)
        if resumed:
            from . import db, execution_events
            with db.transaction(ledger.role, ledger.database) as cursor:
                execution_events.record(cursor, state['run_id'], 'ATTEMPT_RESUMED',
                                        {'attemptId': attempt['id'], 'package': resumed})
        try:
            from .context_delivery import attempt_memory, record_attempt_context, session_view
            from .context_pack import build_pack, context_procedure
            from .efficiency_metrics import record_efficiency
            from .workspace_manifest import visible
            procedure = context_procedure(task, ledger.database, ledger.role)
            goals = [name for name in [(task.get('readiness') or {}).get('failingAcceptanceGoal')] +
                     ((state['history'][-1].get('goals') or []) if state['history'] else []) if name]
            readable = visible(task, start)
            quality = task.get('promptQuality')
            guidance = ''
            if quality:
                from .advisor_select import framework_patterns
                from .prompt_quality import blueprint_guidance
                try:
                    framework = framework_patterns(quality['binding'], quality['profile'], ledger.database, ledger.role,
                                                   audience='coding-worker', revision=quality['revision'])
                    guidance = blueprint_guidance(task, readable, framework)
                except (Conflict, KeyError, ValueError) as error:
                    ledger.move(attempt['id'], owner(state), generation, 'FAILED', {'error': 'Worker blueprint unavailable'})
                    return dict(update, outcome='BLOCKED', detail='Prompt quality: worker blueprint unavailable: ' + str(error),
                                stop={'choice': 'STOP_FIXTURE', 'reasonCode': 'PROMPT_BLUEPRINT_UNAVAILABLE', 'origin': 'fixture'})
            memory, memory_records = attempt_memory(task, ledger.database, ledger.role) if procedure else ('', [])
            session, session_record = session_view(task, ledger.database, ledger.role) if procedure else ('', None)
            pack = build_pack(task, readable, procedure['maxCharacters'], goals, guidance, memory, session) if procedure else None
            context = {'text': pack['text'], 'index': pack['index'], 'tools': procedure['tools'], 'digest': pack['digest'],
                       'notes': pack['notes'], 'characters': pack['characters']} if pack else None
            record_attempt_context(task, attempt['id'], state['run_id'], ledger.database, ledger.role, pack, memory_records, session_record)
            policies = worker_policies(attempt['id'])
            if quality:
                from . import db, execution_events
                from .prompt_quality import audit_prompt
                from .runtime_deepagents import worker_prompt
                audit = audit_prompt(task, worker_prompt(task, readable, policies, context), sources.suite(task))
                if not pack or pack['characters'] > procedure['maxCharacters']:
                    audit['state'] = 'FAIL'
                    audit['missing'].append('valid-context-pack')
                with db.transaction(ledger.role, ledger.database) as cursor:
                    execution_events.record(cursor, state['run_id'], 'PROMPT_FACT_AUDIT',
                                            dict(audit, attemptId=attempt['id'], bindingDigest=framework['bindingDigest']))
                if audit['state'] != 'PASS':
                    detail = 'Prompt fact audit failed: ' + ', '.join(audit['missing'] or ['protected-source-exposure'])
                    ledger.move(attempt['id'], owner(state), generation, 'FAILED', {'error': detail})
                    return dict(update, outcome='BLOCKED', detail=detail,
                                stop={'choice': 'STOP_FIXTURE', 'reasonCode': 'PROMPT_FACTS_MISSING', 'origin': 'fixture',
                                      'missing': audit['missing'], 'protectedLineMatches': audit['protectedLineMatches']})
            advice = advise(state, task, attempt['id'], readable) if advisor is not None else ''
            with providers.resolve(CAPABILITY, attempt['runtime'] + '@' + attempt['runtime_version']) as factory:
                model = BudgetedModel(inner=model_factory(attempt['attempt']), ledger=ledger, budget_id=attempt['budget_id'],
                                      attempt_id=attempt['id'], owner=owner(state), generation=generation,
                                      lease_seconds=task['budget']['seconds'], limit=task['budget']['modelCalls'])
                worker = factory(model, scratch, attempt['id'], **(shared or {}))
                result = invoke(worker, 'execute', task, start, prompt=feedback(state) + advice + (RESUME_NOTE if resumed else ''),
                                policies=policies, **({'context': context} if context else {}))
                record_efficiency(worker, task, attempt['id'], state['run_id'], ledger.database, ledger.role,
                                  pack['digest'] if pack else None, pack['characters'] if pack else None)
            if result.get('stoppedBy'):
                from . import db, execution_events
                with db.transaction(ledger.role, ledger.database) as cursor:
                    execution_events.record(cursor, state['run_id'], 'ATTEMPT_BUDGET_STOPPED',
                                            {'attemptId': attempt['id'], 'reason': result['stoppedBy'],
                                             'changed': result['candidate']['changed']})
        except Conflict as error:
            if ledger.get(attempt['id'])['state'] == 'RUNNING':
                ledger.move(attempt['id'], owner(state), generation, 'FAILED',
                            dict({'error': str(error)}, **({'requestedTool': error.requestedTool} if hasattr(error, 'requestedTool') else {})))
            history = state['history'] + [{'attemptId': attempt['id'], 'verdict': 'FAIL', 'error': str(error),
                                           'feedback': 'The worker run stopped: ' + str(error)}]
            return dict(update, history=history, outcome='ATTEMPT_FAILED', detail=str(error), control=control_fault(error))
        if result.get('state') != 'CANDIDATE':
            raise Conflict('Worker returned no candidate: ' + str(result.get('state')))
        return dict(update, package=store.retain(attempt['id'], task, result['files']), outcome='CANDIDATE')

    def evaluate(state):
        # Sample: Restore candidate package D-1. Run suite H-1. Record FAIL for 199 shares and keep the package and the RUNNING attempt.
        from .task_evaluator import StalePin
        task = state['task']
        files = store.restore(**state['package'])
        findings = unrequested_edits(task, sources.files(task), files)
        goals = []
        if findings:
            body = {'verdict': 'FAIL', 'results': [{'command': 'scope-guard', 'state': 'FAIL', 'output': '; '.join(findings)}],
                    'taskDigest': task['digest']}
            verdict = dict(body, digest=digest(body))
            note = 'Unrequested edit: ' + '; '.join(findings)
        else:
            try:
                verdict = evaluator.evaluate(task, files, sources.suite(task), baseline=sources.files(task))
            except StalePin as error:
                return {'stage': 'evaluation', 'control': 'STALE', 'detail': str(error), 'outcome': 'ATTEMPT_FAILED'}
            comment = next((item for item in verdict['results'] if item['command'] == 'comment-policy' and item['state'] == 'FAIL'), None)
            setup = next((item for item in verdict['results'] if item['command'] == 'database-setup' and item['state'] == 'FAIL'), None)
            unit = next((item for item in verdict['results'] if item['command'] == 'python-unittest' and item['state'] == 'FAIL'), None)
            raised = (database_error_feedback(unit['output']) or recorded_error_feedback(unit)) if unit else None
            note = None if verdict['verdict'] == 'PASS' else (setup_feedback(setup['output']) if setup else
                                                              'Comment rule: ' + comment['output'] if comment else
                                                              raised or PROTECTED_FEEDBACK)
            goals = []
            if unit and not setup and note is not None:
                try:
                    named = triage_procedure(task, ledger.database, ledger.role).get('feedbackGoalNames')
                except Conflict:
                    named = False
                if named:
                    allowed = suite_goal_names(sources.suite(task))
                    goals = [name for name in unit.get('failedGoals') or [] if name in allowed]
            if goals:
                from . import db, execution_events
                note = note + ' Failing protected goals: ' + ', '.join(goals) + '.'
                with db.transaction(ledger.role, ledger.database) as cursor:
                    execution_events.record(cursor, state['run_id'], 'FEEDBACK_GOALS_DELIVERED',
                                            {'attemptId': state['attempt_id'], 'goals': goals})
        entry = {'attemptId': state['attempt_id'], 'verdict': verdict['verdict'], 'verdictDigest': verdict['digest'],
                 'package': state['package'], 'feedback': note, 'goals': goals}
        history = [item for item in state['history'] if item.get('attemptId') != state['attempt_id']] + [entry]
        return {'verdict': verdict, 'history': history, 'stage': 'evaluation', 'control': None,
                'outcome': 'PASSED' if verdict['verdict'] == 'PASS' else 'ATTEMPT_FAILED'}

    def triage(state):
        # Sample: Read FAIL for 199 shares with 1 of 2 attempts and 18 of 20 calls left. Record DECISION_RECORDED. Return RETRY_CANDIDATE.
        from . import db, execution_events
        task, attempt_id, control = state['task'], state.get('attempt_id'), state.get('control')
        running = bool(attempt_id) and ledger.get(attempt_id)['state'] == 'RUNNING'
        if running:
            try:
                ledger.renew(attempt_id, owner(state), state['generation'], task['budget']['seconds'])
            except Conflict:
                control, running = 'OWNERSHIP', False
        usage = ledger.usage(state['budget_id']) if state.get('budget_id') else {}
        try:
            procedure = triage_procedure(task, ledger.database, ledger.role)
        except Conflict:
            procedure, control = {'rules': TRIAGE_RULES, 'evaluatorRetryLimit': 0, 'minimumRetryCalls': 1, 'resumeReasons': [],
                                  'source': 'local'}, 'STALE'
        facts = {'verdict': state.get('verdict') if state.get('stage') == 'evaluation' and control is None else None,
                 'controlFault': control,
                 'remainingAttempts': task['budget']['attempts'] - (usage.get('attempts') or 0),
                 'remainingCalls': task['budget']['modelCalls'] - (usage.get('modelCalls') or 0),
                 'remainingSeconds': task['budget']['seconds'], 'evaluatorRetries': state.get('evaluator_retries', 0),
                 'packageDigest': (state.get('package') or {}).get('digest'), 'taskDigest': task['digest']}

        def emit(kind, payload):
            # Sample: Record DECISION_RECORDED for attempt A-1 and package P-1 with procedure source pinned.
            with db.transaction(ledger.role, ledger.database) as cursor:
                execution_events.record(cursor, state['run_id'], kind,
                                        dict(payload, attemptId=attempt_id, package=state.get('package'),
                                             procedureSource=procedure['source']))
        decision = FailureTriage(procedure['rules'], emit, procedure['evaluatorRetryLimit'],
                                 procedure.get('minimumRetryCalls', 1)).decide(facts)
        if decision['choice'] == 'REPEAT_EVALUATION':
            return {'triage': decision, 'evaluator_retries': state.get('evaluator_retries', 0) + 1}
        if running and decision['choice'] != 'SUBMIT_FOR_REVIEW':
            ledger.move(attempt_id, owner(state), state['generation'], 'FAILED',
                        {'verdictDigest': (state.get('verdict') or {}).get('digest') if facts['verdict'] else None,
                         'package': state.get('package'), 'triage': decision['choice'], 'reasonCode': decision['reasonCode']})
        return {'triage': decision, 'resume_package': resume_package(decision, procedure, state.get('package'))}

    def route_ready(state):
        # Sample: Read outcome READY. Return implement. Read NEEDS_CLARIFICATION. Return end.
        return 'implement' if state['outcome'] == 'READY' else END

    def route_candidate(state):
        # Sample: Read outcome CANDIDATE. Return evaluate. Read a stopped worker. Return triage.
        return {'CANDIDATE': 'evaluate', 'BLOCKED': END}.get(state['outcome'], 'triage')

    def route_triage(state):
        # Sample: Read SUBMIT_FOR_REVIEW, REPEAT_EVALUATION, RETRY_CANDIDATE, and STOP_FIXTURE. Return submit, evaluate, implement, and stop.
        return {'SUBMIT_FOR_REVIEW': 'submit', 'REPEAT_EVALUATION': 'evaluate',
                'RETRY_CANDIDATE': 'implement'}.get(state['triage']['choice'], 'stop')

    def submit(state):
        # Sample: Move attempt 2 from RUNNING to WAITING_FOR_REVIEW once. Record the verdict and package digests.
        ledger.move(state['attempt_id'], owner(state), state['generation'], 'WAITING_FOR_REVIEW',
                    {'verdictDigest': state['verdict']['digest'], 'package': state['package']})
        return {'outcome': 'PASSED'}

    def review(state):
        # Sample: Pause attempt 2 for reviewer decision APPROVE or REJECT. Repeat the pause on a restart.
        decision = interrupt({'attemptId': state['attempt_id'], 'package': state['package'],
                              'verdictDigest': state['verdict']['digest'], 'taskDigest': state['task']['digest']})
        if not isinstance(decision, dict) or decision.get('decision') not in ('APPROVE', 'REJECT'):
            raise Conflict('Review decision must be APPROVE or REJECT')
        if decision['decision'] == 'REJECT':
            ledger.review(state['attempt_id'], 'REJECT', {'reviewer': decision.get('reviewer'), 'package': state['package']})
            return {'decision': decision, 'outcome': 'REJECTED'}
        ledger.review(state['attempt_id'], 'APPROVE', {'reviewer': decision.get('reviewer'), 'package': state['package']})
        return {'decision': decision, 'outcome': 'WAITING_FOR_REVIEW'}

    def stop(state):
        # Sample: Read two failed attempts for T-1 and STOP_BUDGET. Return FAILED with stop STOP_BUDGET and reason CANDIDATE_ASSERTION.
        found = state.get('triage') or {}
        return {'outcome': 'BUDGET_EXHAUSTED' if state['outcome'] == 'BUDGET_EXHAUSTED' else 'FAILED',
                'stop': {'choice': found.get('choice'), 'reasonCode': found.get('reasonCode'), 'origin': found.get('origin'),
                         'reasons': found.get('reasons'), 'ruleDigest': found.get('ruleDigest')}}

    builder = StateGraph(LoopState)
    for name, action in [('readiness', readiness), ('implement', implement), ('evaluate', evaluate), ('triage', triage),
                         ('submit', submit), ('review', review), ('stop', stop)]:
        builder.add_node(name, action)
    builder.add_edge(START, 'readiness')
    builder.add_conditional_edges('readiness', route_ready, ['implement', END])
    builder.add_conditional_edges('implement', route_candidate, ['evaluate', 'triage', END])
    builder.add_edge('evaluate', 'triage')
    builder.add_conditional_edges('triage', route_triage, ['submit', 'evaluate', 'implement', 'stop'])
    builder.add_edge('submit', 'review')
    builder.add_edge('review', END)
    builder.add_edge('stop', END)
    return builder


@contextmanager
def development_graph(builder, database='durai_local'):
    # Sample: Compile the loop with PostgresSaver in durai_checkpoints on durai_test. Yield the graph.
    from langgraph.checkpoint.postgres import PostgresSaver
    from .db import connect
    with connect('durai_runtime', database, autocommit=True) as connection:
        connection.execute('SET search_path=durai_checkpoints')
        yield builder.compile(checkpointer=PostgresSaver(connection))
