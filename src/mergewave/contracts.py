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
    suggested_correction: str = ""


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
    i18n_requirements: tuple[str, ...] = ()
    privacy_requirements: tuple[str, ...] = ()
    factory_requirements: tuple[str, ...] = ()
    migration_requirements: tuple[str, ...] = ()
    schema_requirements: tuple[str, ...] = ()
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
_IMPERATIVE_VERBS = frozenset(
    {
        "add", "adicionar", "adote", "adopt", "align", "alinhar", "build", "construir",
        "configure", "configurar", "create", "criar", "disable", "desabilitar", "enable",
        "expose", "expor", "fix", "corrigir", "harden", "implementar", "implement", "integrate",
        "integrar", "migrate", "migrar", "monitor", "monitorar", "persist", "persistir", "preserve",
        "record", "registrar", "release", "remover", "remove", "replace", "substituir", "ship",
        "split", "support", "suportar", "update", "atualizar", "validate", "validar", "enforce",
    }
)
_REQUIREMENT_KINDS = ("i18n", "privacy", "factories", "migration", "schema")


def _issue(code: str, field: str, message: str, correction: str) -> ValidationIssue:
    return ValidationIssue(code, field, message, correction)


def _non_empty_string_list(
    value: object,
    *,
    field: str,
    min_items: int = 0,
) -> tuple[ValidationIssue, ...]:
    if not isinstance(value, list):
        return (_issue("invalid_list", field, f"{field} must be a list", f"Set {field} to a JSON array."),)
    issues: list[ValidationIssue] = []
    if len(value) < min_items:
        issues.append(
            _issue(
                "empty_executable_section",
                field,
                f"{field} must contain at least {min_items} item(s)",
                f"Add an executable entry to {field}.",
            )
        )
    if not all(isinstance(item, str) and item.strip() for item in value):
        issues.append(
            _issue(
                "invalid_list_item",
                field,
                f"every {field} item must be non-empty text",
                f"Remove blank values and use strings in {field}.",
            )
        )
    return tuple(issues)


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
        _issue(
            "missing_field",
            field,
            f"required field is missing: {field}",
            f"Add the required {field} field.",
        )
        for field in required
        if field not in raw
    ]
    if issues:
        raise WorkItemValidationError(tuple(issues))

    allowed_top_level = frozenset((*required, "requirements"))
    for field in sorted(set(raw) - allowed_top_level):
        issues.append(
            _issue(
                "unknown_field",
                field,
                f"unsupported top-level field: {field}",
                "Move domain-specific metadata into a supported requirement section or remove it.",
            )
        )

    item_id = raw["id"]
    if not isinstance(item_id, str) or not _ITEM_ID.fullmatch(item_id):
        issues.append(_issue("invalid_id", "id", "id must match PROJECT-123 format", "Use an ID such as PAY-123."))

    blocked_by = raw["blocked_by"]
    if not isinstance(blocked_by, list) or not all(isinstance(value, str) for value in blocked_by):
        issues.append(_issue("invalid_blockers", "blocked_by", "blocked_by must be a list of IDs", "Use [] or a list such as [\"PAY-101\"]."))
    elif any(not _ITEM_ID.fullmatch(value) for value in blocked_by):
        issues.append(
            _issue(
                "invalid_blocker_id",
                "blocked_by",
                "every blocked_by value must match PROJECT-123 format",
                "Replace malformed blockers with explicit tracker IDs.",
            )
        )
    elif len(set(blocked_by)) != len(blocked_by):
        issues.append(_issue("duplicate_blocker", "blocked_by", "blocked_by values must be unique", "Remove duplicate blocker IDs."))

    estimate_points = raw["estimate_points"]
    if not isinstance(estimate_points, int) or isinstance(estimate_points, bool) or not 1 <= estimate_points <= 5:
        issues.append(
            _issue(
                "estimate_out_of_range",
                "estimate_points",
                "estimate_points must be an integer between 1 and 5",
                "Split work above five points into independently reviewable items.",
            )
        )

    minimum_lengths = {"title": 12, "problem": 30, "behavior": 30}
    for field, minimum in minimum_lengths.items():
        if not isinstance(raw[field], str) or len(raw[field].strip()) < minimum:
            issues.append(_issue("text_too_short", field, f"{field} must contain at least {minimum} characters", f"Make {field} specific and executable."))

    title = str(raw["title"]).strip()
    first_word = re.sub(r"[^\wÀ-ÿ-]", "", title.split(maxsplit=1)[0]).casefold() if title else ""
    if first_word not in _IMPERATIVE_VERBS:
        issues.append(_issue("title_not_imperative", "title", "title must begin with a recognized imperative verb", "Start with a concrete action such as Add, Implement, Fix, Record, or Validate."))
    normalized_title = " ".join(title.casefold().split())
    standalone_patterns = {
        "test_only_item": r"^(?:add|write|create|implement|update|fix)?\s*(?:unit |integration |e2e |end-to-end |regression )?tests?\b",
        "migration_only_item": r"^(?:add|create|implement|run|write)?\s*(?:database |data )?migrations?\b",
        "schema_only_item": r"^(?:add|create|implement|update|write)?\s*(?:database |api )?schemas?\b",
        "foundation_only_item": r"^(?:add|create|implement|build)?\s*(?:generic |shared |future )?foundation\b",
    }
    for code, pattern in standalone_patterns.items():
        if re.search(pattern, normalized_title) and " and " not in normalized_title and " with " not in normalized_title:
            issues.append(_issue(code, "title", f"standalone {code.removesuffix('_item').replace('_', ' ')} work item is not allowed", "Fold this work into the behavior-changing ticket that consumes it."))

    state = raw["state"]
    if not isinstance(state, str) or state not in {member.value for member in WorkItemState}:
        issues.append(_issue("invalid_state", "state", "state must be a valid WorkItemState", "Use one of the documented tracker states."))

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
            issues.append(_issue("invalid_object", parent, f"{parent} must be an object", f"Set {parent} to a JSON object."))
            continue
        issues.extend(
            _issue("missing_field", f"{parent}.{child}", f"required field is missing: {parent}.{child}", f"Add {parent}.{child}.")
            for child in children
            if child not in value
        )
        for child in sorted(set(value) - set(children)):
            issues.append(_issue("unknown_field", f"{parent}.{child}", f"unsupported field: {parent}.{child}", "Remove the field or place it in the appropriate supported section."))

    if all(isinstance(raw[parent], Mapping) for parent in nested_requirements):
        scope = raw["scope"]
        technical_context = raw["technical_context"]
        risk = raw["risk"]
        rollout = raw["rollout"]
        observability = raw["observability"]
        issues.extend(_non_empty_string_list(scope.get("in"), field="scope.in", min_items=1))
        issues.extend(_non_empty_string_list(scope.get("out"), field="scope.out"))
        if not isinstance(technical_context.get("summary"), str) or not str(technical_context.get("summary", "")).strip():
            issues.append(_issue("invalid_text", "technical_context.summary", "technical_context.summary must be non-empty text", "Describe the relevant existing design and constraints."))
        issues.extend(_non_empty_string_list(technical_context.get("modules"), field="technical_context.modules", min_items=1))
        issues.extend(_non_empty_string_list(technical_context.get("constraints"), field="technical_context.constraints"))
        if risk.get("level") not in {"low", "medium", "high"}:
            issues.append(_issue("invalid_risk_level", "risk.level", "risk.level must be low, medium, or high", "Choose a documented risk level."))
        if not isinstance(risk.get("reason"), str):
            issues.append(_issue("invalid_text", "risk.reason", "risk.reason must be text", "Explain the risk or use an empty string for a genuinely low-risk item."))
        for field in ("strategy", "kill_switch"):
            if not isinstance(rollout.get(field), str):
                issues.append(_issue("invalid_text", f"rollout.{field}", f"rollout.{field} must be text", "Use an explicit empty string when not applicable."))
        issues.extend(_non_empty_string_list(observability.get("events"), field="observability.events"))
        issues.extend(_non_empty_string_list(observability.get("metrics"), field="observability.metrics"))

    issues.extend(_non_empty_string_list(raw["affected_paths"], field="affected_paths", min_items=1))

    structured_lists = {
        "acceptance_criteria": ("id", "criterion"),
        "test_scenarios": ("id", "given", "when", "then"),
    }
    for field, required_keys in structured_lists.items():
        values = raw[field]
        if not isinstance(values, list) or not values:
            issues.append(_issue("empty_executable_section", field, f"{field} must be a non-empty list", f"Add at least one complete {field} entry."))
            continue
        for index, value in enumerate(values):
            path = f"{field}[{index}]"
            if not isinstance(value, Mapping):
                issues.append(_issue("invalid_object", path, f"{path} must be an object", "Replace it with the documented object shape."))
                continue
            for key in required_keys:
                if not isinstance(value.get(key), str) or not str(value.get(key, "")).strip():
                    issues.append(_issue("invalid_text", f"{path}.{key}", f"{path}.{key} must be non-empty text", f"Add {path}.{key}."))
            for key in sorted(set(value) - set(required_keys)):
                issues.append(_issue("unknown_field", f"{path}.{key}", f"unsupported field: {path}.{key}", "Remove the unsupported field."))

    requirements = raw.get("requirements", {})
    if not isinstance(requirements, Mapping):
        issues.append(_issue("invalid_object", "requirements", "requirements must be an object", "Use the documented optional requirements object."))
        requirements = {}
    else:
        for key in sorted(set(requirements) - set(_REQUIREMENT_KINDS)):
            issues.append(_issue("unknown_field", f"requirements.{key}", f"unsupported requirement kind: {key}", "Use i18n, privacy, factories, migration, or schema."))
        for key in _REQUIREMENT_KINDS:
            issues.extend(_non_empty_string_list(requirements.get(key, []), field=f"requirements.{key}"))
        if bool(requirements.get("migration")) != bool(requirements.get("schema")):
            issues.append(_issue("migration_schema_split", "requirements", "migration and schema requirements must be delivered together", "Move the matching schema or migration work into this ticket."))

    if issues:
        raise WorkItemValidationError(tuple(issues))

    risk = raw["risk"]
    rollout = raw["rollout"]
    observability = raw["observability"]
    scope = raw["scope"]
    technical_context = raw["technical_context"]
    requirements = raw.get("requirements", {})
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
        i18n_requirements=tuple(str(value) for value in requirements.get("i18n", ())),
        privacy_requirements=tuple(str(value) for value in requirements.get("privacy", ())),
        factory_requirements=tuple(str(value) for value in requirements.get("factories", ())),
        migration_requirements=tuple(str(value) for value in requirements.get("migration", ())),
        schema_requirements=tuple(str(value) for value in requirements.get("schema", ())),
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
        "requirements": {
            "i18n": list(item.i18n_requirements),
            "privacy": list(item.privacy_requirements),
            "factories": list(item.factory_requirements),
            "migration": list(item.migration_requirements),
            "schema": list(item.schema_requirements),
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
