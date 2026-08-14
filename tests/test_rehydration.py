from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mergewave.controller import ControllerProjection, DeliveryController
from mergewave.git_workspace import Workspace
from mergewave.persistence import SqliteEventLog
from mergewave.runtime import RunHandle, RunSpec
from mergewave.simulator import DeliveryObservation, MergeWaveSimulator


class Tracker:
    def transition_state(self, item_id: str, state: str) -> None:
        pass

    def pull_request_linked(self, item_id: str, url: str) -> bool:
        return True

    def acceptance_criteria_signal(self, item_id: str) -> str:
        return "complete"


class WorkspaceFactory:
    def create(self, workspace_id: str, base_revision: str) -> Workspace:
        return Workspace(workspace_id, "demo", f"/tmp/{workspace_id}", f"mergewave/{workspace_id}", base_revision, base_revision, base_revision)

    def inspect(self, workspace: Workspace) -> Workspace:
        return workspace

    def destroy(self, workspace: Workspace) -> Workspace:
        return workspace


class Runtime:
    def start(self, spec: RunSpec) -> RunHandle:
        return RunHandle(spec.run_id, object())

    def cancel(self, handle: RunHandle):
        return None


class Observer:
    def observe(self, item_id: str, workspace: Workspace) -> DeliveryObservation:
        return DeliveryObservation(
            workspace.repository, workspace.worktree_path, workspace.branch_ref,
            workspace.base_revision, workspace.initial_head_revision,
            "head", "head", "head", True, 1, True, True, "merge", True,
            pr_url="https://github.com/acme/demo/pull/1",
        )


class RehydrationTests(unittest.TestCase):
    def test_projection_and_controller_restore_active_attempt_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "events.sqlite")
            first_log = SqliteEventLog(database)
            first = DeliveryController(
                simulator=MergeWaveSimulator([{"id": "CTRL-1", "blocked_by": []}], policy="continuous_frontier", base_revision="main-0"),
                tracker=Tracker(), workspace_factory=WorkspaceFactory(), runtime=Runtime(), observer=Observer(), event_log=first_log,
            )
            first.dispatch_ready({"CTRL-1": "Implement"})
            projection = ControllerProjection.from_event_log(first_log)
            self.assertIn("CTRL-1", projection.active_assignments)
            first_log.close()

            second_log = SqliteEventLog(database)
            second = DeliveryController.from_event_log(
                event_log=second_log,
                simulator=MergeWaveSimulator([{"id": "CTRL-1", "blocked_by": []}], policy="continuous_frontier", base_revision="main-0"),
                tracker=Tracker(), workspace_factory=WorkspaceFactory(), runtime=Runtime(), observer=Observer(),
            )

            self.assertEqual(second.active_item_ids(), ("CTRL-1",))
            self.assertEqual(second.work_attempt("CTRL-1").state, "running")
            self.assertEqual(second.active_assignment("CTRL-1").workspace.branch_ref, "mergewave/CTRL-1")
            self.assertEqual(second.reconcile("CTRL-1").status, "approved")
            second_log.close()


if __name__ == "__main__":
    unittest.main()
