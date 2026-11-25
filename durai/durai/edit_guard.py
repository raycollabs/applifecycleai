"""Sample: Check complete proposed text before a file write. Refuse the third equal invalid proposal with STALLED."""
from difflib import SequenceMatcher
from pathlib import PurePosixPath
from threading import RLock
from deepagents.backends.utils import perform_string_replacement
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from .common import Conflict, digest
from .comment_delta import comment_findings, sql_comment_findings
from .decision_points import DecisionPoint, EDIT_RULES
from .runtime_adapter import check_candidate
from .scope_guard import unrequested_edits
from .workspace_manifest import capture, visible


class WorkerStalled(Conflict):
    state = 'STALLED'


def refusal_text(error):
    # Sample: Read SyntaxError at line 12 offset 7 with text "def f(:". Return "SyntaxError line 12 offset 7: def f(:".
    if not isinstance(error, SyntaxError):
        return 'Proposal text or file is invalid: ' + type(error).__name__
    shown = (error.text or '').strip()[:120]
    return 'Proposal text or file is invalid: SyntaxError line ' + str(error.lineno) + ' offset ' + str(error.offset) + ': ' + shown


def match_refusal(source, old):
    # Sample: File has "return None" on lines 8 and 20. Old text "return None" returns "2 matches at lines 8, 20" and "replace_all". Old text "def hold_seat(seat_id, order_id):" with 0 matches returns the closest line "12: def hold_seat(order_id, seat_id):".
    lines = source.split('\n')
    count = source.count(old) if old else 0
    if count > 1:
        starts, at = [], source.find(old)
        while at >= 0 and len(starts) < 5:
            starts.append(source.count('\n', 0, at) + 1)
            at = source.find(old, at + len(old))
        return ('Edit match is ambiguous: ' + str(count) + ' matches at lines ' + ', '.join(str(item) for item in starts) +
                '; add more context to old_string or set replace_all')
    wanted = [line.strip() for line in old.split('\n') if line.strip()][:3]
    scored = {}
    for index, line in enumerate(lines):
        for target in wanted:
            ratio = SequenceMatcher(None, target, line.strip()).ratio()
            if ratio >= 0.6 and ratio > scored.get(index, 0):
                scored[index] = ratio
    best = sorted(sorted(scored, key=lambda index: (-scored[index], index))[:8])
    shown = '; '.join(str(index + 1) + ': ' + lines[index][:160] for index in best)
    return ('Edit match is absent: 0 matches' + ('; closest lines ' + shown if shown else '') +
            '; Read the file again and copy old_string exactly')


