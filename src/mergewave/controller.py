"""Small composition layer for the model-agnostic MergeWave control plane."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .git_workspace import Workspace
from .ports import DeliveryObserver, TrackerAdapter, WorkspaceFactory
from .runtime import AgentRuntime, RunHandle, RunSpec
from .simulator import DeliveryObservation, Dispatch, GateDecision, MergeWaveSimulator


@dataclass(frozen=True)
class ActiveAssignment:
    dispatch: Dispatch
    workspace: Workspace
    handle: RunHandle


class DeliveryController:
    """Compose scheduling, workspace, agent, tracker, and delivery observation.

    The controller does not decide that an agent succeeded. It only forwards
    independently observed delivery state to the gate engine and releases the
    next frontier after that engine approves it.
    """

    def __init__(
        self,
        *,
        simulator: MergeWaveSimulator,
        tracker: TrackerAdapter,
        workspace_factory: WorkspaceFactory,
        runtime: AgentRuntime,
        observer: DeliveryObserver,
    ) -> None:
        self._simulator = simulator
        self._tracker = tracker
        self._workspace_factory = workspace_factory
        self._runtime = runtime
        self._observer = observer
        self._active: dict[str, ActiveAssignment] = {}
        self._review_state_published: set[str] = set()

    def dispatch_ready(self, prompts: Mapping[str, str]) -> tuple[Dispatch, ...]:
        dispatches = self._simulator.preview_ready()
        missing_prompts = [item.work_item_id for item in dispatches if item.work_item_id not in prompts]
        if missing_prompts:
            raise ValueError(f"missing prompts for dispatched items: {missing_prompts}")

        dispatches = self._simulator.dispatch_ready()

        for dispatch in dispatches:
            workspace = self._workspace_factory.create(
                dispatch.work_item_id,
                dispatch.base_revision,
            )
            self._tracker.transition_state(dispatch.work_item_id, "In Progress")
            handle = self._runtime.start(
                RunSpec(
                    run_id=f"{dispatch.work_item_id}:{dispatch.base_revision}",
                    work_item_id=dispatch.work_item_id,
                    prompt=prompts[dispatch.work_item_id],
                    workspace_path=workspace.worktree_path,
                )
            )
            self._active[dispatch.work_item_id] = ActiveAssignment(dispatch, workspace, handle)
        return dispatches

    def reconcile(self, item_id: str) -> GateDecision:
        assignment = self._active.get(item_id)
        if assignment is None:
            raise ValueError(f"item is not active: {item_id}")
        workspace = self._workspace_factory.inspect(assignment.workspace)
        observation = self._observer.observe(item_id, workspace)
        self._simulator.observe_delivery(item_id, observation)
        if observation.pr_head_sha and item_id not in self._review_state_published:
            self._tracker.transition_state(item_id, "In Review")
            self._review_state_published.add(item_id)
        return self._simulator.evaluate_gate(item_id)

    def refresh_target_base(self, revision: str) -> None:
        self._simulator.refresh_target_base(revision)

    def active_assignment(self, item_id: str) -> ActiveAssignment:
        try:
            return self._active[item_id]
        except KeyError as error:
            raise ValueError(f"item is not active: {item_id}") from error


__all__ = ["ActiveAssignment", "DeliveryController"]
