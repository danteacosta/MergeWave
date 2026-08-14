"""Small composition layer for the model-agnostic MergeWave control plane."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib

from .git_workspace import Workspace
from .ports import DeliveryObserver, ReliabilityRecorder, TrackerAdapter, WorkspaceFactory
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
        recorder: ReliabilityRecorder | None = None,
    ) -> None:
        self._simulator = simulator
        self._tracker = tracker
        self._workspace_factory = workspace_factory
        self._runtime = runtime
        self._observer = observer
        self._recorder = recorder
        self._active: dict[str, ActiveAssignment] = {}
        self._review_state_published: set[str] = set()
        self._recorded_decisions: dict[str, str] = {}

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
            self._record_run_and_gate_request(dispatch, workspace, prompts[dispatch.work_item_id], handle)
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
        decision = self._simulator.evaluate_gate(item_id)
        self._record_decision(item_id, assignment.handle, decision)
        return decision

    def refresh_target_base(self, revision: str) -> None:
        self._simulator.refresh_target_base(revision)

    def active_assignment(self, item_id: str) -> ActiveAssignment:
        try:
            return self._active[item_id]
        except KeyError as error:
            raise ValueError(f"item is not active: {item_id}") from error

    def _record_run_and_gate_request(
        self,
        dispatch: Dispatch,
        workspace: Workspace,
        prompt: str,
        handle: RunHandle,
    ) -> None:
        if self._recorder is None:
            return
        input_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        configuration_hash = hashlib.sha256(
            type(self._runtime).__qualname__.encode("utf-8")
        ).hexdigest()
        self._recorder.record_run(
            run_id=handle.run_id,
            base_revision=dispatch.base_revision,
            input_ref=dispatch.work_item_id,
            input_hash=input_hash,
            executor_name=type(self._runtime).__qualname__,
            executor_version="1",
            configuration_hash=configuration_hash,
            capture_policy="metadata",
            extensions={
                "work_item_id": dispatch.work_item_id,
                "repository": workspace.repository,
                "worktree_path": workspace.worktree_path,
                "branch_ref": workspace.branch_ref,
            },
        )
        self._recorder.record_gate_request(
            gate_id=self._gate_id(handle),
            run_id=handle.run_id,
            checkpoint="merge",
            policy_version="mergewave/v0.1",
            decision_authority="human",
            required_evidence_ids=[self._evidence_id(handle)],
            capture_policy="metadata",
        )

    def _record_decision(self, item_id: str, handle: RunHandle, decision: GateDecision) -> None:
        if self._recorder is None or self._recorded_decisions.get(item_id) == decision.status:
            return
        self._recorded_decisions[item_id] = decision.status
        self._recorder.record_evidence(
            evidence_id=self._evidence_id(handle),
            run_id=handle.run_id,
            claim="delivery_gate_status",
            observed=decision.status,
            expected="approved",
            comparator="equals",
            stage="delivery",
            capture_policy="metadata",
        )
        self._recorder.record_gate_decision(
            gate_id=self._gate_id(handle),
            run_id=handle.run_id,
            decision=decision.status,
            checkpoint="merge",
            policy_version="mergewave/v0.1",
            decision_authority="human",
            evidence_ids=[self._evidence_id(handle)],
            capture_policy="metadata",
        )

    @staticmethod
    def _gate_id(handle: RunHandle) -> str:
        return f"gate:{handle.run_id}"

    @staticmethod
    def _evidence_id(handle: RunHandle) -> str:
        return f"evidence:{handle.run_id}:delivery"


__all__ = ["ActiveAssignment", "DeliveryController"]
