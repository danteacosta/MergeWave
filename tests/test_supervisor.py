from __future__ import annotations

import unittest

from mergewave.controller import DeliveryController
from mergewave.git_workspace import Workspace
from mergewave.runtime import AgentEvent, RunHandle, RunSpec, RuntimeCapabilities
from mergewave.simulator import DeliveryObservation, MergeWaveSimulator
from mergewave.supervisor import DeliverySupervisor, SupervisorPolicy


class Tracker:
    def transition_state(self, item_id: str, state: str) -> None:
        pass

    def post_comment(self, item_id: str, body: str) -> None:
        pass

    def pull_request_linked(self, item_id: str, url: str) -> bool:
        return True

    def acceptance_criteria_signal(self, item_id: str) -> str:
        return "complete"


class Workspaces:
    def create(self, item_id: str, base: str) -> Workspace:
        return Workspace(item_id, "demo-repository", f"/worktrees/{item_id}", f"mergewave/{item_id}", base, base, base)

    def inspect(self, workspace: Workspace) -> Workspace:
        return workspace

    def destroy(self, workspace: Workspace) -> Workspace:
        return workspace


class Runtime:
    def __init__(self, events: tuple[AgentEvent, ...]) -> None:
        self.events = events
        self.starts = 0
        self.cancelled = 0

    def start(self, spec: RunSpec) -> RunHandle:
        self.starts += 1
        return RunHandle(spec.run_id, f"runtime-{self.starts}")

    def stream(self, handle: RunHandle):
        return iter(self.events)

    def cancel(self, handle: RunHandle) -> AgentEvent:
        self.cancelled += 1
        return AgentEvent("runtime.cancelled", {})

    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(False, True, True)


class Observer:
    def observe(self, item_id: str, workspace: Workspace) -> DeliveryObservation:
        return DeliveryObservation(
            workspace.repository, workspace.worktree_path, workspace.branch_ref,
            workspace.base_revision, workspace.initial_head_revision,
            "head", "head", "head", True, 1, True, False, None, True,
            pr_url="https://github.com/acme/repo/pull/1",
        )


class SupervisorTests(unittest.TestCase):
    @staticmethod
    def cycle_until(supervisor: DeliverySupervisor, predicate):
        for _ in range(20):
            cycle = supervisor.run_once()
            if predicate(cycle):
                return cycle
        raise AssertionError("supervisor did not observe the runtime event")

    def controller(self, runtime: Runtime) -> DeliveryController:
        controller = DeliveryController(
            simulator=MergeWaveSimulator(
                [{"id": "CTRL-1", "blocked_by": []}],
                policy="continuous_frontier",
                base_revision="main-0",
            ),
            tracker=Tracker(), workspace_factory=Workspaces(), runtime=runtime, observer=Observer(),
        )
        controller.dispatch_ready({"CTRL-1": "Implement the work item"})
        return controller

    def test_cycle_consumes_a_runtime_once_and_keeps_reconciling_delivery(self) -> None:
        runtime = Runtime((AgentEvent("runtime.exited", {"returncode": 0}),))
        supervisor = DeliverySupervisor(self.controller(runtime))

        first = self.cycle_until(supervisor, lambda cycle: "CTRL-1" in cycle.decisions)
        second = supervisor.run_once()

        self.assertEqual(first.decisions["CTRL-1"].failure.code, "merge_not_observed")
        self.assertEqual(second.decisions["CTRL-1"].failure.code, "merge_not_observed")
        self.assertEqual(runtime.starts, 1)

    def test_retryable_runtime_failure_is_retried_with_a_bounded_budget(self) -> None:
        runtime = Runtime((AgentEvent("runtime.timeout", {"timeout_seconds": 1}),))
        controller = self.controller(runtime)
        supervisor = DeliverySupervisor(
            controller,
            policy=SupervisorPolicy(poll_interval_seconds=1, max_attempts=2),
        )

        cycle = self.cycle_until(supervisor, lambda result: "CTRL-1" in result.runtime_failures)

        self.assertEqual(cycle.runtime_failures["CTRL-1"].code, "agent_timeout")
        self.assertEqual(cycle.retried_item_ids, ("CTRL-1",))
        self.assertEqual(controller.active_assignment("CTRL-1").dispatch.attempt_number, 2)
        self.assertEqual(runtime.cancelled, 1)


if __name__ == "__main__":
    unittest.main()
