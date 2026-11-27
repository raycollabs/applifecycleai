"""Sample: A host agent asks check_tool_call for a call to example.com:443. The port answers deny with reason egress-not-listed. An agent has no way to approve a gate through this port."""
from typing import Protocol

ALLOW, DENY, GATE = 'allow', 'deny', 'gate'
REASONS = ('no-egress', 'egress-listed', 'gate-approved', 'egress-not-listed', 'egress-ceiling-exceeded', 'egress-ceiling-error',
           'egress-host-invalid', 'gate-waiting', 'gate-cancelled', 'workflow-required', 'governance-error')


class GovernancePort(Protocol):
    def check_tool_call(self, tool_name: str, args: dict, workflow_id: str | None = None, session_id: str | None = None,
                        tool_call_id: str | None = None) -> dict:
        # Sample: Tool fetch with url https://api.deepinfra.com/v1 returns {"decision": "allow", "reason": "egress-listed"}. A url on example.com returns decision deny and reason egress-not-listed. Any internal error returns deny with reason governance-error.
        ...

    def record_result(self, tool_name: str, args: dict, result: object, decision: str | None = None, model: str | None = None,
                      session_id: str | None = None, tool_call_id: str | None = None) -> dict:
        # Sample: Record the result of tool fetch with model Beam-1.0-501B-A23B. Return a receipt with receiptId, digest, argsDigest, resultDigest and model. The receipt holds no raw argument and no raw result.
        ...

    def open_gate(self, workflow_id: str, entry: str, deadline: float | None = None) -> dict:
        # Sample: Open a gate for example.com:443 in workflow W-1. Return the subject digest and the question for a reviewer. A host outside the operator boundary raises before any gate opens.
        ...

    def gate_state(self, workflow_id: str, subject_digest: str) -> str:
        # Sample: Read the gate of W-1 for subject D-1. Return WAITING, APPROVED, CANCELLED or NOT_OPEN. Only a durai_reviewer approval makes it APPROVED.
        ...

    def egress_check(self, entry: str) -> dict:
        # Sample: Entry api.deepinfra.com:443 returns listed true. Entry example.com:443 returns listed false and withinBoundary true or false when a boundary exists, else null.
        ...
