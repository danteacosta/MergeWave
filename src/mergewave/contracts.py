"""Executable work-item and dependency-graph contracts."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Mapping


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

    return WorkItem(item_id=item_id, blocked_by=tuple(blocked_by))


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
