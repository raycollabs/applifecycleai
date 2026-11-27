"""Sample: Hermes loads this folder. register(ctx) adds a pre_tool_call hook and a post_tool_call hook. A call to example.com:443 that is not on the egress list gets {"action": "block", "message": ...}. The hooks never return approve and never return modify."""
import json
import os
import threading
import time

DEADLINE_SECONDS = 5.0
OFF_VALUES = ('off', '0', 'false', 'no')
HOOKS = ('pre_tool_call', 'post_tool_call')
ENV_VARS = ('DURAI_GOVERNANCE', 'DURAI_EGRESS_TIER', 'DURAI_EGRESS_BOUNDARY', 'DURAI_GOVERNANCE_DB', 'DURAI_PROVER_CLI', 'DURAI_RECEIPT_FILE')
MAX_DECISIONS = 1000
MESSAGE_LIMIT = 300

STATE = {'port': None, 'decisions': {}}


def enabled():
    # Sample: DURAI_GOVERNANCE=off returns False. An unset variable returns True.
    return os.environ.get('DURAI_GOVERNANCE', 'on').strip().lower() not in OFF_VALUES


def receipt_sink():
    # Sample: DURAI_RECEIPT_FILE=C:/r/receipts.jsonl returns a function that appends one JSON line per receipt. An unset variable returns None.
    path = os.environ.get('DURAI_RECEIPT_FILE')
    if not path:
        return None

    def sink(receipt):
        with open(path, 'a', encoding='utf-8') as handle:
            handle.write(json.dumps(receipt, sort_keys=True) + '\n')
    return sink


def build_port():
    # Sample: Build a LocalGovernance from the DURAI_ variables with the receipt sink. A missing durai package raises ImportError.
    from durai.mcp_server import port_from_environment
    port = port_from_environment()
    port.sink = receipt_sink()
    return port


def get_port():
    # Sample: The first call builds the port. The next call returns the same object.
    if STATE['port'] is None:
        STATE['port'] = build_port()
    return STATE['port']


def set_port(port):
    # Sample: Set a fake port for a test. Set None to force a new build.
    STATE['port'] = port
    STATE['decisions'].clear()


def run_with_deadline(function, seconds):
    # Sample: A function that returns in 1 s with a 5 s deadline returns its value. A function that runs 10 s raises TimeoutError at 5 s. A function that raises passes the error on.
    box = {}

    def target():
        try:
            box['value'] = function()
        except BaseException as error:
            box['error'] = error
    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        raise TimeoutError('Governance check passed the deadline')
    if 'error' in box:
        raise box['error']
    return box['value']


def checked_args(args):
    # Sample: A dict, a list or a text stays as it is, so a URL inside a text argument is still checked. None or a number becomes an empty dict.
    return args if isinstance(args, (dict, list, tuple, str)) else {}


def block(message):
    # Sample: Message "Denied" returns {"action": "block", "message": "Durai governance: Denied"}. Long text is cut at 300 characters.
    return {'action': 'block', 'message': ('Durai governance: ' + str(message))[:MESSAGE_LIMIT]}


def remember(tool_call_id, decision):
    # Sample: Keep the decision of call C-1 so the post hook can add it to the receipt. Keep at most 1000 calls.
    if tool_call_id is None:
        return
    decisions = STATE['decisions']
    if len(decisions) >= MAX_DECISIONS:
        decisions.pop(next(iter(decisions)))
    decisions[tool_call_id] = decision


def pre_tool_call(tool_name=None, args=None, session_id=None, tool_call_id=None, **kwargs):
    # Sample: A listed host returns None, so Hermes runs the call. An unlisted host, a gate that waits, an error and a timeout each return a block dict. DURAI_GOVERNANCE=off returns None.
    if not enabled():
        return None
    try:
        port = get_port()
        verdict = run_with_deadline(lambda: port.check_tool_call(tool_name or '', checked_args(args), None, session_id, tool_call_id),
                                    DEADLINE_SECONDS)
        decision, reason = verdict['decision'], verdict['reason']
        remember(tool_call_id, decision)
        if decision == 'allow':
            return None
        if decision == 'gate':
            return block('A reviewer must approve this call (gate ' + str(verdict.get('workflow')) + '/' + str(verdict.get('subjectDigest')) + '). Retry after approval.')
        return block('Call denied (' + str(reason) + ').')
    except Exception:
        return block('Call denied (governance-error).')


def post_tool_call(tool_name=None, args=None, result=None, session_id=None, tool_call_id=None, **kwargs):
    # Sample: Record one receipt for a finished call. The receipt holds digests and no raw argument or result. An error inside the hook is dropped. DURAI_GOVERNANCE=off writes nothing.
    if not enabled():
        return None
    try:
        port = get_port()
        decision = STATE['decisions'].pop(tool_call_id, None)
        run_with_deadline(lambda: port.record_result(tool_name or '', checked_args(args), result, decision,
                                                     kwargs.get('model'), session_id, tool_call_id), DEADLINE_SECONDS)
    except Exception:
        return None
    return None


def register(ctx):
    # Sample: Hermes calls register(ctx). The plugin adds two hooks and no tool.
    ctx.register_hook('pre_tool_call', pre_tool_call)
    ctx.register_hook('post_tool_call', post_tool_call)
