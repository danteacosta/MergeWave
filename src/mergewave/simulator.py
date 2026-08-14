"""Deterministic, provider-free simulation of MergeWave delivery gates."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeliveryObservation:
    repository: str
    worktree_path: str
    branch_ref: str
    base_revision: str
    initial_head_revision: str
    current_head_revision: str
    pr_head_sha: str
    ci_head_sha: str
    ci_passed: bool
    approvals: int
    scope_ok: bool
    merged: bool
    merge_revision: str | None
    base_is_ancestor: bool


@dataclass(frozen=True)
class Dispatch:
    work_item_id: str
    base_revision: str
    repository: str
    worktree_path: str
    branch_ref: str


@dataclass(frozen=True)
class Event:
    kind: str
    item_id: str | None = None


@dataclass(frozen=True)
class FailureRecord:
    code: str
    phase: str
    severity: str
    retryable: bool
    human_summary: str
    agent_guidance: str
    suggested_action: str


@dataclass(frozen=True)
class GateDecision:
    status: str
    failure: FailureRecord | None = None


class MergeWaveSimulator:
    """Run the first MergeWave contract without external systems."""

    def __init__(
        self,
        work_items: list[dict[str, object]],
        *,
        policy: str,
        base_revision: str,
        repository: str = "demo-repository",
        workspace_root: str = "/worktrees",
    ) -> None:
        if policy not in {"continuous_frontier", "wave_barrier"}:
            raise ValueError(f"unsupported scheduling policy: {policy}")

        self._policy = policy
        self._base_revision = base_revision
        self._repository = repository
        self._workspace_root = workspace_root.rstrip("/")
        self._blockers = {
            str(item["id"]): frozenset(str(blocker) for blocker in item.get("blocked_by", []))
            for item in work_items
        }
        self._dispatched: dict[str, Dispatch] = {}
        self._observations: dict[str, DeliveryObservation] = {}
        self._decisions: dict[str, GateDecision] = {}
        self._claims: dict[str, str] = {}
        self._events: list[Event] = []
        self._active_wave: set[str] = set()
        self._wave_released = True

    def dispatch_ready(self) -> tuple[Dispatch, ...]:
        if self._policy == "wave_barrier" and not self._wave_released:
            return ()

        ready = []
        for item_id in self._blockers:
            if item_id in self._dispatched:
                continue
            if not all(
                self._decisions.get(blocker, GateDecision("pending")).status == "approved"
                for blocker in self._blockers[item_id]
            ):
                continue
            dispatch = Dispatch(
                work_item_id=item_id,
                base_revision=self._base_revision,
                repository=self._repository,
                worktree_path=f"{self._workspace_root}/{item_id}",
                branch_ref=f"mergewave/{item_id}",
            )
            self._dispatched[item_id] = dispatch
            self._events.append(Event("dispatch.created", item_id))
            ready.append(dispatch)

        if self._policy == "wave_barrier" and ready:
            self._active_wave = {dispatch.work_item_id for dispatch in ready}
            self._wave_released = False

        return tuple(ready)

    def record_agent_claim(self, item_id: str, claim: str) -> None:
        self._claims[item_id] = claim

    def gate_status(self, item_id: str) -> str:
        return self._decisions.get(item_id, GateDecision("pending")).status

    def observe_delivery(self, item_id: str, observation: DeliveryObservation) -> None:
        if item_id not in self._dispatched:
            raise ValueError(f"item was not dispatched: {item_id}")
        self._observations[item_id] = observation
        self._events.append(Event("delivery.observed", item_id))

    def evaluate_gate(self, item_id: str) -> GateDecision:
        existing = self._decisions.get(item_id)
        if existing is not None:
            return existing

        observation = self._observations.get(item_id)
        if observation is None:
            return GateDecision("pending")

        dispatch = self._dispatched[item_id]
        failure = self._classify(dispatch, observation)
        decision = GateDecision("blocked", failure) if failure else GateDecision("approved")
        self._decisions[item_id] = decision
        self._events.append(Event("gate.decided", item_id))
        return decision

    def trace(self) -> tuple[Event, ...]:
        return tuple(self._events)

    def refresh_target_base(self, revision: str) -> None:
        if self._policy != "wave_barrier":
            self._base_revision = revision
            return
        if not self._active_wave or not all(
            self._decisions.get(item_id, GateDecision("pending")).status == "approved"
            for item_id in self._active_wave
        ):
            raise ValueError("cannot refresh target base before the active wave is approved")
        self._base_revision = revision
        self._active_wave = set()
        self._wave_released = True
        self._events.append(Event("reconciliation.target_base_refreshed"))

    def _classify(
        self,
        dispatch: Dispatch,
        observation: DeliveryObservation,
    ) -> FailureRecord | None:
        if (
            observation.repository != dispatch.repository
            or observation.worktree_path != dispatch.worktree_path
            or observation.branch_ref != dispatch.branch_ref
        ):
            return FailureRecord(
                "workspace_drift",
                "workspace",
                "blocking",
                True,
                "The observed repository, worktree, or branch does not match the assignment.",
                "Stop and report workspace identity drift; do not continue from this workspace.",
                "Discard the workspace and recreate the assigned repository, worktree, and branch.",
            )
        if observation.base_revision != dispatch.base_revision:
            return FailureRecord(
                "workspace_drift",
                "workspace",
                "blocking",
                True,
                "The workspace was observed with a different base revision than assigned.",
                "Do not continue from this workspace; recreate it from the assigned base revision.",
                "Discard the workspace and retry from the recorded base revision.",
            )
        if observation.initial_head_revision != observation.base_revision:
            return FailureRecord(
                "workspace_drift",
                "workspace",
                "blocking",
                False,
                "The workspace was not created at the assigned base revision.",
                "Stop and report that workspace creation violated the initial HEAD invariant.",
                "Recreate the workspace with initial_head_revision equal to base_revision.",
            )
        if observation.ci_head_sha != observation.pr_head_sha:
            return FailureRecord(
                "stale_ci",
                "ci",
                "blocking",
                True,
                "CI passed for an older pull-request head revision.",
                "Wait for checks that target the current pull-request head before requesting release.",
                "Refresh CI and re-evaluate the gate for the current head revision.",
            )
        if not observation.base_is_ancestor:
            return FailureRecord(
                "base_revision_mismatch",
                "delivery",
                "blocking",
                True,
                "The pull-request head does not descend from the assigned base revision.",
                "Rebase or recreate the work from the assigned base; do not release dependents.",
                "Verify ancestry and create a new delivery from the correct base revision.",
            )
        if not observation.scope_ok:
            return FailureRecord(
                "out_of_scope_diff",
                "scope",
                "blocking",
                False,
                "The pull-request diff contains paths outside the declared work-item scope.",
                "Remove unrelated changes or update the work item through the authoring process.",
                "Correct the diff and request a fresh scope observation.",
            )
        if not observation.ci_passed:
            return FailureRecord(
                "ci_failed",
                "ci",
                "blocking",
                True,
                "A required CI check failed for the current pull-request head.",
                "Inspect the failing check and repair the implementation before requesting release.",
                "Fix CI failures and wait for a current green result.",
            )
        if observation.approvals < 1:
            return FailureRecord(
                "review_pending",
                "review",
                "blocking",
                True,
                "The required human review approval has not been observed.",
                "Keep the item in review until the configured approval is recorded.",
                "Request and obtain the required review approval.",
            )
        if not observation.merged:
            return FailureRecord(
                "merge_not_observed",
                "merge",
                "blocking",
                True,
                "The pull request has not been observed in the target base branch.",
                "Do not claim release until the human merge is visible in the target branch.",
                "Wait for the merge and reconcile the target base revision.",
            )
        return None
