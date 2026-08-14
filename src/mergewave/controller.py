"""Small composition layer for the model-agnostic MergeWave control plane."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json

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
    attempt_records: Mapping[str, Mapping[str, object]]
    active_assignments: Mapping[str, Mapping[str, object]]
    gate_states: Mapping[str, str]
    wave_records: Mapping[str, Mapping[str, object]]
    evidence_records: Mapping[str, Mapping[str, object]]
    pull_requests: Mapping[str, Mapping[str, object]]

    @classmethod
    def from_event_log(cls, event_log: SqliteEventLog) -> "ControllerProjection":
        initial = {
            "ticket_states": {}, "base_revision": None, "attempt_states": {}, "wave_states": {},
            "attempt_records": {}, "active_assignments": {}, "gate_states": {},
            "wave_records": {}, "evidence_records": {}, "pull_requests": {},
        }
        reduced = event_log.reduce(initial, cls._reduce)
        return cls(
            ticket_states=reduced["ticket_states"],
            base_revision=reduced["base_revision"],
            attempt_states=reduced["attempt_states"],
            wave_states=reduced["wave_states"],
            attempt_records=reduced["attempt_records"],
            active_assignments=reduced["active_assignments"],
            gate_states=reduced["gate_states"],
            wave_records=reduced["wave_records"],
            evidence_records=reduced["evidence_records"],
            pull_requests=reduced["pull_requests"],
        )

    @staticmethod
    def _reduce(state: dict[str, object], event: object) -> dict[str, object]:
        payload = event.payload
        next_state = {
            "ticket_states": dict(state["ticket_states"]),
            "base_revision": state["base_revision"],
            "attempt_states": dict(state["attempt_states"]),
            "wave_states": dict(state["wave_states"]),
            "attempt_records": dict(state["attempt_records"]),
            "active_assignments": dict(state["active_assignments"]),
            "gate_states": dict(state["gate_states"]),
            "wave_records": dict(state["wave_records"]),
            "evidence_records": dict(state["evidence_records"]),
            "pull_requests": dict(state["pull_requests"]),
        }
        if event.kind == "ticket.state_changed":
            next_state["ticket_states"][payload["item_id"]] = payload["state"]
        elif event.kind == "base_revision.refreshed":
            next_state["base_revision"] = payload["revision"]
        elif event.kind == "work_attempt.started":
            next_state["attempt_states"][payload["run_id"]] = "running"
            next_state["attempt_records"][payload["run_id"]] = dict(payload)
            if payload.get("item_id"):
                next_state["active_assignments"][payload["item_id"]] = dict(payload)
        elif event.kind == "work_attempt.state_changed":
            next_state["attempt_states"][payload["run_id"]] = payload["state"]
            record = dict(next_state["attempt_records"].get(payload["run_id"], {}))
            record["state"] = payload["state"]
            next_state["attempt_records"][payload["run_id"]] = record
            if payload["state"] in {"released", "cancelled", "superseded"}:
                item_id = record.get("item_id")
                if item_id:
                    next_state["active_assignments"].pop(item_id, None)
        elif event.kind == "execution_wave.started":
            next_state["wave_states"][payload["wave_id"]] = "active"
            next_state["wave_records"][payload["wave_id"]] = dict(payload)
        elif event.kind == "execution_wave.state_changed":
            next_state["wave_states"][payload["wave_id"]] = payload["state"]
            record = dict(next_state["wave_records"].get(payload["wave_id"], {}))
            record["state"] = payload["state"]
            next_state["wave_records"][payload["wave_id"]] = record
        elif event.kind == "gate.decided":
            next_state["gate_states"][payload["item_id"]] = payload["status"]
        elif event.kind == "validation.evidence_recorded":
            next_state["evidence_records"][payload["item_id"]] = dict(payload["evidence"])
            if payload.get("pull_request"):
                next_state["pull_requests"][payload["item_id"]] = dict(payload["pull_request"])
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
        self._attempt_history: dict[str, list[WorkAttempt]] = {}
        self._waves: dict[str, ExecutionWave] = {}

    @classmethod
    def from_event_log(cls, *, event_log: SqliteEventLog, **kwargs: object) -> "DeliveryController":
        """Rehydrate controller-owned state while keeping delivery verification external."""
        controller = cls(event_log=event_log, **kwargs)
        controller._rehydrate(ControllerProjection.from_event_log(event_log))
        return controller

    def dispatch_ready(self, prompts: Mapping[str, str]) -> tuple[Dispatch, ...]:
        dispatches = self._simulator.preview_ready()
        missing_prompts = [item.work_item_id for item in dispatches if item.work_item_id not in prompts]
        if missing_prompts:
            raise ValueError(f"missing prompts for dispatched items: {missing_prompts}")

        self._prompts.update(prompts)

        dispatches = self._simulator.dispatch_ready()

        for dispatch in dispatches:
            workspace_key = dispatch.work_item_id if dispatch.attempt_number == 1 else f"{dispatch.work_item_id}-attempt-{dispatch.attempt_number}"
            workspace = self._workspace_factory.create(
                workspace_key,
                dispatch.base_revision,
            )
            self._set_ticket_state(dispatch.work_item_id, WorkItemState.IN_PROGRESS.value)
            run_id = f"{dispatch.work_item_id}:{dispatch.base_revision}"
            if dispatch.attempt_number > 1:
                run_id = f"{run_id}:attempt-{dispatch.attempt_number}"
            previous_attempt = self._attempts.get(dispatch.work_item_id)
            attempt = WorkAttempt(
                id=f"attempt:{run_id}",
                work_item_id=dispatch.work_item_id,
                base_sha=dispatch.base_revision,
                workspace_id=workspace.workspace_id,
                agent_runtime=type(self._runtime).__qualname__,
                started_at=workspace.created_at or datetime.now(timezone.utc),
                state="running",
                supersedes_attempt_id=previous_attempt.id if previous_attempt else None,
            )
            handle = self._runtime.start(
                RunSpec(
                    run_id=run_id,
                    work_item_id=dispatch.work_item_id,
                    prompt=prompts[dispatch.work_item_id],
                    workspace_path=workspace.worktree_path,
                    work_item=self._work_items.get(dispatch.work_item_id),
                    workspace=workspace,
                )
            )
            if previous_attempt is not None:
                self._attempt_history.setdefault(dispatch.work_item_id, []).append(previous_attempt)
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
                    "repository": workspace.repository,
                    "worktree_path": workspace.worktree_path,
                    "branch_ref": workspace.branch_ref,
                    "base_revision": dispatch.base_revision,
                    "initial_head_revision": workspace.initial_head_revision,
                    "current_head_revision": workspace.current_head_revision,
                    "started_at": attempt.started_at.isoformat(),
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
        self._record_wave_state()
        if decision.status == "approved":
            if item_id not in self._done_state_published:
                self._set_ticket_state(item_id, WorkItemState.DONE.value)
                self._done_state_published.add(item_id)
                self._attempts[item_id] = replace(self._attempts[item_id], state="released")
                self._append_event("work_attempt.state_changed", {"run_id": assignment.handle.run_id, "state": "released"}, f"attempt-state:{assignment.handle.run_id}:released")
            if self._base_revision_provider and self._simulator.can_refresh_target_base():
                self.refresh_target_base()
                self._dispatch_next_frontier()
        elif decision.status == "blocked" and item_id not in self._attention_state_published:
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

    def active_item_ids(self) -> tuple[str, ...]:
        return tuple(self._active)

    def retry(self, item_id: str, *, prompt: str | None = None) -> tuple[Dispatch, ...]:
        """Supersede one active attempt and create an isolated replacement."""
        assignment = self.active_assignment(item_id)
        previous = self._attempts[item_id]
        if previous.state == "released":
            raise ValueError(f"released item cannot be retried: {item_id}")
        try:
            self._runtime.cancel(assignment.handle)
            self._workspace_factory.destroy(assignment.workspace)
        except Exception as error:
            failure = classify_failure(error, phase="workspace")
            self._append_event(
                "attempt.retry_failed",
                {"item_id": item_id, "failure_code": failure.code},
                f"retry-failure:{previous.id}:{failure.code}",
            )
            raise RuntimeError(failure.human_summary) from error
        superseded = replace(previous, state="superseded", ended_at=datetime.now(timezone.utc))
        self._attempts[item_id] = superseded
        self._append_event(
            "work_attempt.state_changed",
            {"item_id": item_id, "run_id": assignment.handle.run_id, "state": "superseded"},
            f"attempt-state:{assignment.handle.run_id}:superseded",
        )
        self._active.pop(item_id, None)
        self._review_state_published.discard(item_id)
        self._attention_state_published.discard(item_id)
        self._recorded_decisions.pop(item_id, None)
        self._validation_evidence.pop(item_id, None)
        self._human_gates.pop(item_id, None)
        self._pull_requests.pop(item_id, None)
        self._simulator.retry_item(item_id)
        next_prompt = prompt if prompt is not None else self._prompts.get(item_id)
        if next_prompt is None:
            raise ValueError(f"missing prompt for retry: {item_id}")
        return self.dispatch_ready({item_id: next_prompt})

    def cancel_from_wave(self, item_id: str, reason: str) -> None:
        """Apply the explicit human escape hatch for a barrier wave."""
        assignment = self.active_assignment(item_id)
        if not reason.strip():
            raise ValueError("cancellation reason is required")
        self._runtime.cancel(assignment.handle)
        self._workspace_factory.destroy(assignment.workspace)
        self._simulator.cancel_from_wave(item_id, reason)
        self._active.pop(item_id, None)
        self._attempts[item_id] = replace(
            self._attempts[item_id], state="cancelled", ended_at=datetime.now(timezone.utc)
        )
        self._set_ticket_state(item_id, WorkItemState.CANCELLED.value)
        self._append_event(
            "wave.item_cancelled",
            {"item_id": item_id, "reason": reason},
            f"wave-cancelled:{item_id}:{reason}",
        )

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
        if observation.merged and observation.merge_revision:
            contains_revision = getattr(self._base_revision_provider, "contains_revision", None)
            if callable(contains_revision):
                observation = replace(
                    observation,
                    merge_revision_in_target=bool(contains_revision(observation.merge_revision)),
                )
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
            attempt_id=self._active[item_id].attempt.id,
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
                self._active[item_id].attempt.id,
            )
        evidence_payload = json.loads(
            json.dumps(
                {
                    "item_id": item_id,
                    "run_id": self._active[item_id].handle.run_id,
                    "evidence": asdict(evidence),
                    "pull_request": asdict(self._pull_requests[item_id]) if item_id in self._pull_requests else None,
                },
                default=str,
                sort_keys=True,
            )
        )
        self._append_event(
            "validation.evidence_recorded",
            evidence_payload,
            f"validation:{self._active[item_id].handle.run_id}:{observation.pr_head_sha}:{decision.status}:{evidence.acceptance_criteria_signal}",
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
            environment={"runtime": type(self._runtime).__qualname__},
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
            stage="final_artifact",
            capture_policy="metadata",
            artifact_payload=self._delivery_artifact(item_id, handle),
        )
        self._recorder.record_evidence(
            evidence_id=f"{self._evidence_id(handle)}:acceptance",
            run_id=handle.run_id,
            claim="acceptance_criteria_signal",
            observed=self._validation_evidence[item_id].acceptance_criteria_signal,
            expected="complete",
            comparator="informational",
            stage="pre_final",
            capture_policy="metadata",
            artifact_payload=self._delivery_artifact(item_id, handle),
        )
        if decision.status != "pending":
            reasons = ()
            if decision.failure is not None:
                reasons = ((decision.failure.code, decision.failure.human_summary),)
            self._recorder.record_gate_decision(
                gate_id=self._gate_id(handle),
                run_id=handle.run_id,
                decision=decision.status,
                checkpoint="merge",
                policy_version="mergewave/v0.1",
                decision_authority="human",
                evidence_ids=[self._evidence_id(handle), f"{self._evidence_id(handle)}:acceptance"],
                capture_policy="metadata",
                reasons=reasons,
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

    def _delivery_artifact(self, item_id: str, handle: RunHandle) -> dict[str, object]:
        evidence = self._validation_evidence[item_id]
        pull_request = self._pull_requests.get(item_id)
        return json.loads(
            json.dumps(
                {
                    "work_item_id": item_id,
                    "attempt_id": handle.run_id,
                    "validation_evidence": asdict(evidence),
                    "pull_request": asdict(pull_request) if pull_request else None,
                },
                default=str,
                sort_keys=True,
            )
        )

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

    def _record_wave_state(self) -> None:
        wave = self._simulator.current_execution_wave()
        if wave is None:
            return
        self._waves[wave.wave_id] = wave
        if wave.state == "released":
            self._append_event(
                "execution_wave.state_changed",
                {"wave_id": wave.wave_id, "state": wave.state},
                f"wave-state:{wave.wave_id}:{wave.state}",
            )

    def _rehydrate(self, projection: ControllerProjection) -> None:
        if projection.base_revision is not None:
            self._simulator.refresh_target_base(projection.base_revision)
        self._simulator.restore_released(
            tuple(
                item_id
                for item_id, state in projection.ticket_states.items()
                if state in {WorkItemState.DONE.value, WorkItemState.CANCELLED.value}
            )
        )
        for wave_id, payload in projection.wave_records.items():
            wave = ExecutionWave(
                wave_id=str(wave_id),
                base_sha=str(payload["base_revision"] if "base_revision" in payload else payload["base_sha"]),
                work_item_ids=tuple(str(item_id) for item_id in payload.get("work_item_ids", ())),
                state=str(payload.get("state", "active")),
            )
            self._waves[wave.wave_id] = wave
            self._simulator.restore_execution_wave(wave)
        for item_id, payload in projection.active_assignments.items():
            run_id = str(payload["run_id"])
            started_at = datetime.fromisoformat(str(payload["started_at"]))
            workspace = Workspace(
                workspace_id=str(payload["workspace_id"]),
                repository=str(payload["repository"]),
                worktree_path=str(payload["worktree_path"]),
                branch_ref=str(payload["branch_ref"]),
                base_revision=str(payload["base_revision"]),
                initial_head_revision=str(payload["initial_head_revision"]),
                current_head_revision=str(payload["current_head_revision"]),
                work_item_id=str(item_id),
            )
            attempt_record = projection.attempt_records.get(run_id, payload)
            attempt = WorkAttempt(
                id=str(attempt_record["attempt_id"]),
                work_item_id=str(item_id),
                base_sha=str(attempt_record["base_revision"]),
                workspace_id=str(attempt_record["workspace_id"]),
                agent_runtime="recovered",
                started_at=started_at,
                state=str(attempt_record.get("state", "running")),
            )
            dispatch = Dispatch(
                work_item_id=str(item_id),
                base_revision=str(payload["base_revision"]),
                repository=workspace.repository,
                worktree_path=workspace.worktree_path,
                branch_ref=workspace.branch_ref,
            )
            self._attempts[str(item_id)] = attempt
            self._active[str(item_id)] = ActiveAssignment(
                dispatch, workspace, RunHandle(run_id, None), attempt
            )
            self._simulator.restore_dispatch(dispatch)
        self._recorded_decisions.update(projection.gate_states)
        for item_id, payload in projection.evidence_records.items():
            self._validation_evidence[item_id] = ValidationEvidence(
                work_item_id=item_id,
                pr_linked=bool(payload.get("pr_linked")),
                base_sha_verified=bool(payload.get("base_sha_verified")),
                ci_verified_against_head=bool(payload.get("ci_verified_against_head")),
                reviews_resolved=bool(payload.get("reviews_resolved")),
                scope_check=str(payload.get("scope_check", "unknown")),
                acceptance_criteria_signal=str(payload.get("acceptance_criteria_signal", "unknown")),
                collected_at=datetime.fromisoformat(str(payload["collected_at"])),
                attempt_id=str(payload["attempt_id"]) if payload.get("attempt_id") else None,
            )
            gate_status = projection.gate_states.get(item_id, "pending")
            self._human_gates[item_id] = HumanGate(item_id, True, gate_status == "approved")
        for item_id, payload in projection.pull_requests.items():
            self._pull_requests[item_id] = PullRequest(
                id=str(payload["id"]),
                work_item_id=str(payload["work_item_id"]),
                url=str(payload["url"]),
                head_sha=str(payload["head_sha"]),
                base_sha_at_open=str(payload["base_sha_at_open"]),
                ci_status=str(payload["ci_status"]),
                ci_checked_head_sha=str(payload["ci_checked_head_sha"]),
                reviews_resolved=bool(payload["reviews_resolved"]),
                merged=bool(payload["merged"]),
                merge_commit_sha=str(payload["merge_commit_sha"]) if payload.get("merge_commit_sha") else None,
                attempt_id=str(payload["attempt_id"]) if payload.get("attempt_id") else None,
            )


__all__ = ["ActiveAssignment", "ControllerProjection", "DeliveryController"]