class EditGuardMiddleware(AgentMiddleware):
    def __init__(self, task, baseline, root, emit, rules=None, previous=None, leases=None):
        # Sample: Load task T-1, sealed source B-1, and the last refusal count 2 for proposal P-1. With leases (FileLeases, W-1, M-1), check a live lease of M-1 before each write.
        super().__init__()
        self.task, self.baseline, self.root, self.leases = task, baseline, root, leases
        self.lock, self.last, self.count, self.findings = RLock(), None, 0, []
        self.stalled = bool(previous and previous.get('choice') == 'STALLED')
        self.point = DecisionPoint('edit-guard', EDIT_RULES if rules is None else rules, emit)
        if previous and previous['ruleDigest'] == self.point.rule_digest:
            features = previous['features']
            self.last, self.count = features.get('proposalDigest'), features.get('refusalCount', 0)

    def proposal(self, call):
        # Sample: Replace one return value in a.py. Refuse ../outside.py and a symlink before opening the file.
        args = call['args']
        path = args.get('file_path')
        if (not isinstance(path, str) or not path.startswith('/workspace/') or '\\' in path or
                '..' in path.split('/') or str(PurePosixPath(path)) != path or path not in self.task['writePaths'] or
                path not in visible(self.task, {path: b''})):
            raise Conflict('permission denied: proposal path is outside the declared write scope')
        target = self.root
        for segment in PurePosixPath(path).parts[1:]:
            target = target / segment
            if target.is_symlink():
                raise Conflict('Proposal path contains a symlink')
        if not target.resolve().is_relative_to(self.root.resolve()):
            raise Conflict('Proposal path escapes the workspace')
        if call['name'] == 'write_file':
            if target.exists():
                raise Conflict('Use edit_file for an existing file')
            text = args.get('content')
            if not isinstance(text, str):
                raise Conflict('File content must be text')
        else:
            if not target.is_file():
                raise Conflict('Edit target is not a file')
            old, new = args.get('old_string'), args.get('new_string')
            if not isinstance(old, str) or not isinstance(new, str) or type(args.get('replace_all', False)) is not bool:
                raise Conflict('Edit strings and replace_all are invalid')
            old, new = old.replace('\r\n', '\n').replace('\r', '\n'), new.replace('\r\n', '\n').replace('\r', '\n')
            replaced = perform_string_replacement(target.read_text(encoding='utf-8'), old, new, args.get('replace_all', False))
            if isinstance(replaced, str):
                raise Conflict(match_refusal(target.read_text(encoding='utf-8'), old))
            text = replaced[0]
        proposed = capture(self.root)
        proposed[path] = text.encode('utf-8')
        check_candidate(self.task, self.baseline, proposed)
        findings = unrequested_edits(self.task, self.baseline, proposed)
        if path.endswith('.py'):
            findings += comment_findings(self.baseline.get(path), proposed[path])
        elif path.endswith('.sql'):
            findings += sql_comment_findings(self.baseline.get(path), proposed[path])
        else:
            findings.append('No comment checker for this file type')
        return findings

    def lease_findings(self, call):
        # Sample: Member M-1 writes journal.py with no lease. Return LEASE_REQUIRED. After expiry return LEASE_LOST. With a live lease return nothing.
        if self.leases is None:
            return []
        store, worktree, owner = self.leases
        path = call['args'].get('file_path')
        if not isinstance(path, str):
            return []
        verdict = store.check(worktree, path, owner)
        if verdict['status'] == 'LIVE':
            return []
        if verdict['status'] == 'LOST':
            return ['LEASE_LOST: the lease of ' + owner + ' on ' + path + ' expired; claim it again before writing']
        holder = verdict['heldBy']
        return ['LEASE_REQUIRED: ' + owner + ' holds no live lease on ' + path + (' (held by ' + holder + ')' if holder else '')]

    def wrap_tool_call(self, request, handler):
        # Sample: Refuse P-1 three times. Return two tool errors, then raise STALLED. Never call the file handler.
        call = request.tool_call
        if call['name'] not in ('write_file', 'edit_file'):
            return handler(request)
        with self.lock:
            if self.stalled:
                raise WorkerStalled('STALLED: this worker has stopped after three equal refused edits')
            try:
                findings = self.lease_findings(call) or self.proposal(call)
            except (Conflict, SyntaxError, UnicodeError, OSError, ValueError) as error:
                findings = [str(error) if isinstance(error, Conflict) else refusal_text(error)]
            identity = digest({'name': call['name'], 'args': call['args'], 'findings': findings})
            count = self.count + 1 if self.last == identity else 1
            features = {'proposalDigest': identity, 'hasFindings': bool(findings),
                        'repeatLimitReached': bool(findings) and count >= 3, 'refusalCount': count if findings else 0}
            decision = self.point.decide(features)
            self.findings = findings
            if findings:
                self.last, self.count = identity, count
                if decision['choice'] == 'STALLED':
                    self.stalled = True
                    raise WorkerStalled('STALLED: three equal refused edits: ' + '; '.join(findings))
                return ToolMessage(content='Error: Edit refused: ' + '; '.join(findings), status='error',
                                   tool_call_id=call['id'], name=call['name'])
            if decision['choice'] != 'APPLIED':
                raise Conflict('Pinned edit guard does not permit the valid proposal')
            self.last, self.count = None, 0
            return handler(request)
