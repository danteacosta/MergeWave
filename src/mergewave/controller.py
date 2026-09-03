"""Small composition layer for the model-agnostic MergeWave control plane."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import json

from .domain import ExecutionWave, HumanGate, PullRequest, ValidationEvidence, WorkAttempt
from .contracts import ProjectSnapshot, WorkItem, WorkItemState
from .git_workspace import Workspace, WorkspaceDriftError
from .persistence import SqliteEventLog
from .ports import BaseRevisionProvider, DeliveryObserver, ReliabilityRecorder, TrackerAdapter, WorkspaceFactory
from .runtime import AgentEvent, AgentRuntime, RunHandle, RunSpec, classify_runtime_event
from .skills import (
    GitWorkspaceAuthorityVerifier,
    SKILL_PACK_VERSION,
    SkillArtifactVerifier,
    SkillInvocation,
    SkillManifestVerifier,
    SkillResult,
    SkillResultEvent,
    WorkspaceAuthorityVerifier,
    WorkspaceSkillArtifactVerifier,
)
from .simulator import DeliveryObservation, Dispatch, FailureRecord, GateDecision, MergeWaveSimulator, classify_failure


@dataclass(frozen=True)
class ActiveAssignment:
    dispatch: Dispatch
    workspace: Workspace
    handle: RunHandle
    attempt: WorkAttempt
    runtime_attached: bool = True


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
    prompts: Mapping[str, Mapping[str, object]]
    project_snapshot: Mapping[str, object] | None
    arp_emissions: frozenset[str]
    failure_records: Mapping[str, Mapping[str, object]]
    skill_results: Mapping[str, tuple[Mapping[str, object], ...]] = field(default_factory=dict)

    @classmethod
    def from_event_log(cls, event_log: SqliteEventLog) -> "ControllerProjection":
        initial = {
            "ticket_states": {}, "base_revision": None, "attempt_states": {}, "wave_states": {},
            "attempt_records": {}, "active_assignments": {}, "gate_states": {},
            "wave_records": {}, "evidence_records": {}, "pull_requests": {},
            "prompts": {}, "project_snapshot": None, "arp_emissions": set(),
            "failure_records": {}, "skill_results": {},
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
            prompts=reduced["prompts"],
            project_snapshot=reduced["project_snapshot"],
            arp_emissions=frozenset(reduced["arp_emissions"]),
            failure_records=reduced["failure_records"],
            skill_results=reduced["skill_results"],
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
            "prompts": dict(state["prompts"]),
            "project_snapshot": state["project_snapshot"],
            "arp_emissions": set(state["arp_emissions"]),
            "failure_records": dict(state["failure_records"]),
            "skill_results": {
                item_id: tuple(records)
                for item_id, records in state["skill_results"].items()
            },
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
        elif event.kind == "skill.stage_started":
            item_id = str(payload["item_id"])
            run_id = str(payload["run_id"])
            if item_id in next_state["active_assignments"]:
                assignment = dict(next_state["active_assignments"][item_id])
                assignment["skill"] = payload.get("invocation")
                assignment["stage"] = payload.get("invocation", {}).get("stage") if isinstance(payload.get("invocation"), Mapping) else None
                assignment["invocation_id"] = payload.get("invocation", {}).get("invocation_id") if isinstance(payload.get("invocation"), Mapping) else None
                next_state["active_assignments"][item_id] = assignment
            if run_id in next_state["attempt_records"]:
                attempt = dict(next_state["attempt_records"][run_id])
                attempt["skill"] = payload.get("invocation")
                next_state["attempt_records"][run_id] = attempt
        elif event.kind == "execution_wave.started":
            next_state["wave_states"][payload["wave_id"]] = "active"
            next_state["wave_records"][payload["wave_id"]] = dict(payload)
        elif event.kind == "execution_wave.state_changed":
            next_state["wave_states"][payload["wave_id"]] = payload["state"]
            record = dict(next_state["wave_records"].get(payload["wave_id"], {}))
            record["state"] = payload["state"]
            next_state["wave_records"][payload["wave_id"]] = record
        elif event.kind in {"gate.decided", "gate.pending"}:
            next_state["gate_states"][payload["item_id"]] = payload["status"]
        elif event.kind == "validation.evidence_recorded":
            next_state["evidence_records"][payload["item_id"]] = dict(payload["evidence"])
            if payload.get("pull_request"):
                next_state["pull_requests"][payload["item_id"]] = dict(payload["pull_request"])
        elif event.kind == "prompt.snapshot_recorded":
            next_state["prompts"][payload["item_id"]] = dict(payload)
        elif event.kind == "project.snapshot_recorded":
            next_state["project_snapshot"] = dict(payload)
        elif event.kind == "arp.emission_recorded":
            next_state["arp_emissions"].add(payload["emission_id"])
        elif event.kind == "failure.recorded":
            next_state["failure_records"][payload["evidence_id"]] = dict(payload)
        elif event.kind == "skill.result.recorded":
            item_id = str(payload["item_id"])
            records = list(next_state["skill_results"].get(item_id, ()))
            records.append(dict(payload))
            next_state["skill_results"][item_id] = tuple(records)
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
        project_snapshot: ProjectSnapshot | None = None,
        merge_authority: str = "human_only",
        skill_invocations: Mapping[str, SkillInvocation] | None = None,
        manifest_verifier: SkillManifestVerifier | None = None,
        artifact_verifier: SkillArtifactVerifier | None = None,
        authority_verifier: WorkspaceAuthorityVerifier | None = None,
    ) -> None:
        if merge_authority != "human_only":
            raise ValueError(
                "MergeWave supports merge_authority='human_only'; auto-merge is intentionally absent"
            )
        self._simulator = simulator
        self._tracker = tracker
        self._workspace_factory = workspace_factory
        self._runtime = runtime
        self._observer = observer
        self._recorder = recorder
        self._base_revision_provider = base_revision_provider
        self._event_log = event_log
        self._work_items = work_items or {}
        self._project_snapshot = project_snapshot or (
            ProjectSnapshot.from_work_items(self._work_items.values()) if self._work_items else None
        )
        self._skill_templates: dict[str, SkillInvocation] = dict(skill_invocations or {})
        self._skill_invocations: dict[str, SkillInvocation] = {}
        self._manifest_verifier = manifest_verifier
        self._artifact_verifier = artifact_verifier or WorkspaceSkillArtifactVerifier()
        self._authority_verifier = authority_verifier or GitWorkspaceAuthorityVerifier()
        self._authority_baselines: dict[str, object] = {}
        self._skill_results: dict[str, list[Mapping[str, object]]] = {}
        self._recorded_skill_result_keys: set[str] = set()
        self._recorded_skill_result_fingerprints: dict[str, str] = {}
        self._skill_result_seen: dict[str, str | None] = {}
        self._prompts: dict[str, str] = {}
        self._active: dict[str, ActiveAssignment] = {}
        self._review_state_published: set[str] = set()
        self._recorded_decisions: dict[str, str] = {}
        self._recorded_evidence_ids: set[str] = set()
        self._gate_request_evidence: dict[str, tuple[str, ...]] = {}
        self._validation_evidence: dict[str, ValidationEvidence] = {}
        self._human_gates: dict[str, HumanGate] = {}
        self._pull_requests: dict[str, PullRequest] = {}
        self._attention_state_published: set[str] = set()
        self._done_state_published: set[str] = set()
        self._attempts: dict[str, WorkAttempt] = {}
        self._attempt_history: dict[str, list[WorkAttempt]] = {}
        self._waves: dict[str, ExecutionWave] = {}
        self._arp_emissions: set[str] = set()
        self._failure_records: dict[str, FailureRecord] = {}
        self._merge_authority = merge_authority

    @property
    def merge_authority(self) -> str:
        return self._merge_authority

    @classmethod
    def from_event_log(cls, *, event_log: SqliteEventLog, **kwargs: object) -> "DeliveryController":
        """Rehydrate controller-owned state while keeping delivery verification external."""
        controller = cls(event_log=event_log, **kwargs)
        controller._rehydrate(ControllerProjection.from_event_log(event_log))
        return controller

    def dispatch_ready(
        self,
        prompts: Mapping[str, str],
        *,
        skill_invocations: Mapping[str, SkillInvocation] | None = None,
    ) -> tuple[Dispatch, ...]:
        if skill_invocations is not None:
            self._skill_templates.update(skill_invocations)
        preview = self._simulator.preview_ready()
        missing_prompts = [item.work_item_id for item in preview if item.work_item_id not in prompts]
        if missing_prompts:
            raise ValueError(f"missing prompts for dispatched items: {missing_prompts}")
        for dispatch in preview:
            skill_template = self._skill_templates.get(dispatch.work_item_id)
            if skill_template is None and dispatch.work_item_id in self._skill_invocations:
                skill_template = self._unbound_skill_template(self._skill_invocations[dispatch.work_item_id])
                self._skill_templates[dispatch.work_item_id] = skill_template
            if skill_template is not None:
                self._validate_skill_template(skill_template)

        self._persist_prompt_snapshots(prompts)
        self._persist_project_snapshot()

        self._prompts.update(prompts)

        dispatches = self._simulator.dispatch_ready()

        for dispatch in dispatches:
            workspace_key = dispatch.work_item_id if dispatch.attempt_number == 1 else f"{dispatch.work_item_id}-attempt-{dispatch.attempt_number}"
            run_id = f"{dispatch.work_item_id}:{dispatch.base_revision}"
            if dispatch.attempt_number > 1:
                run_id = f"{run_id}:attempt-{dispatch.attempt_number}"
            previous_attempt = self._attempts.get(dispatch.work_item_id)
            skill_template = self._skill_templates.get(dispatch.work_item_id)
            if skill_template is None and dispatch.work_item_id in self._skill_invocations:
                skill_template = self._unbound_skill_template(self._skill_invocations[dispatch.work_item_id])
            skill_invocation = None
            if skill_template is not None:
                skill_invocation = skill_template.bind(
                    work_item_id=dispatch.work_item_id,
                    attempt_id=f"attempt:{run_id}",
                )
                self._skill_invocations[dispatch.work_item_id] = skill_invocation
            workspace = self._workspace_factory.create(
                workspace_key,
                dispatch.base_revision,
            )
            if skill_invocation is not None:
                self._authority_baselines[dispatch.work_item_id] = self._authority_verifier.capture(workspace)
                self._skill_result_seen[dispatch.work_item_id] = None
            self._set_ticket_state(dispatch.work_item_id, WorkItemState.IN_PROGRESS.value)
            attempt = WorkAttempt(
                id=f"attempt:{run_id}",
                work_item_id=dispatch.work_item_id,
                base_sha=dispatch.base_revision,
                workspace_id=workspace.workspace_id,
                agent_runtime=type(self._runtime).__qualname__,
                started_at=workspace.created_at or datetime.now(timezone.utc),
                state="running",
                supersedes_attempt_id=previous_attempt.id if previous_attempt else None,
                skill=skill_invocation.skill if skill_invocation else None,
                skill_version=skill_invocation.skill_version if skill_invocation else None,
                stage=skill_invocation.stage if skill_invocation else None,
                invocation_id=skill_invocation.invocation_id if skill_invocation else None,
            )
            try:
                handle = self._runtime.start(
                    RunSpec(
                        run_id=run_id,
                        work_item_id=dispatch.work_item_id,
                        prompt=prompts[dispatch.work_item_id],
                        workspace_path=workspace.worktree_path,
                        work_item=self._work_items.get(dispatch.work_item_id),
                        workspace=workspace,
                        skill=skill_invocation,
                    )
                )
            except Exception:
                self._authority_baselines.pop(dispatch.work_item_id, None)
                try:
                    self._workspace_factory.destroy(workspace)
                except Exception:
                    pass
                raise
            runtime_snapshot = self._runtime_snapshot(handle)
            if previous_attempt is not None:
                self._attempt_history.setdefault(dispatch.work_item_id, []).append(previous_attempt)
            self._attempts[dispatch.work_item_id] = attempt
            self._active[dispatch.work_item_id] = ActiveAssignment(dispatch, workspace, handle, attempt)
            self._record_run(dispatch, workspace, prompts[dispatch.work_item_id], handle, attempt)
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
                    "runtime_snapshot": runtime_snapshot,
                    "skill": skill_invocation.to_payload() if skill_invocation else None,
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
            self._verify_active_authority(item_id)
        except ValueError as error:
            failure = FailureRecord(
                "authority_violation",
                "runtime",
                "blocking",
                True,
                "The assigned workspace is outside the active skill authority envelope.",
                "Inspect the changed paths and correct the runtime or issue a narrower explicit authority envelope.",
                "Route the item to NeedsAttention and retry only after the workspace is safe.",
            )
            self._mark_runtime_failure(item_id, failure)
            self._append_event(
                "authority.violation",
                {"item_id": item_id, "error": str(error)},
                f"authority-violation:{item_id}:{hashlib.sha256(str(error).encode('utf-8')).hexdigest()}",
            )
            return GateDecision("blocked", failure)
        try:
            workspace = self._workspace_factory.inspect(assignment.workspace)
        except (FileNotFoundError, WorkspaceDriftError) as error:
            failure = classify_failure(error, phase="workspace")
            self._set_ticket_state(item_id, WorkItemState.NEEDS_ATTENTION.value)
            self._attention_state_published.add(item_id)
            self._append_event("reconciliation.failed", {"item_id": item_id, "failure_code": failure.code}, f"reconcile-failure:{item_id}:{failure.code}")
            self._record_failure(item_id, failure)
            return GateDecision("blocked", failure)
        try:
            observation = self._observer.observe(item_id, workspace)
        except Exception as error:
            failure = classify_failure(error, phase="delivery")
            self._append_event("reconciliation.failed", {"item_id": item_id, "failure_code": failure.code}, f"reconcile-failure:{item_id}:{failure.code}")
            self._record_failure(item_id, failure)
            return GateDecision("blocked", failure)
        try:
            observation = self._enrich_observation(item_id, observation)
        except Exception as error:
            failure = classify_failure(error, phase="tracker")
            self._append_event("reconciliation.failed", {"item_id": item_id, "failure_code": failure.code}, f"reconcile-failure:{item_id}:{failure.code}")
            self._record_failure(item_id, failure)
            return GateDecision("blocked", failure)
        self._simulator.observe_delivery(item_id, observation)
        if observation.pr_head_sha and item_id not in self._review_state_published:
            self._set_ticket_state(item_id, WorkItemState.IN_REVIEW.value)
            self._review_state_published.add(item_id)
        decision = self._simulator.evaluate_gate(item_id)
        self._record_domain_evidence(item_id, observation, decision)
        self._record_wave_state()
        self._record_decision(item_id, assignment.handle, decision)
        self._append_event(
            "gate.pending" if decision.status == "pending" else "gate.decided",
            {"item_id": item_id, "status": decision.status, "failure": decision.failure.code if decision.failure else None},
            ":".join(
                (
                    "gate",
                    assignment.handle.run_id,
                    decision.status,
                    decision.failure.code if decision.failure else "none",
                    observation.pr_head_sha or "no-head",
                    observation.ci_head_sha or "no-ci-head",
                    observation.merge_revision or "no-merge",
                )
            ),
        )
        if decision.status == "approved":
            if item_id not in self._done_state_published:
                self._set_ticket_state(item_id, WorkItemState.DONE.value)
                self._done_state_published.add(item_id)
                self._attempts[item_id] = replace(self._attempts[item_id], state="released")
                self._append_event("work_attempt.state_changed", {"run_id": assignment.handle.run_id, "state": "released"}, f"attempt-state:{assignment.handle.run_id}:released")
            self._active.pop(item_id, None)
            if self._base_revision_provider and self._simulator.can_refresh_target_base():
                self.refresh_target_base()
                self._dispatch_next_frontier()
        elif decision.status == "blocked" and item_id not in self._attention_state_published:
            self._set_ticket_state(item_id, WorkItemState.NEEDS_ATTENTION.value)
            self._attention_state_published.add(item_id)
            self._attempts[item_id] = replace(self._attempts[item_id], state="needs_attention")
            self._append_event("work_attempt.state_changed", {"run_id": assignment.handle.run_id, "state": "needs_attention"}, f"attempt-state:{assignment.handle.run_id}:needs_attention")
            if decision.failure is not None:
                self._record_failure(item_id, decision.failure)
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

    def runtime_events(self, item_id: str) -> tuple[AgentEvent, ...]:
        assignment = self.active_assignment(item_id)
        if not assignment.runtime_attached:
            return ()
        return tuple(self._runtime.stream(assignment.handle))

    def retry(self, item_id: str, *, prompt: str | None = None) -> tuple[Dispatch, ...]:
        """Supersede one active attempt and create an isolated replacement."""
        assignment = self.active_assignment(item_id)
        previous = self._attempts[item_id]
        if previous.state == "released":
            raise ValueError(f"released item cannot be retried: {item_id}")
        try:
            if assignment.runtime_attached:
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
        if assignment.runtime_attached:
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

    def skill_results(self, item_id: str) -> tuple[Mapping[str, object], ...]:
        """Return skill results bound to the current or historical attempts."""
        return tuple(self._skill_results.get(item_id, ()))

    def execution_wave(self) -> ExecutionWave:
        wave = self._simulator.current_execution_wave()
        if wave is None:
            raise ValueError("no execution wave has been dispatched")
        return wave

    def observe_runtime_events(self, item_id: str, events: list[AgentEvent]) -> FailureRecord | None:
        for event in events:
            if event.kind == "skill.stage_started":
                try:
                    self._record_skill_stage_started(item_id, event.payload)
                except ValueError as error:
                    failure = FailureRecord(
                        "invalid_skill_provenance",
                        "runtime",
                        "blocking",
                        False,
                        "The lifecycle stage identity did not match the assigned attempt.",
                        "Emit a source-bound stage envelope with the active run, attempt, workspace, and authority.",
                        "Inspect the stage event and retry the attempt after correcting the runtime adapter.",
                    )
                    self._mark_runtime_failure(item_id, failure)
                    self._append_event(
                        "skill.stage.rejected",
                        {"item_id": item_id, "error": str(error)},
                        f"skill-stage-rejected:{item_id}:{hashlib.sha256(str(error).encode('utf-8')).hexdigest()}",
                    )
                    return failure
            if event.kind == "skill.result":
                try:
                    result = self._record_skill_result(item_id, event.payload)
                except ValueError as error:
                    failure = FailureRecord(
                        "invalid_skill_result",
                        "runtime",
                        "blocking",
                        False,
                        "The skill result was malformed or did not match the assigned skill.",
                        "Emit the versioned skill-result contract for the assigned work item and attempt.",
                        "Inspect the result payload, then reconcile or retry the attempt after correction.",
                    )
                    self._mark_runtime_failure(item_id, failure)
                    self._append_event(
                        "skill.result.rejected",
                        {"item_id": item_id, "error": str(error)},
                        f"skill-result-rejected:{item_id}:{hashlib.sha256(str(error).encode('utf-8')).hexdigest()}",
                    )
                    return failure
                if result.status in {"blocked", "failed", "needs_input"}:
                    failure = FailureRecord(
                        f"skill_result_{result.status}",
                        "runtime",
                        "blocking",
                        result.status == "failed",
                        f"The skill reported status {result.status!r} for the active stage.",
                        "Use the structured findings and next_actions to correct the stage or request human input.",
                        "Route the item to NeedsAttention and retry only after the stage evidence is understood.",
                    )
                    self._mark_runtime_failure(item_id, failure)
                    return failure
            if event.kind == "runtime.exited" and item_id in self._skill_invocations:
                try:
                    self._verify_active_authority(item_id)
                except ValueError as error:
                    failure = FailureRecord(
                        "authority_violation",
                        "runtime",
                        "blocking",
                        True,
                        "The runtime changed workspace state outside the assigned authority envelope.",
                        "Inspect the changed paths and narrow the command or issue a new explicit authority envelope.",
                        "Route the item to NeedsAttention and retry only after the authority boundary is corrected.",
                    )
                    self._mark_runtime_failure(item_id, failure)
                    self._append_event(
                        "authority.violation",
                        {"item_id": item_id, "error": str(error)},
                        f"authority-violation:{item_id}:{hashlib.sha256(str(error).encode('utf-8')).hexdigest()}",
                    )
                    return failure
                invocation = self._skill_invocations[item_id]
                if (
                    event.payload.get("returncode") in {0, None}
                    and self._skill_result_seen.get(item_id) != invocation.invocation_id
                ):
                    failure = FailureRecord(
                        "missing_skill_result",
                        "runtime",
                        "blocking",
                        True,
                        "The skill runtime exited without a result for the active invocation.",
                        "Emit one source-bound skill.result event for the active stage before exiting.",
                        "Route the item to NeedsAttention and retry after fixing the runtime adapter.",
                    )
                    self._mark_runtime_failure(item_id, failure)
                    return failure
            code = classify_runtime_event(event)
            if code is None:
                continue
            if code == "authority_violation":
                failure = FailureRecord(
                    "authority_violation",
                    "runtime",
                    "blocking",
                    True,
                    "The runtime reported a workspace authority violation.",
                    "Inspect the reported paths and correct the runtime or issue a narrower explicit authority envelope.",
                    "Route the item to NeedsAttention and retry only after the authority boundary is corrected.",
                )
                self._mark_runtime_failure(item_id, failure)
                return failure
            failure = FailureRecord(
                code,
                "runtime",
                "blocking",
                code in {"agent_timeout", "runtime_failed"},
                "The agent runtime did not complete successfully.",
                "Do not treat the runtime claim as delivery evidence; inspect and retry the attempt.",
                "Route the item to NeedsAttention and reconcile a new attempt after the workspace is safe.",
            )
            self._mark_runtime_failure(item_id, failure)
            return failure
        return None

    def _mark_runtime_failure(self, item_id: str, failure: FailureRecord) -> None:
        if item_id not in self._attention_state_published:
            self._set_ticket_state(item_id, WorkItemState.NEEDS_ATTENTION.value)
            self._attention_state_published.add(item_id)
        if item_id in self._attempts:
            assignment = self._active.get(item_id)
            self._attempts[item_id] = replace(self._attempts[item_id], state="needs_attention")
            if assignment is not None:
                self._append_event(
                    "work_attempt.state_changed",
                    {"run_id": assignment.handle.run_id, "state": "needs_attention"},
                    f"attempt-state:{assignment.handle.run_id}:needs_attention",
                )
        self._append_event(
            "runtime.failed",
            {"item_id": item_id, "failure_code": failure.code},
            f"runtime-failure:{item_id}:{failure.code}",
        )
        self._record_failure(item_id, failure)

    def _record_skill_stage_started(self, item_id: str, event_payload: Mapping[str, object]) -> None:
        assignment = self._active.get(item_id)
        if assignment is None:
            raise ValueError(f"cannot bind a lifecycle stage without an active attempt: {item_id}")
        invocation_payload = event_payload.get("invocation")
        if not isinstance(invocation_payload, Mapping):
            raise ValueError("skill.stage_started is missing invocation")
        invocation = SkillInvocation.from_payload(invocation_payload)
        if event_payload.get("run_id") != assignment.handle.run_id:
            raise ValueError("skill stage run_id does not match the active run")
        if event_payload.get("attempt_id") != assignment.attempt.id:
            raise ValueError("skill stage attempt_id does not match the active attempt")
        if event_payload.get("workspace_id") != assignment.workspace.workspace_id:
            raise ValueError("skill stage workspace_id does not match the active workspace")
        if invocation.work_item_id != item_id or invocation.attempt_id != assignment.attempt.id:
            raise ValueError("skill stage invocation is not bound to the active work item and attempt")
        if invocation.authority is None:
            raise ValueError("skill stage invocation has no authority envelope")
        previous = self._skill_invocations.get(item_id)
        if previous is None:
            raise ValueError("skill stage has no initial invocation provenance")
        if invocation.skill_version != previous.skill_version:
            raise ValueError("skill stage changed the skill pack version")
        if invocation.manifest_ref != previous.manifest_ref or invocation.manifest_sha256 != previous.manifest_sha256:
            raise ValueError("skill stage changed manifest provenance")
        expected_invocation_id = f"invocation:{assignment.attempt.id}:{invocation.stage}:{invocation.skill}"
        if invocation.invocation_id != expected_invocation_id:
            raise ValueError("skill stage invocation_id is not deterministic for the active attempt")
        if invocation.authority.is_expired():
            raise ValueError("skill stage authority has expired")
        self._skill_invocations[item_id] = invocation
        self._authority_baselines[item_id] = self._authority_verifier.capture(assignment.workspace)
        self._skill_result_seen[item_id] = None
        self._attempts[item_id] = replace(
            assignment.attempt,
            stage=invocation.stage,
            invocation_id=invocation.invocation_id,
            skill=invocation.skill,
            skill_version=invocation.skill_version,
        )
        self._active[item_id] = replace(assignment, attempt=self._attempts[item_id])
        payload = {
            "item_id": item_id,
            "run_id": assignment.handle.run_id,
            "attempt_id": assignment.attempt.id,
            "workspace_id": assignment.workspace.workspace_id,
            "invocation": invocation.to_payload(),
        }
        event_id = event_payload.get("event_id") or f"stage-started:{assignment.handle.run_id}:{invocation.invocation_id}"
        self._append_event("skill.stage_started", payload, str(event_id))

    def _record_skill_result(self, item_id: str, event_payload: Mapping[str, object]) -> SkillResult:
        assignment = self._active.get(item_id)
        if assignment is None:
            raise ValueError(f"cannot bind a skill result without an active attempt: {item_id}")
        source = SkillResultEvent.from_payload(event_payload)
        result = source.result
        if source.run_id != assignment.handle.run_id:
            raise ValueError("skill result run_id does not match the active run")
        if source.attempt_id != assignment.attempt.id:
            raise ValueError("skill result attempt_id does not match the active attempt")
        if source.workspace_id != assignment.workspace.workspace_id:
            raise ValueError("skill result workspace_id does not match the active workspace")
        expected = self._skill_invocations.get(item_id)
        if expected is None:
            raise ValueError("skill result cannot be accepted without an assigned invocation")
        if source.invocation_id != expected.invocation_id:
            raise ValueError("skill result invocation_id does not match the active invocation")
        if result.work_item_id != item_id:
            raise ValueError(
                f"skill result work_item_id {result.work_item_id!r} does not match active item {item_id!r}"
            )
        if (
            result.skill != expected.skill
            or result.skill_version != expected.skill_version
            or result.stage != expected.stage
        ):
            raise ValueError(
                "skill result identity does not match the assigned invocation "
                f"({expected.skill}@{expected.skill_version}/{expected.stage})"
            )
        if result.result_id != source.result.result_id:
            raise ValueError("skill result identity is internally inconsistent")
        authority = expected.authority
        if authority is None:
            raise ValueError("assigned invocation has no authority envelope")
        baseline = self._authority_baselines.get(item_id)
        if baseline is None:
            raise ValueError("skill result has no authority baseline")
        authority_observation = self._authority_verifier.verify(assignment.workspace, authority, baseline)
        result_payload = result.to_payload()
        artifact_bindings = []
        for artifact in result.artifacts:
            verification = dict(self._artifact_verifier.verify(artifact, assignment.workspace))
            if verification.get("observed_sha256") != artifact.sha256:
                raise ValueError(f"artifact verifier did not confirm the declared hash for {artifact.uri}")
            verified_scope = verification.get("verified_scope")
            if not isinstance(verified_scope, str) or not verified_scope.strip():
                raise ValueError(f"artifact verifier did not return a verified scope for {artifact.uri}")
            artifact_bindings.append(
                {
                    "artifact_id": artifact.artifact_id,
                    "uri": artifact.uri,
                    "sha256": artifact.sha256,
                    "media_type": artifact.media_type,
                    "run_id": assignment.handle.run_id,
                    "attempt_id": assignment.attempt.id,
                    "workspace_id": assignment.workspace.workspace_id,
                    "verified_scope": verified_scope,
                    "observed_sha256": verification["observed_sha256"],
                }
            )
        event_payload_for_log: dict[str, object] = {
            "item_id": item_id,
            "event_id": source.event_id,
            "run_id": source.run_id,
            "invocation_id": source.invocation_id,
            "attempt_id": source.attempt_id,
            "workspace_id": source.workspace_id,
            "skill": result.skill,
            "skill_version": result.skill_version,
            "stage": result.stage,
            "result_id": result.result_id,
            "result": result_payload,
            "artifact_bindings": artifact_bindings,
            "authority_observation": dict(authority_observation),
        }
        fingerprint = hashlib.sha256(
            json.dumps(event_payload_for_log, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        event_key = f"skill-result:{source.run_id}:{source.attempt_id}:{result.result_id}"
        if event_key in self._recorded_skill_result_keys:
            if self._recorded_skill_result_fingerprints.get(event_key) not in {None, fingerprint}:
                raise ValueError("skill result idempotency key was reused for different content")
            return result
        event_payload_for_log["event_key"] = event_key
        self._skill_results.setdefault(item_id, []).append(event_payload_for_log)
        self._recorded_skill_result_keys.add(event_key)
        self._recorded_skill_result_fingerprints[event_key] = fingerprint
        self._skill_result_seen[item_id] = source.invocation_id
        self._append_event("skill.result.recorded", event_payload_for_log, event_key)
        self._authority_baselines[item_id] = self._authority_verifier.capture(assignment.workspace)
        return result

    def _verify_active_authority(self, item_id: str) -> Mapping[str, object] | None:
        assignment = self._active.get(item_id)
        invocation = self._skill_invocations.get(item_id)
        baseline = self._authority_baselines.get(item_id)
        if assignment is None or invocation is None or invocation.authority is None or baseline is None:
            return None
        observation = self._authority_verifier.verify(assignment.workspace, invocation.authority, baseline)
        self._authority_baselines[item_id] = self._authority_verifier.capture(assignment.workspace)
        return observation

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
        pull_request = None
        if observation.pr_head_sha:
            pull_request = PullRequest(
                observation.pr_id,
                item_id,
                observation.pr_url,
                observation.pr_head_sha,
                observation.base_sha_at_open or observation.base_revision,
                "pending" if observation.ci_pending else ("passing" if observation.ci_passed else "failing"),
                observation.ci_head_sha,
                evidence.reviews_resolved,
                observation.merged,
                observation.merge_revision,
                self._active[item_id].attempt.id,
            )
        previous_evidence = self._validation_evidence.get(item_id)
        previous_pull_request = self._pull_requests.get(item_id)
        if (
            previous_evidence is not None
            and replace(evidence, collected_at=previous_evidence.collected_at) == previous_evidence
            and pull_request == previous_pull_request
        ):
            evidence = previous_evidence
        self._validation_evidence[item_id] = evidence
        self._human_gates[item_id] = HumanGate(
            item_id,
            True,
            decision.status == "approved" and observation.merged,
            observation.merged_by,
            now if decision.status == "approved" and observation.merged else None,
        )
        if pull_request is not None:
            self._pull_requests[item_id] = pull_request
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
        event_digest = hashlib.sha256(
            json.dumps(evidence_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self._append_event(
            "validation.evidence_recorded",
            evidence_payload,
            f"validation:{self._active[item_id].handle.run_id}:{event_digest}",
        )

    def _record_run(
        self,
        dispatch: Dispatch,
        workspace: Workspace,
        prompt: str,
        handle: RunHandle,
        attempt: WorkAttempt,
    ) -> None:
        emission_id = f"run:{handle.run_id}"
        if self._recorder is None or emission_id in self._arp_emissions:
            return
        if self._project_snapshot is None:
            prompt_digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            input_ref = f"urn:mergewave:work-item-prompt:{dispatch.work_item_id}:{prompt_digest}"
            input_hash = f"sha256:{prompt_digest}"
        else:
            input_ref = self._project_snapshot.ref
            input_hash = self._project_snapshot.digest
        configuration_hash = hashlib.sha256(
            json.dumps(
                {
                    "runtime": type(self._runtime).__qualname__,
                    "scheduling_policy": self._simulator.scheduling_policy,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        wave = self._simulator.current_execution_wave()
        self._recorder.record_run(
            run_id=handle.run_id,
            base_revision=dispatch.base_revision,
            input_ref=input_ref,
            input_hash=input_hash,
            executor_name=type(self._runtime).__qualname__,
            executor_version="1",
            configuration_hash=f"sha256:{configuration_hash}",
            capture_policy="metadata",
            environment={"runtime": type(self._runtime).__qualname__},
            extensions={
                "work_item_id": dispatch.work_item_id,
                "attempt_id": attempt.id,
                "wave_id": wave.wave_id if wave is not None else None,
                "workspace_id": workspace.workspace_id,
                "repository": workspace.repository,
                "worktree_path": workspace.worktree_path,
                "branch_ref": workspace.branch_ref,
                "skill": self._skill_invocations[dispatch.work_item_id].to_payload()
                if dispatch.work_item_id in self._skill_invocations
                else None,
            },
        )
        self._record_arp_emission(emission_id, handle.run_id, "run")

    def _record_decision(self, item_id: str, handle: RunHandle, decision: GateDecision) -> None:
        if self._recorder is None:
            return

        artifact = self._delivery_artifact(item_id, handle)
        observation_identity = {key: value for key, value in artifact.items() if key != "observed_at"}
        encoded = json.dumps(observation_identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        observation_digest = hashlib.sha256(encoded).hexdigest()
        evidence_ids = (
            f"evidence:{handle.run_id}:{observation_digest}:revision",
            f"evidence:{handle.run_id}:{observation_digest}:checks",
            f"evidence:{handle.run_id}:{observation_digest}:acceptance",
        )
        if evidence_ids[0] not in self._recorded_evidence_ids and all(
            f"evidence:{evidence_id}" not in self._arp_emissions for evidence_id in evidence_ids
        ):
            evidence = self._validation_evidence[item_id]
            pull_request = self._pull_requests.get(item_id)
            revision_matches = bool(
                pull_request
                and evidence.base_sha_verified
                and evidence.ci_verified_against_head
            )
            required_checks_satisfied = bool(
                pull_request
                and (
                    decision.status == "approved"
                    or (
                        decision.status == "pending"
                        and decision.failure is not None
                        and decision.failure.code == "merge_not_observed"
                    )
                )
            )
            self._recorder.record_evidence(
                evidence_id=evidence_ids[0],
                run_id=handle.run_id,
                claim="artifact_matches_expected_revision",
                observed=revision_matches,
                expected=True,
                comparator="equals",
                stage="final_artifact",
                capture_policy="metadata",
                artifact_payload=artifact,
            )
            self._recorder.record_evidence(
                evidence_id=evidence_ids[1],
                run_id=handle.run_id,
                claim="required_checks_satisfied",
                observed=required_checks_satisfied,
                expected=True,
                comparator="equals",
                stage="final_artifact",
                capture_policy="metadata",
                artifact_payload=artifact,
            )
            self._recorder.record_evidence(
                evidence_id=evidence_ids[2],
                run_id=handle.run_id,
                claim="acceptance_criteria_signal",
                observed=evidence.acceptance_criteria_signal,
                expected="complete",
                comparator="informational",
                stage="pre_final",
                capture_policy="metadata",
                artifact_payload=artifact,
            )
            self._recorded_evidence_ids.update(evidence_ids)
            for evidence_id in evidence_ids:
                self._record_arp_emission(f"evidence:{evidence_id}", handle.run_id, "evidence")

        is_human_gate_ready = decision.status == "approved" or (
            decision.status == "pending"
            and decision.failure is not None
            and decision.failure.code == "merge_not_observed"
        )
        gate_request_emission = f"gate-request:{self._gate_id(handle)}"
        if is_human_gate_ready and item_id not in self._gate_request_evidence and gate_request_emission not in self._arp_emissions:
            authoritative_evidence_ids = evidence_ids[:2]
            self._gate_request_evidence[item_id] = authoritative_evidence_ids
            self._recorder.record_gate_request(
                gate_id=self._gate_id(handle),
                run_id=handle.run_id,
                checkpoint="merge",
                policy_version="mergewave/v0.1",
                decision_authority="human",
                required_evidence_ids=authoritative_evidence_ids,
                capture_policy="metadata",
                extensions=self._delivery_extensions(item_id),
            )
            self._record_arp_emission(gate_request_emission, handle.run_id, "gate_request")

        gate_decision_emission = f"gate-decision:{self._gate_id(handle)}:approved"
        if decision.status == "approved" and self._recorded_decisions.get(item_id) != "approved" and gate_decision_emission not in self._arp_emissions:
            self._recorded_decisions[item_id] = "approved"
            requested_evidence = self._gate_request_evidence.get(item_id, ())
            self._recorder.record_gate_decision(
                gate_id=self._gate_id(handle),
                run_id=handle.run_id,
                decision="approved",
                checkpoint="merge",
                policy_version="mergewave/v0.1",
                decision_authority="human",
                evidence_ids=tuple(dict.fromkeys((*requested_evidence, *evidence_ids))),
                capture_policy="metadata",
                extensions=self._delivery_extensions(item_id),
            )
            self._record_arp_emission(gate_decision_emission, handle.run_id, "gate_decision")

    @staticmethod
    def _gate_id(handle: RunHandle) -> str:
        return f"gate:{handle.run_id}"

    def _append_event(self, kind: str, payload: Mapping[str, object], key: str) -> None:
        if self._event_log is not None:
            self._event_log.append(kind, payload, idempotency_key=key)

    def _record_arp_emission(self, emission_id: str, run_id: str, kind: str) -> None:
        self._arp_emissions.add(emission_id)
        self._append_event(
            "arp.emission_recorded",
            {"emission_id": emission_id, "run_id": run_id, "kind": kind},
            f"arp-emission:{emission_id}",
        )

    def _persist_prompt_snapshots(self, prompts: Mapping[str, str]) -> None:
        for item_id, prompt in prompts.items():
            digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            existing = self._prompts.get(item_id)
            if existing is not None and existing != prompt:
                raise ValueError(f"prompt snapshot changed for {item_id}")
            self._prompts[item_id] = prompt
            self._append_event(
                "prompt.snapshot_recorded",
                {
                    "item_id": item_id,
                    "prompt": prompt,
                    "prompt_ref": f"urn:mergewave:work-item-prompt:{item_id}:{digest}",
                    "prompt_hash": f"sha256:{digest}",
                },
                f"prompt:{item_id}:{digest}",
            )

    def _persist_project_snapshot(self) -> None:
        if self._project_snapshot is None:
            return
        self._append_event(
            "project.snapshot_recorded",
            {"ref": self._project_snapshot.ref, "digest": self._project_snapshot.digest},
            f"project-snapshot:{self._project_snapshot.digest}",
        )

    def _runtime_snapshot(self, handle: RunHandle) -> Mapping[str, object]:
        snapshot = getattr(self._runtime, "snapshot", None)
        if not callable(snapshot):
            return {}
        value = snapshot(handle)
        return dict(value)

    def _validate_skill_template(self, invocation: SkillInvocation) -> None:
        if invocation.is_bound:
            raise ValueError("skill invocation templates must not be bound to a previous attempt")
        if invocation.skill_version != SKILL_PACK_VERSION:
            raise ValueError(
                f"unsupported skill pack version {invocation.skill_version!r}; expected {SKILL_PACK_VERSION!r}"
            )
        if invocation.manifest_ref is None or invocation.manifest_sha256 is None:
            raise ValueError("skill dispatch requires manifest_ref and manifest_sha256 provenance")
        if invocation.authority is None:
            raise ValueError("skill dispatch requires an explicit authority envelope")
        if invocation.authority.is_expired():
            raise ValueError("skill dispatch authority has expired")
        if self._manifest_verifier is None:
            raise ValueError("a manifest_verifier is required before dispatching a skill")
        self._manifest_verifier.verify(invocation)
        capabilities = getattr(self._runtime, "capabilities", None)
        if callable(capabilities):
            reported = capabilities()
            if reported is not None and not getattr(reported, "supports_authority", False):
                raise ValueError("configured runtime does not advertise authority-envelope support")

    @staticmethod
    def _unbound_skill_template(invocation: SkillInvocation) -> SkillInvocation:
        return SkillInvocation(
            skill=invocation.skill,
            skill_version=invocation.skill_version,
            stage=invocation.stage,
            manifest_ref=invocation.manifest_ref,
            manifest_sha256=invocation.manifest_sha256,
            authority=invocation.authority,
        )

    def _record_failure(self, item_id: str, failure: FailureRecord) -> str:
        assignment = self._active.get(item_id)
        run_id = assignment.handle.run_id if assignment is not None else item_id
        payload = asdict(failure)
        digest = hashlib.sha256(
            json.dumps({"run_id": run_id, **payload}, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        evidence_id = f"failure:{run_id}:{digest}"
        if evidence_id in self._failure_records:
            return evidence_id
        self._failure_records[evidence_id] = failure
        self._append_event(
            "failure.recorded",
            {"item_id": item_id, "run_id": run_id, "evidence_id": evidence_id, **payload},
            evidence_id,
        )
        message = (
            f"MergeWave failure `{failure.code}` ({evidence_id})\n\n"
            f"{failure.human_summary}\n\nNext action: {failure.suggested_action}"
        )
        post_comment = getattr(self._tracker, "post_comment", None)
        if callable(post_comment):
            post_comment(item_id, message)
        if assignment is not None and assignment.runtime_attached:
            capabilities = getattr(self._runtime, "capabilities", None)
            continue_run = getattr(self._runtime, "continue_run", None)
            if callable(capabilities) and capabilities().supports_continue and callable(continue_run):
                continue_run(assignment.handle, f"{failure.agent_guidance}\n{failure.suggested_action}")
        return evidence_id

    def _delivery_artifact(self, item_id: str, handle: RunHandle) -> dict[str, object]:
        evidence = self._validation_evidence[item_id]
        pull_request = self._pull_requests.get(item_id)
        return json.loads(
            json.dumps(
                {
                    "work_item_id": item_id,
                    "attempt_id": self._active[item_id].attempt.id,
                    "base_revision": self._active[item_id].dispatch.base_revision,
                    "head_revision": pull_request.head_sha if pull_request else None,
                    "ci_checked_revision": pull_request.ci_checked_head_sha if pull_request else None,
                    "ci_status": pull_request.ci_status if pull_request else "unknown",
                    "reviews_resolved": evidence.reviews_resolved,
                    "scope_check": evidence.scope_check,
                    "acceptance_criteria_signal": evidence.acceptance_criteria_signal,
                    "merged": pull_request.merged if pull_request else False,
                    "merge_revision": pull_request.merge_commit_sha if pull_request else None,
                    "observed_at": evidence.collected_at,
                },
                default=str,
                sort_keys=True,
            )
        )

    def _delivery_extensions(self, item_id: str) -> dict[str, object]:
        assignment = self._active[item_id]
        wave = self._simulator.current_execution_wave()
        pull_request = self._pull_requests.get(item_id)
        return {
            "work_item_id": item_id,
            "attempt_id": assignment.attempt.id,
            "wave_id": wave.wave_id if wave is not None else None,
            "workspace_id": assignment.workspace.workspace_id,
            "pull_request_ref": pull_request.url if pull_request else None,
            "head_revision": pull_request.head_sha if pull_request else None,
            "merge_revision": pull_request.merge_commit_sha if pull_request else None,
        }

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
        if (
            projection.project_snapshot is not None
            and self._project_snapshot is not None
            and projection.project_snapshot.get("digest") != self._project_snapshot.digest
        ):
            raise ValueError("project snapshot changed since the durable controller state was recorded")
        self._prompts.update(
            {
                item_id: str(payload["prompt"])
                for item_id, payload in projection.prompts.items()
                if isinstance(payload.get("prompt"), str)
            }
        )
        self._arp_emissions.update(projection.arp_emissions)
        self._review_state_published.update(
            item_id
            for item_id, state in projection.ticket_states.items()
            if state in {WorkItemState.IN_REVIEW.value, WorkItemState.NEEDS_ATTENTION.value, WorkItemState.DONE.value}
        )
        self._attention_state_published.update(
            item_id
            for item_id, state in projection.ticket_states.items()
            if state == WorkItemState.NEEDS_ATTENTION.value
        )
        self._done_state_published.update(
            item_id
            for item_id, state in projection.ticket_states.items()
            if state == WorkItemState.DONE.value
        )
        for payload in projection.failure_records.values():
            evidence_id = str(payload["evidence_id"])
            self._failure_records[evidence_id] = FailureRecord(
                code=str(payload["code"]), phase=str(payload["phase"]), severity=str(payload["severity"]),
                retryable=bool(payload["retryable"]), human_summary=str(payload["human_summary"]),
                agent_guidance=str(payload["agent_guidance"]), suggested_action=str(payload["suggested_action"]),
            )
        if projection.base_revision is not None:
            self._simulator.refresh_target_base(projection.base_revision)
        self._simulator.restore_released(
            tuple(
                item_id
                for item_id, state in projection.ticket_states.items()
                if state in {WorkItemState.DONE.value, WorkItemState.CANCELLED.value}
            )
        )
        active_wave_items: set[str] = set()
        for wave_id, payload in projection.wave_records.items():
            wave = ExecutionWave(
                wave_id=str(wave_id),
                base_sha=str(payload["base_revision"] if "base_revision" in payload else payload["base_sha"]),
                work_item_ids=tuple(str(item_id) for item_id in payload.get("work_item_ids", ())),
                state=str(payload.get("state", "active")),
            )
            self._waves[wave.wave_id] = wave
            self._simulator.restore_execution_wave(wave)
            if wave.state == "active":
                active_wave_items.update(wave.work_item_ids)
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
            raw_skill = attempt_record.get("skill") or payload.get("skill")
            skill_invocation = None
            if isinstance(raw_skill, Mapping):
                skill_invocation = SkillInvocation.from_payload(raw_skill)
                self._skill_invocations[str(item_id)] = skill_invocation
                self._skill_templates[str(item_id)] = self._unbound_skill_template(skill_invocation)
            attempt = WorkAttempt(
                id=str(attempt_record["attempt_id"]),
                work_item_id=str(item_id),
                base_sha=str(attempt_record["base_revision"]),
                workspace_id=str(attempt_record["workspace_id"]),
                agent_runtime="recovered",
                started_at=started_at,
                state=str(attempt_record.get("state", "running")),
                skill=skill_invocation.skill if skill_invocation else None,
                skill_version=skill_invocation.skill_version if skill_invocation else None,
                stage=skill_invocation.stage if skill_invocation else None,
                invocation_id=skill_invocation.invocation_id if skill_invocation else None,
            )
            dispatch = Dispatch(
                work_item_id=str(item_id),
                base_revision=str(payload["base_revision"]),
                repository=workspace.repository,
                worktree_path=workspace.worktree_path,
                branch_ref=workspace.branch_ref,
            )
            runtime_attached = False
            handle = RunHandle(run_id, None)
            capabilities = getattr(self._runtime, "capabilities", None)
            reattach = getattr(self._runtime, "reattach", None)
            runtime_snapshot = payload.get("runtime_snapshot", {})
            if callable(capabilities) and capabilities().supports_reattach and callable(reattach) and isinstance(runtime_snapshot, Mapping):
                try:
                    handle = reattach(run_id, runtime_snapshot)
                    runtime_attached = True
                except Exception:
                    runtime_attached = False
            if not runtime_attached:
                attempt = replace(attempt, state="orphaned")
                self._append_event(
                    "runtime.orphaned",
                    {"item_id": item_id, "run_id": run_id, "reason": "runtime_not_reattachable"},
                    f"runtime-orphaned:{run_id}",
                )
            self._attempts[str(item_id)] = attempt
            self._active[str(item_id)] = ActiveAssignment(
                dispatch, workspace, handle, attempt, runtime_attached
            )
            if skill_invocation is not None:
                self._skill_result_seen.setdefault(str(item_id), None)
                try:
                    self._authority_baselines[str(item_id)] = self._authority_verifier.capture(workspace)
                except ValueError:
                    # Rehydration remains possible so the controller can expose
                    # the failed provenance on the next runtime event.
                    pass
            self._simulator.restore_dispatch(
                dispatch,
                active_wave=str(item_id) in active_wave_items,
            )
            restore_run = getattr(self._recorder, "restore_run", None)
            if callable(restore_run) and f"run:{run_id}" in self._arp_emissions:
                gate_requested = f"gate-request:{self._gate_id(handle)}" in self._arp_emissions
                restore_run(run_id, sequence_number=3 if gate_requested else 2)
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
        for item_id, records in projection.skill_results.items():
            self._skill_results[item_id] = [dict(record) for record in records]
            if records:
                last_invocation_id = records[-1].get("invocation_id")
                if isinstance(last_invocation_id, str):
                    self._skill_result_seen[item_id] = last_invocation_id
            self._recorded_skill_result_keys.update(
                str(record["event_key"])
                for record in records
                if record.get("event_key")
            )
            self._recorded_skill_result_fingerprints.update(
                (
                    str(record["event_key"]),
                    hashlib.sha256(
                        json.dumps(
                            {key: value for key, value in record.items() if key != "event_key"},
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest(),
                )
                for record in records
                if record.get("event_key")
            )


__all__ = ["ActiveAssignment", "ControllerProjection", "DeliveryController"]
