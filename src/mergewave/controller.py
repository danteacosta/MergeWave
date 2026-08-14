"""Small composition layer for the model-agnostic MergeWave control plane."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime, timezone
import hashlib

from .domain import ExecutionWave, HumanGate, PullRequest, ValidationEvidence, WorkAttempt
from .contracts import WorkItem, WorkItemState
from .git_workspace import Workspace, WorkspaceDriftError
from .persistence import SqliteEventLog
from .ports import BaseRevisionProvider, DeliveryObserver, ReliabilityRecorder, TrackerAdapter, WorkspaceFactory
from .runtime import AgentEvent, AgentRuntime, RunHandle, RunSpec, classify_runtime_event
from .simulator import DeliveryObservation, Dispatch, FailureRecord, GateDecision, MergeWaveSimulator, classify_failure


@dataclass(frozen=True)
class ActiveAssignment:
    dispatch: Dispatch
    workspace: Workspace
    handle: RunHandle
    attempt: WorkAttempt


@dataclass(frozen=True)
class ControllerProjection:
    ticket_states: Mapping[str, str]
    base_revision: str | None
    attempt_states: Mapping[str, str]
    wave_states: Mapping[str, str]

    @classmethod
    def from_event_log(cls, event_log: SqliteEventLog) -> "ControllerProjection":
        initial = {"ticket_states": {}, "base_revision": None, "attempt_states": {}, "wave_states": {}}
        reduced = event_log.reduce(initial, cls._reduce)
        return cls(
            ticket_states=reduced["ticket_states"],
            base_revision=reduced["base_revision"],
            attempt_states=reduced["attempt_states"],
            wave_states=reduced["wave_states"],
        )

    @staticmethod
    def _reduce(state: dict[str, object], event: object) -> dict[str, object]:
        payload = event.payload
        next_state = {
            "ticket_states": dict(state["ticket_states"]),
            "base_revision": state["base_revision"],
            "attempt_states": dict(state["attempt_states"]),
            "wave_states": dict(state["wave_states"]),
        }
        if event.kind == "ticket.state_changed":
            next_state["ticket_states"][payload["item_id"]] = payload["state"]
        elif event.kind == "base_revision.refreshed":
            next_state["base_revision"] = payload["revision"]
        elif event.kind == "work_attempt.started":
            next_state["attempt_states"][payload["run_id"]] = "running"
        elif event.kind == "work_attempt.state_changed":
            next_state["attempt_states"][payload["run_id"]] = payload["state"]
        elif event.kind == "execution_wave.started":
            next_state["wave_states"][payload["wave_id"]] = "active"
        elif event.kind == "execution_wave.state_changed":
            next_state["wave_states"][payload["wave_id"]] = payload["state"]
        return next_state


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
        work_items: Mapping[str, WorkItem] | None = None,
    ) -> None:
        self._simulator = simulator
        self._tracker = tracker
        self._workspace_factory = workspace_factory
        self._runtime = runtime
        self._observer = observer
        self._recorder = recorder
        self._base_revision_provider = base_revision_provider
        self._event_log = event_log
        self._work_items = work_items or {}
        self._prompts: dict[str, str] = {}
        self._active: dict[str, ActiveAssignment] = {}
        self._review_state_published: set[str] = set()
        self._recorded_decisions: dict[str, str] = {}
        self._validation_evidence: dict[str, ValidationEvidence] = {}
        self._human_gates: dict[str, HumanGate] = {}
        self._pull_requests: dict[str, PullRequest] = {}
        self._attention_state_published: set[str] = set()
        self._done_state_published: set[str] = set()
        self._attempts: dict[str, WorkAttempt] = {}
        self._waves: dict[str, ExecutionWave] = {}

    def dispatch_ready(self, prompts: Mapping[str, str]) -> tuple[Dispatch, ...]:
        dispatches = self._simulator.preview_ready()
        missing_prompts = [item.work_item_id for item in dispatches if item.work_item_id not in prompts]
        if missing_prompts:
            raise ValueError(f"missing prompts for dispatched items: {missing_prompts}")

        self._prompts.update(prompts)

        dispatches = self._simulator.dispatch_ready()

        for dispatch in dispatches:
            workspace = self._workspace_factory.create(
                dispatch.work_item_id,
                dispatch.base_revision,
            )
            self._set_ticket_state(dispatch.work_item_id, WorkItemState.IN_PROGRESS.value)
            attempt = WorkAttempt(
                id=f"attempt:{dispatch.work_item_id}:{dispatch.base_revision}",
                work_item_id=dispatch.work_item_id,
                base_sha=dispatch.base_revision,
                workspace_id=workspace.workspace_id,
                agent_runtime=type(self._runtime).__qualname__,
                started_at=workspace.created_at or datetime.now(timezone.utc),
                state="running",
            )
            handle = self._runtime.start(
                RunSpec(
                    run_id=f"{dispatch.work_item_id}:{dispatch.base_revision}",
                    work_item_id=dispatch.work_item_id,
                    prompt=prompts[dispatch.work_item_id],
                    workspace_path=workspace.worktree_path,
                    work_item=self._work_items.get(dispatch.work_item_id),
                    workspace=workspace,
                )
            )
            self._attempts[dispatch.work_item_id] = attempt
            self._active[dispatch.work_item_id] = ActiveAssignment(dispatch, workspace, handle, attempt)
            self._record_run_and_gate_request(dispatch, workspace, prompts[dispatch.work_item_id], handle)
            self._append_event(
                "work_attempt.started",
                {
                    "item_id": dispatch.work_item_id,
                    "run_id": handle.run_id,
                    "attempt_id": attempt.id,
                    "workspace_id": workspace.workspace_id,
                    "base_revision": dispatch.base_revision,
                },
                f"attempt:{handle.run_id}",
            )
        wave = self._simulator.current_execution_wave()
        if wave is not None:
            self._waves[wave.wave_id] = wave
            self._append_event(
                "execution_wave.started",
                {"wave_id": wave.wave_id, "base_revision": wave.base_sha, "work_item_ids": list(wave.work_item_ids)},
                f"wave:{wave.wave_id}",
            )
        return dispatches

    def reconcile(self, item_id: str) -> GateDecision:
        assignment = self._active.get(item_id)
        if assignment is None:
            raise ValueError(f"item is not active: {item_id}")
        try:
            workspace = self._workspace_factory.inspect(assignment.workspace)
        except (FileNotFoundError, WorkspaceDriftError) as error:
            failure = classify_failure(error, phase="workspace")
            self._set_ticket_state(item_id, WorkItemState.NEEDS_ATTENTION.value)
            self._attention_state_published.add(item_id)
            self._append_event("reconciliation.failed", {"item_id": item_id, "failure_code": failure.code}, f"reconcile-failure:{item_id}:{failure.code}")
            return GateDecision("blocked", failure)
        try:
            observation = self._observer.observe(item_id, workspace)
        except Exception as error:
            failure = classify_failure(error, phase="delivery")
            self._append_event("reconciliation.failed", {"item_id": item_id, "failure_code": failure.code}, f"reconcile-failure:{item_id}:{failure.code}")
            return GateDecision("blocked", failure)
        try:
            observation = self._enrich_observation(item_id, observation)
        except Exception as error:
            failure = classify_failure(error, phase="tracker")
            self._append_event("reconciliation.failed", {"item_id": item_id, "failure_code": failure.code}, f"reconcile-failure:{item_id}:{failure.code}")
            return GateDecision("blocked", failure)
        self._simulator.observe_delivery(item_id, observation)
        if observation.pr_head_sha and item_id not in self._review_state_published:
            self._set_ticket_state(item_id, WorkItemState.IN_REVIEW.value)
            self._review_state_published.add(item_id)
        decision = self._simulator.evaluate_gate(item_id)
        self._record_domain_evidence(item_id, observation, decision)
        if decision.status == "approved":
            if item_id not in self._done_state_published:
                self._set_ticket_state(item_id, WorkItemState.DONE.value)
                self._done_state_published.add(item_id)
                self._attempts[item_id] = replace(self._attempts[item_id], state="released")
                self._append_event("work_attempt.state_changed", {"run_id": assignment.handle.run_id, "state": "released"}, f"attempt-state:{assignment.handle.run_id}:released")
            if self._base_revision_provider and self._simulator.can_refresh_target_base():
                self.refresh_target_base()
                self._dispatch_next_frontier()
        elif item_id not in self._attention_state_published:
            self._set_ticket_state(item_id, WorkItemState.NEEDS_ATTENTION.value)
            self._attention_state_published.add(item_id)
            self._attempts[item_id] = replace(self._attempts[item_id], state="needs_attention")
            self._append_event("work_attempt.state_changed", {"run_id": assignment.handle.run_id, "state": "needs_attention"}, f"attempt-state:{assignment.handle.run_id}:needs_attention")
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

    def work_attempt(self, item_id: str) -> WorkAttempt:
        return self._attempts[item_id]

    def execution_wave(self) -> ExecutionWave:
        wave = self._simulator.current_execution_wave()
        if wave is None:
            raise ValueError("no execution wave has been dispatched")
        return wave

    def observe_runtime_events(self, item_id: str, events: list[AgentEvent]) -> FailureRecord | None:
        for event in events:
            code = classify_runtime_event(event)
            if code is None:
                continue
            failure = FailureRecord(
                code,
                "runtime",
                "blocking",
                code in {"agent_timeout", "runtime_failed"},
                "The agent runtime did not complete successfully.",
                "Do not treat the runtime claim as delivery evidence; inspect and retry the attempt.",
                "Route the item to NeedsAttention and reconcile a new attempt after the workspace is safe.",
            )
            if item_id not in self._attention_state_published:
                self._set_ticket_state(item_id, WorkItemState.NEEDS_ATTENTION.value)
                self._attention_state_published.add(item_id)
            if item_id in self._attempts:
                assignment = self._active.get(item_id)
                self._attempts[item_id] = replace(self._attempts[item_id], state="needs_attention")
                if assignment is not None:
                    self._append_event("work_attempt.state_changed", {"run_id": assignment.handle.run_id, "state": "needs_attention"}, f"attempt-state:{assignment.handle.run_id}:needs_attention")
            self._append_event(
                "runtime.failed",
                {"item_id": item_id, "failure_code": code},
                f"runtime-failure:{item_id}:{code}",
            )
            return failure
        return None

    def _enrich_observation(self, item_id: str, observation: DeliveryObservation) -> DeliveryObservation:
        linked = observation.linked_to_ticket
        if observation.pr_url:
            linked_checker = getattr(self._tracker, "pull_request_linked", None)
            if callable(linked_checker):
                linked = bool(linked_checker(item_id, observation.pr_url))
            else:
                linked = False
        elif observation.pr_head_sha:
            linked = False
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
            pr_linked=bool(observation.pr_head_sha) and observation.linked_to_ticket is True,
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
            observed=self._validation_evidence[item_id].acceptance_criteria_signal,
            expected="complete",
            comparator="informational",
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

    def _set_ticket_state(self, item_id: str, state: str) -> None:
        self._tracker.transition_state(item_id, state)
        self._append_event(
            "ticket.state_changed",
            {"item_id": item_id, "state": state},
            f"ticket-state:{item_id}:{state}",
        )

    def _dispatch_next_frontier(self) -> tuple[Dispatch, ...]:
        ready = self._simulator.preview_ready()
        if not ready:
            return ()
        if any(item.work_item_id not in self._prompts for item in ready):
            self._append_event(
                "frontier.waiting_for_prompts",
                {"work_item_ids": [item.work_item_id for item in ready]},
                f"frontier-prompts:{','.join(item.work_item_id for item in ready)}",
            )
            return ()
        return self.dispatch_ready(self._prompts)


__all__ = ["ActiveAssignment", "ControllerProjection", "DeliveryController"]
