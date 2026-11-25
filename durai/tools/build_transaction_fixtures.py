"""Sample: Read 11 protected transaction contracts. Write 22 v14 test task templates with shells, includes, and source digests."""
import ast
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from durai.common import digest

FIXTURE = ROOT / 'qualification/phase4'
TEMPLATES = ROOT / 'templates'
MODULES = ('__init__', 'common', 'db', 'idempotency', 'outbox', 'inbox', 'reservations', 'inventory',
           'payment_intents', 'payment_funds', 'settlement', 'journal', 'job_progress', 'coordination',
           'object_download', 'location_search')
SCHEMAS = ('idempotency/v1.sql', 'idempotency/reference.sql', 'events/v1.sql', 'events/reference.sql',
           'reservations/v1.sql', 'inventory/v1.sql', 'payments/v1.sql', 'payments/v2.sql', 'transactions/v1.sql')
CONTRACT_NOTE = ' Read /workspace/public-contract.md.'
ERROR_NOTES = {
    'idempotency': 'Raise durai.common.Conflict when a request key is reused with a changed request body.',
    'events': 'settle raises durai.common.Conflict for an expired or replaced lease token.',
    'seats': 'change_hold raises durai.common.Conflict for a stale revision or a terminal hold.',
    'inventory': 'change_hold raises durai.common.Conflict for a stale revision or a write outside its deadline.',
    'jobs': 'report_progress and the coordinated commit raise durai.common.Conflict for a stale owner, lease, or generation, also after cancel_job.',
    'journal': 'post_journal raises durai.common.Conflict for lines that do not sum to zero. reverse_journal raises durai.common.Conflict for a second reversal.',
    'downloads': 'download raises KeyError when the subject has no grant for the object in the current tenant, and ValueError for a duration outside 1 to 300 seconds.',
}
OUTPUT_NOTES = {
    'idempotency': 'execute_request returns {outcome, replayed}: outcome is the handler result, and replayed is False for the '
                   'first call and True for a retry with the same key and body.',
    'seats': 'hold_seat returns {outcome: {result: HELD, order, event}}, or {outcome: {result: SEAT_UNAVAILABLE}} when the seat '
             'already has an active hold (no exception). order has orderId. change_hold returns {outcome: {result, order, event}}.',
    'inventory': 'hold_units returns {outcome: {result: HELD, hold, event}}, or {outcome: {result: INSUFFICIENT_STOCK}} when '
                 'available stock is too low (no exception). change_hold returns {outcome: {result, hold, event}}.',
    'funds': 'register_authorization returns {outcome: {paymentId}}. create_operation returns {outcome: {result: READY, operation, '
             'event}} with operation.operationId, or {outcome: {result: INSUFFICIENT_REFUND_BALANCE}} (no exception). '
             'dispatch and reconcile return the operation with its state. The provider has submit(operation) for dispatch and '
             'lookup(operation) for reconcile. Each returns a proof or None and can raise TimeoutError or ConnectionError.',
    'location': 'nearby returns rows with the keys location_id, latitude, longitude, and distance_meters.',
    'settlement': 'reconcile_statement returns {statementId, account, findings}. Each finding has kind, operationId, '
                  'state (MATCHED, MISSING, UNEXPECTED, or MISMATCH), local, and statement.',
    'events': 'inbox.consume gives the handler the full event envelope (eventId, applicationId, payload), not the payload alone. '
              'consume returns the execute_request result {outcome, replayed}: replayed is False on the first delivery and True on a duplicate.',
}
INPUT_NOTES = {
    'idempotency': 'payload is a JSON object (dict with string keys). The handler is called with two positional '
                   'arguments as handler(db, body): db is the active connection, body is the request body dict, and it returns a JSON object (dict).',
    'events': 'settle takes one claim as returned by claim_batch (a dict with event, leaseToken, and attempt) and a bool. '
              'consume takes an event envelope dict with exactly the keys format, applicationId, eventId, streamId, sequence, '
              'eventType, and payload, and a handler called with two positional arguments as handler(db, body): db is the active connection and body is the '
              'envelope; it returns a JSON object (dict).',
    'charge': 'The provider has charge(intent) for dispatch_charge and lookup(intent) for reconcile, each called with one positional argument. intent is the operation '
              'document (dict) with applicationId, operationId, orderId, amountMinor, currency, state, revision, and providerReference. '
              'Each returns a proof (dict) or None and can raise TimeoutError or ConnectionError. Save a proof with record_result.',
    'settlement': 'operations is a list of (kind, operationId) pairs with kind CHARGE, CAPTURE, or REFUND. statement is a list of '
                  'lines (dicts) with exactly the keys kind, operationId, currency, amountMinor, feeMinor, netMinor, and '
                  'providerReference (integer minor units, 3-letter currency). amountMinor is above 0 and feeMinor is 0 or more. netMinor = '
                  'amountMinor - feeMinor for CHARGE and CAPTURE, and netMinor = -amountMinor - feeMinor for REFUND. A line that '
                  'breaks this rule raises ValueError (it is not a finding).',
    'journal': 'lines is a list of 2 to 100 dicts with exactly the keys account (text) and signedMinor (nonzero integer minor units).',
    'downloads': 'signer is a callable. Call it with exactly two positional arguments, signer(storage_key, ttl_seconds), and no keyword arguments. It returns an HTTPS URL string.',
    'funds': 'A CAPTURE amount_minor can be equal to or lower than the authorized amount, never higher (it need not equal it). A payment has one capture operation. The provider methods submit(operation) and lookup(operation) each take one positional argument.',
}
CONTRACT_APPENDIX = ('\n## Input fields\n\n' + ''.join('- ' + INPUT_NOTES[name] + '\n' for name in sorted(INPUT_NOTES)) +
                     '\n## Output fields\n\n' + ''.join('- ' + OUTPUT_NOTES[name] + '\n' for name in sorted(OUTPUT_NOTES)))
