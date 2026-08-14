from __future__ import annotations

import unittest
from dataclasses import replace

from mergewave.simulator import DeliveryObservation, MergeWaveSimulator


def work_items() -> list[dict[str, object]]:
    return [
        {"id": "A", "blocked_by": []},
        {"id": "B", "blocked_by": []},
        {"id": "C", "blocked_by": []},
        {"id": "D", "blocked_by": ["A", "B", "C"]},
    ]


def valid_delivery(item_id: str, base_revision: str = "main-0") -> DeliveryObservation:
    head_sha = f"{item_id.lower()}-1"
    return DeliveryObservation(
        repository="demo-repository",
        worktree_path=f"/worktrees/{item_id}",
        branch_ref=f"mergewave/{item_id}",
        base_revision=base_revision,
        initial_head_revision=base_revision,
        current_head_revision=head_sha,
        pr_head_sha=head_sha,
        ci_head_sha=head_sha,
        ci_passed=True,
        approvals=1,
        scope_ok=True,
        merged=True,
        merge_revision=f"main-{item_id.lower()}",
        base_is_ancestor=True,
    )


class MergeWaveSimulatorAcceptanceTests(unittest.TestCase):
    def test_wave_barrier_dispatches_roots_from_one_base_and_holds_dependent(self) -> None:
        simulator = MergeWaveSimulator(
            work_items(), policy="wave_barrier", base_revision="main-0"
        )

        dispatches = simulator.dispatch_ready()

        self.assertEqual({dispatch.work_item_id for dispatch in dispatches}, {"A", "B", "C"})
        self.assertEqual({dispatch.base_revision for dispatch in dispatches}, {"main-0"})
        self.assertNotIn("D", {dispatch.work_item_id for dispatch in dispatches})

    def test_agent_claim_does_not_release_items_and_fresh_main_releases_next_wave(self) -> None:
        simulator = MergeWaveSimulator(
            work_items(), policy="wave_barrier", base_revision="main-0"
        )
        simulator.dispatch_ready()

        for item_id in ("A", "B", "C"):
            simulator.record_agent_claim(item_id, "completed")

        self.assertEqual(simulator.gate_status("A"), "pending")
        self.assertNotIn("D", {dispatch.work_item_id for dispatch in simulator.dispatch_ready()})

        for item_id in ("A", "B", "C"):
            simulator.observe_delivery(item_id, valid_delivery(item_id))
            self.assertEqual(simulator.evaluate_gate(item_id).status, "approved")

        simulator.refresh_target_base("main-3")
        next_wave = simulator.dispatch_ready()

        self.assertEqual([(dispatch.work_item_id, dispatch.base_revision) for dispatch in next_wave], [("D", "main-3")])

    def test_normal_agent_commit_advances_head_without_workspace_drift(self) -> None:
        simulator = MergeWaveSimulator(
            work_items(), policy="continuous_frontier", base_revision="main-0"
        )
        simulator.dispatch_ready()
        simulator.observe_delivery("A", valid_delivery("A"))

        decision = simulator.evaluate_gate("A")

        self.assertEqual(decision.status, "approved")
        self.assertIsNone(decision.failure)

    def test_wrong_repository_worktree_or_branch_is_workspace_drift(self) -> None:
        simulator = MergeWaveSimulator(
            work_items(), policy="continuous_frontier", base_revision="main-0"
        )
        simulator.dispatch_ready()
        simulator.observe_delivery("A", replace(valid_delivery("A"), branch_ref="other/A"))

        decision = simulator.evaluate_gate("A")

        self.assertEqual(decision.status, "blocked")
        self.assertEqual(decision.failure.code, "workspace_drift")

    def test_workspace_created_after_a_reset_is_workspace_drift(self) -> None:
        simulator = MergeWaveSimulator(
            work_items(), policy="continuous_frontier", base_revision="main-0"
        )
        simulator.dispatch_ready()
        simulator.observe_delivery(
            "A", replace(valid_delivery("A"), initial_head_revision="unexpected-head")
        )

        decision = simulator.evaluate_gate("A")

        self.assertEqual(decision.status, "blocked")
        self.assertEqual(decision.failure.code, "workspace_drift")

    def test_unrelated_pull_request_history_is_base_revision_mismatch(self) -> None:
        simulator = MergeWaveSimulator(
            work_items(), policy="continuous_frontier", base_revision="main-0"
        )
        simulator.dispatch_ready()
        observation = valid_delivery("A")
        observation = replace(observation, base_is_ancestor=False)
        simulator.observe_delivery("A", observation)

        decision = simulator.evaluate_gate("A")

        self.assertEqual(decision.status, "blocked")
        self.assertEqual(decision.failure.code, "base_revision_mismatch")

    def test_stale_ci_explains_why_delivery_is_blocked(self) -> None:
        simulator = MergeWaveSimulator(
            work_items(), policy="continuous_frontier", base_revision="main-0"
        )
        simulator.dispatch_ready()
        observation = valid_delivery("A")
        observation = replace(observation, ci_head_sha="old-head")
        simulator.observe_delivery("A", observation)

        decision = simulator.evaluate_gate("A")

        self.assertEqual(decision.status, "blocked")
        self.assertEqual(decision.failure.code, "stale_ci")
        self.assertTrue(decision.failure.agent_guidance)
        self.assertTrue(decision.failure.suggested_action)

    def test_simulator_exposes_a_trace_of_delivery_decisions(self) -> None:
        simulator = MergeWaveSimulator(
            work_items(), policy="continuous_frontier", base_revision="main-0"
        )
        simulator.dispatch_ready()
        simulator.observe_delivery("A", valid_delivery("A"))
        simulator.evaluate_gate("A")

        trace_kinds = [event.kind for event in simulator.trace()]

        self.assertEqual(
            trace_kinds,
            [
                "dispatch.created",
                "dispatch.created",
                "dispatch.created",
                "delivery.observed",
                "gate.decided",
            ],
        )


if __name__ == "__main__":
    unittest.main()
