from __future__ import annotations

import unittest

from mergewave.controller import DeliveryController
from mergewave.runtime import AgentEvent, RunHandle, RunSpec
from mergewave.simulator import DeliveryObservation, MergeWaveSimulator
from mergewave.git_workspace import Workspace


class FakeTracker:
    def __init__(self) -> None:
        self.transitions: list[tuple[str, str]] = []

    def transition_state(self, item_id: str, state: str) -> None:
        self.transitions.append((item_id, state))

    def fetch_candidates(self):
        return ()

    def link_pull_request(self, item_id: str, url: str) -> None:
        pass

    def post_comment(self, item_id: str, body: str) -> None:
        pass


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
                    )
                }
            ),
        )

        first = controller.dispatch_ready({"CTRL-1": "Implement first"})
        decision = controller.reconcile("CTRL-1")

        self.assertEqual(tuple(item.work_item_id for item in first), ("CTRL-1",))
        self.assertEqual(decision.status, "approved")
        self.assertEqual(tracker.transitions, [("CTRL-1", "In Progress"), ("CTRL-1", "In Review")])

        controller.refresh_target_base("main-1")
        second = controller.dispatch_ready({"CTRL-2": "Implement second"})

        self.assertEqual(tuple(item.work_item_id for item in second), ("CTRL-2",))
        self.assertEqual(workspaces.created[-1].base_revision, "main-1")
        self.assertEqual(runtime.specs[-1].prompt, "Implement second")


if __name__ == "__main__":
    unittest.main()
