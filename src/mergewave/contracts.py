"""Executable work-item and dependency-graph contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import re
from typing import Iterable, Mapping


class WorkItemState(StrEnum):
    BLOCKED = "Blocked"
    READY = "Ready"
    IN_PROGRESS = "InProgress"
    IN_REVIEW = "InReview"
    NEEDS_ATTENTION = "NeedsAttention"
    DONE = "Done"
    CANCELLED = "Cancelled"


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    field: str
    message: str


class WorkItemValidationError(ValueError):
    def __init__(self, issues: tuple[ValidationIssue, ...]) -> None:
        self.issues = issues
        super().__init__("; ".join(issue.message for issue in issues))


@dataclass(frozen=True)
class WorkItem:
    item_id: str
    blocked_by: tuple[str, ...]
    title: str = ""
    description: str = ""
    behavior: str = ""
    technical_context_summary: str = ""
    technical_modules: tuple[str, ...] = ()
    technical_constraints: tuple[str, ...] = ()
    scope_in: tuple[str, ...] = ()
    scope_out: tuple[str, ...] = ()
    acceptance_criteria: tuple[Mapping[str, object], ...] = ()
    test_scenarios: tuple[Mapping[str, object], ...] = ()
    affected_paths: tuple[str, ...] = ()
    estimate_points: int = 1
    risk_level: str = "low"
    risk_reason: str = ""
    rollout_strategy: str | None = None
    kill_switch: str | None = None
    observability_events: tuple[str, ...] = ()
    metrics: tuple[str, ...] = ()
    state: WorkItemState = WorkItemState.READY


@dataclass(frozen=True)
class ProjectSnapshot:
    """Canonical tracker/DAG input identity shared with reliability adapters."""

    ref: str
    digest: str
    payload: Mapping[str, object]

    @classmethod
    def from_work_items(cls, items: Iterable[WorkItem]) -> "ProjectSnapshot":
        payload: dict[str, object] = {
            "work_items": [
                work_item_to_payload(item)
                for item in sorted(items, key=lambda candidate: candidate.item_id)
            ]
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        hex_digest = hashlib.sha256(encoded).hexdigest()
        return cls(
            ref=f"urn:mergewave:project-snapshot:{hex_digest}",
            digest=f"sha256:{hex_digest}",
            payload=payload,
        )


class DependencyGraphError(ValueError):
    def __init__(self, cycle: tuple[str, ...]) -> None:
        self.cycle = cycle
        super().__init__(f"dependency cycle: {' -> '.join(cycle)}")


@dataclass(frozen=True)
class DependencyGraph:
    items: Mapping[str, WorkItem]

    def ready(self, completed: Iterable[str] = ()) -> tuple[WorkItem, ...]:
        completed_ids = frozenset(completed)
        return tuple(
            item
            for item in self.items.values()
            if item.item_id not in completed_ids
            and set(item.blocked_by).issubset(completed_ids)
        )


_ITEM_ID = re.compile(r"^[A-Z][A-Z0-9_-]*-[0-9]+$")


def validate_work_item(raw: Mapping[str, object]) -> WorkItem:
    required = (
        "id",
        "title",
        "problem",
        "scope",
        "behavior",
        "technical_context",
        "affected_paths",
        "acceptance_criteria",
        "test_scenarios",
        "blocked_by",
        "estimate_points",
        "risk",
        "rollout",
        "observability",
        "state",
    )
    issues = [
        ValidationIssue("missing_field", field, f"required field is missing: {field}")
        for field in required
        if field not in raw
    ]
    if issues:
        raise WorkItemValidationError(tuple(issues))

    item_id = raw["id"]
    if not isinstance(item_id, str) or not _ITEM_ID.fullmatch(item_id):
        issues.append(ValidationIssue("invalid_id", "id", "id must match PROJECT-123 format"))

    blocked_by = raw["blocked_by"]
    if not isinstance(blocked_by, list) or not all(isinstance(value, str) for value in blocked_by):
        issues.append(ValidationIssue("invalid_blockers", "blocked_by", "blocked_by must be a list of IDs"))
    elif any(not _ITEM_ID.fullmatch(value) for value in blocked_by):
        issues.append(
            ValidationIssue(
                "invalid_blocker_id",
                "blocked_by",
                "every blocked_by value must match PROJECT-123 format",
            )
        )

    estimate_points = raw["estimate_points"]
    if not isinstance(estimate_points, int) or isinstance(estimate_points, bool) or not 1 <= estimate_points <= 5:
        issues.append(
            ValidationIssue(
                "estimate_out_of_range",
                "estimate_points",
                "estimate_points must be an integer between 1 and 5",
            )
        )

    for field in ("title", "problem", "behavior"):
        if not isinstance(raw[field], str) or not raw[field].strip():
            issues.append(ValidationIssue("invalid_text", field, f"{field} must be non-empty text"))

    state = raw["state"]
    if not isinstance(state, str) or state not in {member.value for member in WorkItemState}:
        issues.append(ValidationIssue("invalid_state", "state", "state must be a valid WorkItemState"))

    nested_requirements = {
        "scope": ("in", "out"),
        "technical_context": ("summary", "modules", "constraints"),
        "risk": ("level", "reason"),
        "rollout": ("strategy", "kill_switch"),
        "observability": ("events", "metrics"),
    }
    for parent, children in nested_requirements.items():
        value = raw[parent]
        if not isinstance(value, Mapping):
            issues.append(ValidationIssue("invalid_object", parent, f"{parent} must be an object"))
            continue
        issues.extend(
            ValidationIssue("missing_field", f"{parent}.{child}", f"required field is missing: {parent}.{child}")
            for child in children
            if child not in value
        )

    if issues:
        raise WorkItemValidationError(tuple(issues))

    risk = raw["risk"]
    rollout = raw["rollout"]
    observability = raw["observability"]
    scope = raw["scope"]
    technical_context = raw["technical_context"]
    return WorkItem(
        item_id=item_id,
        blocked_by=tuple(blocked_by),
        title=str(raw["title"]),
        description=str(raw["problem"]),
        behavior=str(raw["behavior"]),
        technical_context_summary=str(technical_context["summary"]),
        technical_modules=tuple(str(value) for value in technical_context["modules"]),
        technical_constraints=tuple(str(value) for value in technical_context["constraints"]),
        scope_in=tuple(str(value) for value in scope["in"]),
        scope_out=tuple(str(value) for value in scope["out"]),
        acceptance_criteria=tuple(raw["acceptance_criteria"]),
        test_scenarios=tuple(raw["test_scenarios"]),
        affected_paths=tuple(str(value) for value in raw["affected_paths"]),
        estimate_points=estimate_points,
        risk_level=str(risk["level"]),
        risk_reason=str(risk["reason"]),
        rollout_strategy=str(rollout["strategy"]),
        kill_switch=str(rollout["kill_switch"]),
        observability_events=tuple(str(value) for value in observability["events"]),
        metrics=tuple(str(value) for value in observability["metrics"]),
        state=WorkItemState(state),
    )


def work_item_to_payload(item: WorkItem) -> dict[str, object]:
    """Return the complete executable ticket contract without lossy remapping."""

    return {
        "id": item.item_id,
        "title": item.title,
        "problem": item.description,
        "scope": {"in": list(item.scope_in), "out": list(item.scope_out)},
        "behavior": item.behavior,
        "technical_context": {
            "summary": item.technical_context_summary,
            "modules": list(item.technical_modules),
            "constraints": list(item.technical_constraints),
        },
        "affected_paths": list(item.affected_paths),
        "acceptance_criteria": [dict(value) for value in item.acceptance_criteria],
        "test_scenarios": [dict(value) for value in item.test_scenarios],
        "blocked_by": list(item.blocked_by),
        "estimate_points": item.estimate_points,
        "risk": {"level": item.risk_level, "reason": item.risk_reason},
        "rollout": {"strategy": item.rollout_strategy, "kill_switch": item.kill_switch},
        "observability": {
            "events": list(item.observability_events),
            "metrics": list(item.metrics),
        },
        "state": item.state.value,
    }


def compile_dependency_graph(raw_items: Iterable[Mapping[str, object]]) -> DependencyGraph:
    validated = [validate_work_item(raw) for raw in raw_items]
    items = {item.item_id: item for item in validated}
    if len(items) != len(validated):
        raise ValueError("duplicate work-item id")

    for item in validated:
        missing = set(item.blocked_by) - items.keys()
        if missing:
            raise ValueError(f"missing dependencies for {item.item_id}: {sorted(missing)}")

    visiting: list[str] = []
    visited: set[str] = set()

    def visit(item_id: str) -> None:
        if item_id in visiting:
            start = visiting.index(item_id)
            raise DependencyGraphError(tuple(visiting[start:] + [item_id]))
        if item_id in visited:
            return
        visiting.append(item_id)
        for blocker in items[item_id].blocked_by:
            visit(blocker)
        visiting.pop()
        visited.add(item_id)

    for item_id in items:
        visit(item_id)
    return DependencyGraph(items)
