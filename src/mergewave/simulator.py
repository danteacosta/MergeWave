"""Deterministic, provider-free simulation of MergeWave delivery gates."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.error import HTTPError

from .contracts import DependencyGraph, WorkItem
from .domain import ExecutionWave
from .scheduler import Scheduler


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
    pr_id: str = ""
    pr_url: str = ""
    base_sha_at_open: str = ""
    reviews_resolved: bool | None = None
    linked_to_ticket: bool | None = None
    acceptance_criteria_signal: str = "unknown"
    merged_by: str | None = None
    approval_reviewers: tuple[str, ...] = ()
    changes_requested: bool = False
    required_reviewers_satisfied: bool | None = None


@dataclass(frozen=True)
class ReviewPolicy:
    required_approvals: int = 1
    required_reviewers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.required_approvals < 1:
            raise ValueError("required_approvals must be positive")


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
    warnings: tuple[FailureRecord, ...] = ()


def classify_failure(error: BaseException, *, phase: str) -> FailureRecord:
    """Normalize operational failures into the human-readable gate vocabulary."""
    if isinstance(error, FileNotFoundError):
        code, summary, retryable = "workspace_missing", "The assigned workspace no longer exists.", True
    elif isinstance(error, HTTPError) and error.code in {401, 403}:
        code, summary, retryable = "tracker_authentication_failed", "The provider rejected authentication.", False
    elif isinstance(error, HTTPError) and error.code in {429, 500, 502, 503, 504}:
        code, summary, retryable = "retry_exhaustion", "The external provider remained unavailable after retries.", True
    elif isinstance(error, TimeoutError):
        code, summary, retryable = "reconciliation_interrupted", "Reconciliation was interrupted before state was complete.", True
    else:
        code, summary, retryable = ("tracker_unavailable", str(error) or "An external operation failed.", True) if phase == "tracker" else ("reconciliation_interrupted", str(error) or "An external operation failed.", True)
    return FailureRecord(
        code, phase, "blocking", retryable, summary,
        "Do not treat the incomplete observation as delivery evidence.",
        "Retry reconciliation after the external dependency is healthy.",
    )


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
        review_policy: ReviewPolicy | None = None,
    ) -> None:
        if policy not in {"continuous_frontier", "wave_barrier"}:
            raise ValueError(f"unsupported scheduling policy: {policy}")

        self._policy = policy
        self._scheduler = Scheduler(
            DependencyGraph(
                {
                    str(item["id"]): WorkItem(
                        str(item["id"]),
                        tuple(str(blocker) for blocker in item.get("blocked_by", [])),
                    )
                    for item in work_items
                }
            ),
            policy=policy,
            base_revision=base_revision,
        )
        self._repository = repository
        self._workspace_root = workspace_root.rstrip("/")
        self._review_policy = review_policy or ReviewPolicy()
        self._dispatched: dict[str, Dispatch] = {}
        self._observations: dict[str, DeliveryObservation] = {}
        self._decisions: dict[str, GateDecision] = {}
        self._claims: dict[str, str] = {}
        self._events: list[Event] = []
        self._active_wave: set[str] = set()

    def dispatch_ready(self) -> tuple[Dispatch, ...]:
        ready = []
        for scheduled in self._scheduler.dispatch_ready():
            item_id = scheduled.work_item_id
            dispatch = Dispatch(
                work_item_id=item_id,
                base_revision=scheduled.base_revision,
                repository=self._repository,
                worktree_path=f"{self._workspace_root}/{item_id}",
                branch_ref=f"mergewave/{item_id}",
            )
            self._dispatched[item_id] = dispatch
            self._events.append(Event("dispatch.created", item_id))
            ready.append(dispatch)

        if self._policy == "wave_barrier" and ready:
            self._active_wave = {dispatch.work_item_id for dispatch in ready}

        return tuple(ready)

    def preview_ready(self) -> tuple[Dispatch, ...]:
        return tuple(
            Dispatch(
                work_item_id=scheduled.work_item_id,
                base_revision=scheduled.base_revision,
                repository=self._repository,
                worktree_path=f"{self._workspace_root}/{scheduled.work_item_id}",
                branch_ref=f"mergewave/{scheduled.work_item_id}",
            )
            for scheduled in self._scheduler.preview_ready()
        )

    def record_agent_claim(self, item_id: str, claim: str) -> None:
        self._claims[item_id] = claim

    def gate_status(self, item_id: str) -> str:
        return self._decisions.get(item_id, GateDecision("pending")).status

    def observe_delivery(self, item_id: str, observation: DeliveryObservation) -> None:
        if item_id not in self._dispatched:
            raise ValueError(f"item was not dispatched: {item_id}")
        self._observations[item_id] = observation
        if self._decisions.get(item_id, GateDecision("pending")).status == "blocked":
            self._decisions.pop(item_id)
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
        warnings = self._warnings(observation)
        decision = GateDecision("blocked", failure, warnings) if failure else GateDecision("approved", warnings=warnings)
        self._decisions[item_id] = decision
        if decision.status == "approved":
            self._scheduler.release(item_id)
        self._events.append(Event("gate.decided", item_id))
        return decision

    def can_refresh_target_base(self) -> bool:
        if self._policy != "wave_barrier":
            return True
        return bool(self._active_wave) and all(
            self._decisions.get(item_id, GateDecision("pending")).status == "approved"
            for item_id in self._active_wave
        )

    def trace(self) -> tuple[Event, ...]:
        return tuple(self._events)

    def current_execution_wave(self) -> ExecutionWave | None:
        return self._scheduler.current_execution_wave()

    def execution_waves(self) -> tuple[ExecutionWave, ...]:
        return self._scheduler.execution_waves()

    def refresh_target_base(self, revision: str) -> None:
        if self._policy == "wave_barrier" and (
            not self._active_wave
            or not all(
                self._decisions.get(item_id, GateDecision("pending")).status == "approved"
                for item_id in self._active_wave
            )
        ):
            raise ValueError("cannot refresh target base before the active wave is approved")
        self._scheduler.refresh_target_base(revision)
        self._active_wave = set()
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
        if not observation.pr_head_sha:
            return FailureRecord(
                "missing_pull_request",
                "delivery",
                "blocking",
                True,
                "No pull request linked to the dispatched work was observed.",
                "Create or link the pull request before requesting delivery verification.",
                "Refresh pull-request observation and keep the item blocked until it exists.",
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
        if observation.linked_to_ticket is not True:
            return FailureRecord(
                "pull_request_unlinked", "delivery", "blocking", True,
                "The pull request was not independently verified as linked to the work item.",
                "Do not release this item until the tracker confirms the pull request link.",
                "Create or repair the tracker attachment and reconcile again.",
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
        if observation.changes_requested or observation.reviews_resolved is False:
            return FailureRecord(
                "review_changes_requested", "review", "blocking", True,
                "A required review is unresolved or requested changes remain.",
                "Resolve requested changes and obtain a current approval before release.",
                "Reconcile review state after the author addresses the feedback.",
            )
        if self._review_policy.required_reviewers and observation.required_reviewers_satisfied is not True:
            return FailureRecord(
                "required_reviewer_missing", "review", "blocking", True,
                "A configured required reviewer has not approved the pull request.",
                "Obtain approval from every reviewer configured by the delivery policy.",
                "Request the missing reviewer approval and reconcile again.",
            )
        if observation.approvals < self._review_policy.required_approvals:
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

    @staticmethod
    def _warnings(observation: DeliveryObservation) -> tuple[FailureRecord, ...]:
        if observation.scope_ok:
            return ()
        return (
            FailureRecord(
                "out_of_scope_diff", "scope", "warning", False,
                "The pull-request diff contains paths outside the declared work-item scope.",
                "Review unrelated changes before accepting the delivery as a safe change.",
                "Record the scope violation and remove unrelated changes when possible.",
            ),
        )
