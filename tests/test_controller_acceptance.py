from __future__ import annotations

import unittest

from mergewave.controller import DeliveryController
from mergewave.contracts import WorkItemState
from mergewave.runtime import AgentEvent, RunHandle, RunSpec
from mergewave.runtime import classify_runtime_event
from mergewave.simulator import DeliveryObservation, MergeWaveSimulator
from mergewave.git_workspace import Workspace


class FakeTracker:
    def __init__(self) -> None:
        self.transitions: list[tuple[str, str]] = []
        self.linked = True
        self.acceptance_signal = "unknown"

    def transition_state(self, item_id: str, state: str) -> None:
        self.transitions.append((item_id, state))

    def fetch_candidates(self):
        return ()

    def link_pull_request(self, item_id: str, url: str) -> None:
        pass

    def post_comment(self, item_id: str, body: str) -> None:
        pass

    def pull_request_linked(self, item_id: str, url: str) -> bool:
        return self.linked

    def acceptance_criteria_signal(self, item_id: str) -> str:
        return self.acceptance_signal


class FakeBaseRevisionProvider:
    def __init__(self, revision: str) -> None:
        self.revision = revision
        self.reads = 0

    def current_revision(self) -> str:
        self.reads += 1
        return self.revision


class FakeWorkspaceFactory:
    def __init__(self) -> None:
        self.created: list[Workspace] = []

    def create(self, item_id: str, base_revision: str) -> Workspace:
        workspace = Workspace(
            workspace_id=item_id,
            repository="demo-repository",
            worktree_path=f"/worktrees/{item_id}",
            branch_ref=f"mergewave/{item_id}",
            base_revision=base_revision,
            initial_head_revision=base_revision,
            current_head_revision=base_revision,
        )
        self.created.append(workspace)
        return workspace

    def inspect(self, workspace: Workspace) -> Workspace:
        return workspace


class FakeRuntime:
    def __init__(self) -> None:
        self.specs: list[RunSpec] = []

    def start(self, spec: RunSpec) -> RunHandle:
        self.specs.append(spec)
        return RunHandle(spec.run_id, object())

    def stream(self, handle: RunHandle):
        return iter((AgentEvent("runtime.exited", {"returncode": 0}),))

    def cancel(self, handle: RunHandle) -> AgentEvent:
        return AgentEvent("runtime.cancelled", {})


class FakeObserver:
    def __init__(self, observations: dict[str, DeliveryObservation]) -> None:
        self.observations = observations

    def observe(self, item_id: str, workspace: Workspace) -> DeliveryObservation:
        return self.observations[item_id]


class FakeRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def __getattr__(self, name: str):
        def record(**kwargs: object) -> None:
            self.calls.append((name, kwargs))

        return record


