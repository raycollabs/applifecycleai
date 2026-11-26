"""Sample: An agent proposes api.github.com:443. Open a gate. Only a durai_reviewer approval makes it allowed. A runtime approval is ignored."""
from .common import digest
from .egress_policy import parse_allow
from .egress_presets import gate_question, with_rules

APPROVED = 'APPROVED'


class EgressGate:
    def __init__(self, gates=None, database='durai_local', step_id='egress', ceiling=None):
        # Sample: Use durai.workflow_gates on database durai_test. Tests pass a fake with the same three functions.
        if gates is None:
            from . import workflow_gates as gates
        self.gates, self.database, self.step_id, self.ceiling = gates, database, step_id, ceiling

    def propose(self, workflow_id, entry, deadline):
        # Sample: Propose api.github.com:443 for workflow W-1 with deadline T+3600. Return the subject digest and the question. Propose it again. Return the same digest.
        entry = parse_allow(entry)
        if self.ceiling is not None:
            self.ceiling.require(entry)
        subject = digest(entry)
        self.gates.propose_subject(workflow_id, self.step_id, 'before', subject, self.database)
        gate = self.gates.open_gate(workflow_id, self.step_id, subject, gate_question(entry), deadline, self.database)
        return {'entry': entry, 'subjectDigest': subject, 'question': gate['question']}

    def state(self, workflow_id, proposal):
        # Sample: Return WAITING, APPROVED or CANCELLED for the proposal. The gate module reads only durai_reviewer decisions.
        return self.gates.gate_state(workflow_id, self.step_id, proposal['subjectDigest'], self.database)

    def apply(self, workflow_id, body, proposals):
        # Sample: Two proposals, one APPROVED and one WAITING. Return a new body of revision +1 that holds only the approved rule. With none approved return the same body.
        approved = [proposal for proposal in proposals if self.state(workflow_id, proposal) == APPROVED]
        if not approved:
            return body
        rules = [('rule-' + proposal['subjectDigest'][:10], proposal['entry']) for proposal in approved]
        return with_rules(body, rules)