STUB ="    raise NotImplementedError('Complete the public task')\n"
BINDING = {'id': 'foundation-generic-v14', 'version': '14'}
BUDGET = {'modelCalls': 40, 'toolCalls': 50, 'seconds': 900, 'attempts': 2}
VARIANTS = (('', True), ('-plain', False))


def make_shell(source, symbols):
    # Sample: Read journal.py with post_journal and reverse_journal. Replace both bodies with the stub. Keep every other line.
    text = source.decode('utf-8')
    lines = text.splitlines(keepends=True)
    nodes = [node for node in ast.parse(text).body if isinstance(node, ast.FunctionDef) and node.name in symbols]
    if len(nodes) != len(symbols):
        raise ValueError('A requested function is unavailable')
    for node in sorted(nodes, key=lambda node: node.lineno, reverse=True):
        lines[node.body[0].lineno - 1:node.end_lineno] = [STUB]
    return ''.join(lines).encode('utf-8')


def folder_name(case, suffix):
    # Sample: Read case journal and suffix -plain. Return task-tx-journal-v14-plain.
    return 'task-tx-' + case['id'] + '-v14' + suffix


def protected_include(case):
    # Sample: Read case journal. Return support.py, protected/journal.py as test_protected.py, and seeds 00 to 08 and 99.
    paths = {'tests/support.py': 'qualification/phase4/protected/support.py',
             'tests/test_protected.py': 'qualification/phase4/protected/' + case['id'] + '.py'}
    paths.update({'tests/seed/' + str(index).zfill(2) + '.sql': 'blueprints/' + name for index, name in enumerate(SCHEMAS)})
    paths['tests/seed/99.sql'] = 'qualification/phase4/protected/seed.sql'
    return paths


def task_config(case, suffix, contract):
    # Sample: Read case events with contract. Return prefix TASK-TX-EVENTS-V14, outbox.py first, and both write paths.
    scope = {'/workspace/durai/' + module + '.py': list(symbols) for module, symbols in case['scope'].items()}
    scoped = {module for module in case['scope']}
    include = ['durai/' + module + '.py' for module in MODULES if module not in scoped] + ['blueprints/' + name for name in SCHEMAS]
    sources = sorted(['durai/' + module + '.py' for module in MODULES] + ['blueprints/' + name for name in SCHEMAS] +
                     list(protected_include(case).values()) + ['qualification/phase4/contracts.json'] +
                     (['qualification/phase4/public-contract.md'] if contract else []))
    return {'taskPrefix': 'TASK-TX-' + case['id'].upper() + '-V14' + suffix.upper(), 'kind': 'FEATURE',
            'module': next(iter(scope)), 'writePaths': list(scope), 'editScope': scope, 'include': include,
            'database': True, 'failingAcceptanceGoal': case['goal'], 'budget': dict(BUDGET),
            'objective': case['objective'] + (' ' + ERROR_NOTES[case['id']] if case['id'] in ERROR_NOTES else '') +
                         (' ' + OUTPUT_NOTES[case['id']] if case['id'] in OUTPUT_NOTES else '') +
                         (' ' + INPUT_NOTES[case['id']] if case['id'] in INPUT_NOTES else '') +
                         (CONTRACT_NOTE if contract else ''), 'binding': dict(BINDING),
            'case': case['id'], 'variant': 'contract' if contract else 'plain',
            'protectedInclude': protected_include(case),
            'sources': {path: digest((ROOT / path).read_bytes()) for path in sources}}


def write_template(case, suffix, contract):
    # Sample: Write task-tx-journal-v14. Return its folder with task.json, the journal shell, the contract, and an empty tests/__init__.py.
    folder = TEMPLATES / folder_name(case, suffix)
    if folder.exists():
        shutil.rmtree(folder)
    for module, symbols in case['scope'].items():
        target = folder / 'workspace/durai' / (module + '.py')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(make_shell((ROOT / 'durai' / (module + '.py')).read_bytes(), symbols))
    if contract:
        (folder / 'workspace/public-contract.md').write_bytes((FIXTURE / 'public-contract.md').read_bytes() +
                                                              CONTRACT_APPENDIX.encode('utf-8'))
    (folder / 'protected/tests').mkdir(parents=True)
    (folder / 'protected/tests/__init__.py').write_bytes(b'')
    (folder / 'task.json').write_text(json.dumps(task_config(case, suffix, contract), indent=2) + '\n', encoding='utf-8', newline='\n')
    return folder


def main():
    # Sample: Write 22 folders for 11 cases. Print each folder name.
    for case in json.loads((FIXTURE / 'contracts.json').read_text(encoding='utf-8')):
        for suffix, contract in VARIANTS:
            print(write_template(case, suffix, contract).name)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
