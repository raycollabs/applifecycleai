"""Sample: Test clustering failure receipts and gating prompt lesson approval on human reviews."""
import pytest
from durai.lesson_proposals import (
    cluster_failures,
    draft_proposal,
    approve_proposal,
)


class TestLessonProposals:
    def test_cluster_failures_by_stop_reason(self):
        # Sample: Receipts with STOP_STALL and STOP_BUDGET. Verify distinct clusters are generated.
        receipts = [
            {"sampleId": "S-TRADE-SCREENS-C1-5", "stopReason": "STOP_BUDGET", "lastRunTests": {"failed": 0}},
            {"sampleId": "S-TRADE-CASH", "stopReason": "STOP_STALL", "lastRunTests": {"failed": 0}},
            {"sampleId": "S-TRADE-BOOK", "stopReason": "STOP_COMPLETED", "lastRunTests": {"failed": 2}},
        ]
        clusters = cluster_failures(receipts)
        assert "STOP_BUDGET" in clusters
        assert "STOP_STALL" in clusters
        assert "TEST_FAILURES" in clusters

    def test_draft_proposal_generates_valid_schema(self):
        # Sample: Draft proposal for budget cluster. Verify ID format and initial PROPOSED status.
        runs = [{"sampleId": "S-TRADE-SCREENS-C1-5", "stopReason": "STOP_BUDGET"}]
        prop = draft_proposal(
            cluster_name="STOP_BUDGET",
            runs=runs,
            rule_text="Keep pinned workspace manifest to prevent file re-read loop.",
            enforcement="Build driver manifest injection at index 2."
        )
        assert prop["id"].startswith("L-PROP-")
        assert prop["status"] == "PROPOSED"
        assert prop["runCount"] == 1
        assert "S-TRADE-SCREENS-C1-5" in prop["affectedComponents"]

    def test_approve_proposal_refuses_agent_approval(self):
        # Sample: Attempt approval with an automated bot agent name. Verify PermissionError.
        runs = [{"sampleId": "S-TRADE-SCREENS-C1-5", "stopReason": "STOP_BUDGET"}]
        prop = draft_proposal("STOP_BUDGET", runs, "Rule text", "Enforcement text")
        with pytest.raises(PermissionError, match="not an authorized human reviewer"):
            approve_proposal(prop, reviewer="gemini-agent-auto")

    def test_approve_proposal_accepts_authorized_human(self):
        # Sample: Approve with operator username. Verify status transitions to APPROVED.
        runs = [{"sampleId": "S-TRADE-SCREENS-C1-5", "stopReason": "STOP_BUDGET"}]
        prop = draft_proposal("STOP_BUDGET", runs, "Rule text", "Enforcement text")
        approved = approve_proposal(prop, reviewer="operator", note="Confirmed fix for shrink re-read loop")
        assert approved["status"] == "APPROVED"
        assert approved["reviewer"] == "operator"
