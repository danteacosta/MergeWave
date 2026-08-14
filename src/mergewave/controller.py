"""Small composition layer for the model-agnostic MergeWave control plane."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime, timezone
import hashlib

from .domain import HumanGate, PullRequest, ValidationEvidence
from .contracts import WorkItemState
from .git_workspace import Workspace
from .persistence import SqliteEventLog
from .ports import BaseRevisionProvider, DeliveryObserver, ReliabilityRecorder, TrackerAdapter, WorkspaceFactory
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
        base_revision_provider: BaseRevisionProvider | None = None,
        event_log: SqliteEventLog | None = None,
    ) -> None:
        self._simulator = simulator
        self._tracker = tracker
        self._workspace_factory = workspace_factory
        self._runtime = runtime
        self._observer = observer
        self._recorder = recorder
        self._base_revision_provider = base_revision_provider
        self._event_log = event_log
        self._active: dict[str, ActiveAssignment] = {}
        self._review_state_published: set[str] = set()
        self._recorded_decisions: dict[str, str] = {}
        self._validation_evidence: dict[str, ValidationEvidence] = {}
        self._human_gates: dict[str, HumanGate] = {}
        self._pull_requests: dict[str, PullRequest] = {}
        self._attention_state_published: set[str] = set()
        self._done_state_published: set[str] = set()

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
            self._tracker.transition_state(dispatch.work_item_id, WorkItemState.IN_PROGRESS.value)
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
            self._append_event(
                "work_attempt.started",
                {"item_id": dispatch.work_item_id, "run_id": handle.run_id, "base_revision": dispatch.base_revision},
                f"attempt:{handle.run_id}",
            )
        return dispatches

    def reconcile(self, item_id: str) -> GateDecision:
        assignment = self._active.get(item_id)
        if assignment is None:
            raise ValueError(f"item is not active: {item_id}")
        workspace = self._workspace_factory.inspect(assignment.workspace)
        observation = self._observer.observe(item_id, workspace)
        observation = self._enrich_observation(item_id, observation)
        self._simulator.observe_delivery(item_id, observation)
        if observation.pr_head_sha and item_id not in self._review_state_published:
            self._tracker.transition_state(item_id, WorkItemState.IN_REVIEW.value)
            self._review_state_published.add(item_id)
        decision = self._simulator.evaluate_gate(item_id)
        self._record_domain_evidence(item_id, observation, decision)
        if decision.status == "approved":
            if item_id not in self._done_state_published:
                self._tracker.transition_state(item_id, "Done")
                self._done_state_published.add(item_id)
            if self._base_revision_provider and self._simulator.can_refresh_target_base():
                self.refresh_target_base()
        elif item_id not in self._attention_state_published:
            self._tracker.transition_state(item_id, "NeedsAttention")
            self._attention_state_published.add(item_id)
        self._record_decision(item_id, assignment.handle, decision)
        self._append_event(
            "gate.decided",
            {"item_id": item_id, "status": decision.status, "failure": decision.failure.code if decision.failure else None},
            f"gate:{assignment.handle.run_id}:{decision.status}",
        )
        return decision

    def refresh_target_base(self, revision: str | None = None) -> None:
        if revision is None:
            if self._base_revision_provider is None:
                raise ValueError("a base revision provider is required when revision is omitted")
            revision = self._base_revision_provider.current_revision()
        self._simulator.refresh_target_base(revision)
        self._append_event("base_revision.refreshed", {"revision": revision}, f"base:{revision}")

    def active_assignment(self, item_id: str) -> ActiveAssignment:
        try:
            return self._active[item_id]
        except KeyError as error:
            raise ValueError(f"item is not active: {item_id}") from error

    def validation_evidence(self, item_id: str) -> ValidationEvidence:
        return self._validation_evidence[item_id]

    def human_gate(self, item_id: str) -> HumanGate:
        return self._human_gates[item_id]

    def pull_request(self, item_id: str) -> PullRequest:
        return self._pull_requests[item_id]

    def _enrich_observation(self, item_id: str, observation: DeliveryObservation) -> DeliveryObservation:
        linked = observation.linked_to_ticket
        if observation.pr_url:
            linked_checker = getattr(self._tracker, "pull_request_linked", None)
            if callable(linked_checker):
                linked = bool(linked_checker(item_id, observation.pr_url))
        acceptance_signal = observation.acceptance_criteria_signal
        acceptance_checker = getattr(self._tracker, "acceptance_criteria_signal", None)
        if callable(acceptance_checker):
            acceptance_signal = str(acceptance_checker(item_id))
        return replace(observation, linked_to_ticket=linked, acceptance_criteria_signal=acceptance_signal)

    def _record_domain_evidence(
        self,
        item_id: str,
        observation: DeliveryObservation,
        decision: GateDecision,
    ) -> None:
        now = datetime.now(timezone.utc)
        evidence = ValidationEvidence(
            work_item_id=item_id,
            pr_linked=bool(observation.pr_head_sha) and observation.linked_to_ticket is not False,
            base_sha_verified=observation.base_is_ancestor,
            ci_verified_against_head=observation.ci_head_sha == observation.pr_head_sha,
            reviews_resolved=observation.reviews_resolved if observation.reviews_resolved is not None else observation.approvals >= 1,
            scope_check="pass" if observation.scope_ok else "flagged",
            acceptance_criteria_signal=observation.acceptance_criteria_signal,
            collected_at=now,
        )
        self._validation_evidence[item_id] = evidence
        self._human_gates[item_id] = HumanGate(
            item_id,
            True,
            decision.status == "approved" and observation.merged,
            observation.merged_by,
            now if decision.status == "approved" and observation.merged else None,
        )
        if observation.pr_head_sha:
            self._pull_requests[item_id] = PullRequest(
                observation.pr_id,
                item_id,
                observation.pr_url,
                observation.pr_head_sha,
                observation.base_sha_at_open or observation.base_revision,
                "passing" if observation.ci_passed else "failing",
                observation.ci_head_sha,
                evidence.reviews_resolved,
                observation.merged,
                observation.merge_revision,
            )

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
        self._recorder.record_evidence(
            evidence_id=f"{self._evidence_id(handle)}:acceptance",
            run_id=handle.run_id,
            claim="acceptance_criteria_signal",
            observed="recorded",
            expected="recorded",
            comparator="equals",
            stage="acceptance",
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

    def _append_event(self, kind: str, payload: Mapping[str, object], key: str) -> None:
        if self._event_log is not None:
            self._event_log.append(kind, payload, idempotency_key=key)


__all__ = ["ActiveAssignment", "DeliveryController"]