class DeliveryControllerAcceptanceTests(unittest.TestCase):
    def test_missing_prompt_is_rejected_before_scheduler_state_changes(self) -> None:
        simulator = MergeWaveSimulator(
            [{"id": "CTRL-1", "blocked_by": []}],
            policy="continuous_frontier",
            base_revision="main-0",
        )
        controller = DeliveryController(
            simulator=simulator,
            tracker=FakeTracker(),
            workspace_factory=FakeWorkspaceFactory(),
            runtime=FakeRuntime(),
            observer=FakeObserver({}),
        )

        with self.assertRaisesRegex(ValueError, "missing prompts"):
            controller.dispatch_ready({})

        self.assertEqual(tuple(item.work_item_id for item in controller.dispatch_ready({"CTRL-1": "prompt"})), ("CTRL-1",))

    def test_merge_releases_next_item_from_the_new_target_base(self) -> None:
        simulator = MergeWaveSimulator(
            [{"id": "CTRL-1", "blocked_by": []}, {"id": "CTRL-2", "blocked_by": ["CTRL-1"]}],
            policy="wave_barrier",
            base_revision="main-0",
        )
        tracker = FakeTracker()
        workspaces = FakeWorkspaceFactory()
        runtime = FakeRuntime()
        controller = DeliveryController(
            simulator=simulator,
            tracker=tracker,
            workspace_factory=workspaces,
            runtime=runtime,
            observer=FakeObserver(
                {
                    "CTRL-1": DeliveryObservation(
                        "demo-repository", "/worktrees/CTRL-1", "mergewave/CTRL-1",
                        "main-0", "main-0", "commit-1", "commit-1", "commit-1",
                        True, 1, True, True, "main-1", True,
                        pr_url="https://github.com/acme/demo/pull/1",
                    )
                }
            ),
        )

        first = controller.dispatch_ready({"CTRL-1": "Implement first"})
        decision = controller.reconcile("CTRL-1")

        self.assertEqual(tuple(item.work_item_id for item in first), ("CTRL-1",))
        self.assertEqual(decision.status, "approved")
        self.assertEqual(
            tracker.transitions,
            [("CTRL-1", WorkItemState.IN_PROGRESS.value), ("CTRL-1", WorkItemState.IN_REVIEW.value), ("CTRL-1", WorkItemState.DONE.value)],
        )

        controller.refresh_target_base("main-1")
        second = controller.dispatch_ready({"CTRL-2": "Implement second"})

        self.assertEqual(tuple(item.work_item_id for item in second), ("CTRL-2",))
        self.assertEqual(workspaces.created[-1].base_revision, "main-1")
        self.assertEqual(runtime.specs[-1].prompt, "Implement second")

    def test_controller_emits_arp_run_gate_evidence_and_decision(self) -> None:
        simulator = MergeWaveSimulator(
            [{"id": "CTRL-1", "blocked_by": []}],
            policy="continuous_frontier",
            base_revision="main-0",
        )
        recorder = FakeRecorder()
        controller = DeliveryController(
            simulator=simulator,
            tracker=FakeTracker(),
            workspace_factory=FakeWorkspaceFactory(),
            runtime=FakeRuntime(),
            observer=FakeObserver(
                {
                    "CTRL-1": DeliveryObservation(
                        "demo-repository", "/worktrees/CTRL-1", "mergewave/CTRL-1",
                        "main-0", "main-0", "commit-1", "commit-1", "commit-1",
                        True, 1, True, True, "main-1", True,
                        pr_url="https://github.com/acme/demo/pull/1",
                    )
                }
            ),
            recorder=recorder,
        )

        controller.dispatch_ready({"CTRL-1": "Implement first"})
        decision = controller.reconcile("CTRL-1")

        self.assertEqual(decision.status, "approved")
        call_names = [name for name, _ in recorder.calls]
        self.assertEqual(call_names, ["record_run", "record_gate_request", "record_evidence", "record_evidence", "record_gate_decision"])
        gate_decision = recorder.calls[-1][1]
        self.assertEqual(gate_decision["decision_authority"], "human")
        self.assertEqual(gate_decision["decision"], "approved")

    def test_runtime_timeout_routes_item_to_needs_attention(self) -> None:
        simulator = MergeWaveSimulator([{"id": "CTRL-1", "blocked_by": []}], policy="continuous_frontier", base_revision="main-0")
        tracker = FakeTracker()
        controller = DeliveryController(
            simulator=simulator,
            tracker=tracker,
            workspace_factory=FakeWorkspaceFactory(),
            runtime=FakeRuntime(),
            observer=FakeObserver({}),
        )
        controller.dispatch_ready({"CTRL-1": "Implement"})

        failure = controller.observe_runtime_events("CTRL-1", [AgentEvent("runtime.timeout", {"timeout_seconds": 1})])

        self.assertEqual(failure.code, "agent_timeout")
        self.assertEqual(tracker.transitions[-1], ("CTRL-1", WorkItemState.NEEDS_ATTENTION.value))

    def test_unlinked_pull_request_routes_item_to_needs_attention(self) -> None:
        simulator = MergeWaveSimulator([{"id": "CTRL-1", "blocked_by": []}], policy="continuous_frontier", base_revision="main-0")
        tracker = FakeTracker()
        tracker.linked = False
        controller = DeliveryController(
            simulator=simulator,
            tracker=tracker,
            workspace_factory=FakeWorkspaceFactory(),
            runtime=FakeRuntime(),
            observer=FakeObserver({"CTRL-1": replace_delivery(linked_to_ticket=False)}),
        )

        controller.dispatch_ready({"CTRL-1": "Implement"})
        decision = controller.reconcile("CTRL-1")

        self.assertEqual(decision.failure.code, "pull_request_unlinked")
        self.assertEqual(tracker.transitions[-1], ("CTRL-1", WorkItemState.NEEDS_ATTENTION.value))

    def test_scope_violation_is_a_visible_warning_not_a_hard_block(self) -> None:
        simulator = MergeWaveSimulator([{"id": "CTRL-1", "blocked_by": []}], policy="continuous_frontier", base_revision="main-0")
        controller = DeliveryController(
            simulator=simulator,
            tracker=FakeTracker(),
            workspace_factory=FakeWorkspaceFactory(),
            runtime=FakeRuntime(),
            observer=FakeObserver({"CTRL-1": replace_delivery(scope_ok=False)}),
        )

        controller.dispatch_ready({"CTRL-1": "Implement"})
        decision = controller.reconcile("CTRL-1")

        self.assertEqual(decision.status, "approved")
        self.assertEqual(decision.warnings[0].code, "out_of_scope_diff")

    def test_approved_delivery_refreshes_base_from_provider(self) -> None:
        simulator = MergeWaveSimulator([{"id": "CTRL-1", "blocked_by": []}], policy="continuous_frontier", base_revision="main-0")
        provider = FakeBaseRevisionProvider("main-1")
        controller = DeliveryController(
            simulator=simulator,
            tracker=FakeTracker(),
            workspace_factory=FakeWorkspaceFactory(),
            runtime=FakeRuntime(),
            observer=FakeObserver({"CTRL-1": replace_delivery()}),
            base_revision_provider=provider,
        )

        controller.dispatch_ready({"CTRL-1": "Implement"})
        controller.reconcile("CTRL-1")

        self.assertEqual(provider.reads, 1)
        self.assertEqual(controller._simulator.preview_ready(), ())

    def test_controller_persists_attempt_and_wave_contracts(self) -> None:
        simulator = MergeWaveSimulator([{"id": "CTRL-1", "blocked_by": []}], policy="continuous_frontier", base_revision="main-0")
        controller = DeliveryController(
            simulator=simulator,
            tracker=FakeTracker(),
            workspace_factory=FakeWorkspaceFactory(),
            runtime=FakeRuntime(),
            observer=FakeObserver({"CTRL-1": replace_delivery()}),
        )

        controller.dispatch_ready({"CTRL-1": "Implement"})

        self.assertEqual(controller.work_attempt("CTRL-1").state, "running")
        self.assertEqual(controller.execution_wave().work_item_ids, ("CTRL-1",))
        controller.reconcile("CTRL-1")
        self.assertEqual(controller.work_attempt("CTRL-1").state, "released")

    def test_controller_dispatches_the_next_frontier_after_fresh_base_observation(self) -> None:
        simulator = MergeWaveSimulator(
            [{"id": "CTRL-1", "blocked_by": []}, {"id": "CTRL-2", "blocked_by": ["CTRL-1"]}],
            policy="continuous_frontier",
            base_revision="main-0",
        )
        workspaces = FakeWorkspaceFactory()
        controller = DeliveryController(
            simulator=simulator,
            tracker=FakeTracker(),
            workspace_factory=workspaces,
            runtime=FakeRuntime(),
            observer=FakeObserver({"CTRL-1": replace_delivery()}),
            base_revision_provider=FakeBaseRevisionProvider("main-1"),
        )

        controller.dispatch_ready({"CTRL-1": "First", "CTRL-2": "Second"})
        controller.reconcile("CTRL-1")

        self.assertEqual(controller.active_assignment("CTRL-2").dispatch.base_revision, "main-1")


def replace_delivery(**changes: object) -> DeliveryObservation:
    from dataclasses import replace

    base = DeliveryObservation(
        "demo-repository", "/worktrees/CTRL-1", "mergewave/CTRL-1",
        "main-0", "main-0", "commit-1", "commit-1", "commit-1",
        True, 1, True, True, "main-1", True,
        pr_url="https://github.com/acme/demo/pull/1",
    )
    return replace(base, **changes)


if __name__ == "__main__":
    unittest.main()
