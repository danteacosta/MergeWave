from __future__ import annotations

from datetime import datetime, timezone
import unittest

from mergewave.contracts import WorkItemState, validate_work_item
from mergewave.domain import (
    ExecutionWave,
    GateStatus,
    HumanGate,
    PullRequest,
    ValidationEvidence,
    WorkAttempt,
)


class DomainContractTests(unittest.TestCase):
    def test_work_item_preserves_state_and_delivery_contract_fields(self) -> None:
        raw = {
            "id": "CTRL-1",
            "title": "Ship the delivery contract",
            "problem": "Operators cannot safely release dependent work without verified state.",
            "scope": {"in": ["Add state"], "out": ["Build a dashboard"]},
            "behavior": "The controller exposes a stable state machine for every work item.",
            "technical_context": {"summary": "Core contract", "modules": ["contracts"], "constraints": []},
            "affected_paths": ["src/mergewave"],
            "acceptance_criteria": [{"id": "AC-1", "criterion": "State is explicit."}],
            "test_scenarios": [{"id": "SC-1", "given": "A ready item", "when": "Dispatched", "then": "It is in progress."}],
            "blocked_by": [],
            "estimate_points": 3,
            "risk": {"level": "low", "reason": "Local change"},
            "rollout": {"strategy": "Immediate", "kill_switch": "Disable"},
            "observability": {"events": [], "metrics": []},
            "state": "Ready",
        }

        work_item = validate_work_item(raw)

        self.assertEqual(work_item.state, WorkItemState.READY)
        self.assertEqual(work_item.acceptance_criteria[0]["id"], "AC-1")

    def test_delivery_entities_are_explicit_and_arp_neutral(self) -> None:
        now = datetime.now(timezone.utc)
        attempt = WorkAttempt("attempt-1", "CTRL-1", "base-1", "ws-1", "acp", now, "running")
        wave = ExecutionWave("wave-1", "base-1", ("CTRL-1", "CTRL-2"), "Open")
        pull_request = PullRequest(
            "pr-1", "CTRL-1", "https://github.com/acme/repo/pull/1", "head-1",
            "base-1", "passing", "head-1", True, True, "merge-1",
        )
        evidence = ValidationEvidence(
            "CTRL-1", True, True, True, True, "pass", "partial", now,
        )
        gate = HumanGate("CTRL-1", True, True, "reviewer", now)

        self.assertEqual(attempt.state, "running")
        self.assertEqual(wave.state, "Open")
        self.assertEqual(pull_request.base_sha_at_open, "base-1")
        self.assertEqual(evidence.scope_check, "pass")
        self.assertEqual(gate.status, GateStatus.SATISFIED)


if __name__ == "__main__":
    unittest.main()
