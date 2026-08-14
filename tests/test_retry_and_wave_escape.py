from __future__ import annotations

import unittest

from mergewave.controller import DeliveryController
from mergewave.git_workspace import Workspace
from mergewave.runtime import RunHandle, RunSpec
from mergewave.simulator import DeliveryObservation, MergeWaveSimulator


class Tracker:
    def __init__(self) -> None:
        self.transitions: list[tuple[str, str]] = []

    def transition_state(self, item_id: str, state: str) -> None:
        self.transitions.append((item_id, state))

    def pull_request_linked(self, item_id: str, url: str) -> bool:
        return True

    def acceptance_criteria_signal(self, item_id: str) -> str:
        return "complete"


class WorkspaceFactory:
    def __init__(self) -> None:
        self.created: list[Workspace] = []
        self.destroyed: list[Workspace] = []

    def create(self, workspace_id: str, base_revision: str) -> Workspace:
        workspace = Workspace(
            workspace_id,
            "demo-repository",
            f"/worktrees/{workspace_id}",
            f"mergewave/{workspace_id}",
            base_revision,
            base_revision,
            base_revision,
        )
        self.created.append(workspace)
        return workspace

    def inspect(self, workspace: Workspace) -> Workspace:
        return workspace

    def destroy(self, workspace: Workspace) -> Workspace:
        self.destroyed.append(workspace)
        return workspace


class Runtime:
    def __init__(self) -> None:
        self.started: list[RunSpec] = []
        self.cancelled: list[RunHandle] = []

    def start(self, spec: RunSpec) -> RunHandle:
        self.started.append(spec)
        return RunHandle(spec.run_id, object())

    def cancel(self, handle: RunHandle):
        self.cancelled.append(handle)
        return None


class Observer:
    def observe(self, item_id: str, workspace: Workspace) -> DeliveryObservation:
        return DeliveryObservation(
            workspace.repository,
            workspace.worktree_path,
            workspace.branch_ref,
            workspace.base_revision,
            workspace.initial_head_revision,
            "head-1",
            "head-1",
            "head-1",
            True,
            1,
            True,
            True,
            "merge-1",
            True,
            pr_id=f"pr-{workspace.workspace_id}",
            pr_url=f"https://github.com/acme/demo/{workspace.workspace_id}",
        )


class RetryAndWaveEscapeTests(unittest.TestCase):
    def _controller(self, items: list[dict[str, object]], policy: str) -> tuple[DeliveryController, Tracker, WorkspaceFactory, Runtime]:
        tracker = Tracker()
        workspaces = WorkspaceFactory()
        runtime = Runtime()
        controller = DeliveryController(
            simulator=MergeWaveSimulator(items, policy=policy, base_revision="main-0"),
            tracker=tracker,
            workspace_factory=workspaces,
            runtime=runtime,
            observer=Observer(),
        )
        return controller, tracker, workspaces, runtime

    def test_retry_supersedes_attempt_and_uses_new_workspace_and_branch(self) -> None:
        controller, _tracker, workspaces, runtime = self._controller([{"id": "CTRL-1", "blocked_by": []}], "continuous_frontier")
        controller.dispatch_ready({"CTRL-1": "Implement"})
        old_attempt = controller.work_attempt("CTRL-1")

        replacement = controller.retry("CTRL-1")

        self.assertEqual(len(replacement), 1)
        self.assertEqual(old_attempt.state, "running")
        self.assertEqual(controller._attempt_history["CTRL-1"][0].state, "superseded")
        self.assertEqual(controller.work_attempt("CTRL-1").supersedes_attempt_id, old_attempt.id)
        self.assertEqual(len(runtime.cancelled), 1)
        self.assertEqual(len(workspaces.destroyed), 1)
        self.assertNotEqual(workspaces.created[0].workspace_id, workspaces.created[1].workspace_id)
        self.assertNotEqual(replacement[0].branch_ref, "mergewave/CTRL-1")

    def test_wave_barrier_escape_is_explicit_and_unblocks_dependents(self) -> None:
        controller, tracker, _workspaces, _runtime = self._controller(
            [{"id": "CTRL-1", "blocked_by": []}, {"id": "CTRL-2", "blocked_by": ["CTRL-1"]}],
            "wave_barrier",
        )
        controller.dispatch_ready({"CTRL-1": "First", "CTRL-2": "Second"})

        controller.cancel_from_wave("CTRL-1", "superseded by a product decision")
        next_dispatch = controller.dispatch_ready({"CTRL-2": "Second"})

        self.assertEqual(tuple(dispatch.work_item_id for dispatch in next_dispatch), ("CTRL-2",))
        self.assertEqual(controller.work_attempt("CTRL-1").state, "cancelled")
        self.assertIn(("CTRL-1", "Cancelled"), tracker.transitions)


if __name__ == "__main__":
    unittest.main()
