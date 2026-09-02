"""Composition boundary from executable Linear tickets to a live controller."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json

from .contracts import (
    DependencyGraph,
    ProjectSnapshot,
    WorkItem,
    WorkItemState,
    compile_dependency_graph,
    work_item_to_payload,
)
from .controller import DeliveryController
from .persistence import SqliteEventLog
from .ports import BaseRevisionProvider, DeliveryObserver, ReliabilityRecorder, TrackerAdapter, WorkspaceFactory
from .runtime import AgentRuntime
from .skills import SkillInvocation
from .simulator import GateDecision, MergeWaveSimulator


@dataclass
class LinearDeliveryApplication:
    """Own the long-lived controller and its polling/reconciliation cycle."""

    controller: DeliveryController
    graph: DependencyGraph
    work_items: Mapping[str, WorkItem]
    prompts: Mapping[str, str]

    def start(self) -> tuple[object, ...]:
        return self.controller.dispatch_ready(self.prompts)

    def reconcile_once(self) -> Mapping[str, GateDecision]:
        decisions: dict[str, GateDecision] = {}
        for item_id in self.controller.active_item_ids():
            decisions[item_id] = self.controller.reconcile(item_id)
        return decisions


def build_linear_application(
    *,
    tracker: TrackerAdapter,
    base_revision_provider: BaseRevisionProvider,
    workspace_factory: WorkspaceFactory,
    runtime: AgentRuntime,
    observer: DeliveryObserver,
    policy: str,
    recorder: ReliabilityRecorder | None = None,
    event_log: SqliteEventLog | None = None,
    skill_invocations: Mapping[str, SkillInvocation] | None = None,
) -> LinearDeliveryApplication:
    candidates = tuple(tracker.fetch_candidates())
    payloads = tuple(_candidate_payload(candidate) for candidate in candidates)
    graph = compile_dependency_graph(payloads)
    work_items = graph.items
    project_snapshot = ProjectSnapshot.from_work_items(work_items.values())
    completed = tuple(item.item_id for item in work_items.values() if item.state is WorkItemState.DONE)
    simulator = MergeWaveSimulator(
        [{"id": item.item_id, "blocked_by": list(item.blocked_by)} for item in work_items.values()],
        policy=policy,
        base_revision=base_revision_provider.current_revision(),
        completed_item_ids=completed,
    )
    controller_factory = (
        DeliveryController.from_event_log
        if event_log is not None and event_log.events()
        else DeliveryController
    )
    controller = controller_factory(
        simulator=simulator,
        tracker=tracker,
        workspace_factory=workspace_factory,
        runtime=runtime,
        observer=observer,
        recorder=recorder,
        base_revision_provider=base_revision_provider,
        event_log=event_log,
        work_items=work_items,
        project_snapshot=project_snapshot,
        skill_invocations=skill_invocations,
    )
    prompts = {
        item.item_id: _prompt_for(item)
        for item in work_items.values()
        if item.state not in {WorkItemState.DONE, WorkItemState.CANCELLED}
    }
    return LinearDeliveryApplication(controller, graph, work_items, prompts)


def _candidate_payload(candidate: Mapping[str, object]) -> dict[str, object]:
    raw_work_item = candidate.get("work_item")
    if isinstance(raw_work_item, Mapping):
        payload = dict(raw_work_item)
    else:
        description = candidate.get("description", "")
        if not isinstance(description, str):
            raise ValueError(f"Linear candidate {candidate.get('id', '<unknown>')} has no executable work-item body")
        try:
            parsed = json.loads(description)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"Linear candidate {candidate.get('id', '<unknown>')} description must contain the work-item JSON contract"
            ) from error
        if not isinstance(parsed, Mapping):
            raise ValueError("Linear work-item body must be a JSON object")
        payload = dict(parsed)
    for key in ("id", "blocked_by"):
        if key in candidate:
            payload[key] = candidate[key]
    payload.setdefault("state", _normalize_state(candidate.get("state", WorkItemState.READY.value)))
    return payload


def _normalize_state(value: object) -> str:
    if isinstance(value, str) and value in {member.value for member in WorkItemState}:
        return value
    aliases = {
        "backlog": WorkItemState.READY.value,
        "todo": WorkItemState.READY.value,
        "in progress": WorkItemState.IN_PROGRESS.value,
        "in review": WorkItemState.IN_REVIEW.value,
        "needs attention": WorkItemState.NEEDS_ATTENTION.value,
        "done": WorkItemState.DONE.value,
        "canceled": WorkItemState.CANCELLED.value,
        "cancelled": WorkItemState.CANCELLED.value,
        "blocked": WorkItemState.BLOCKED.value,
    }
    if isinstance(value, str):
        return aliases.get(value.strip().lower(), value)
    return WorkItemState.READY.value


def _prompt_for(item: WorkItem) -> str:
    return json.dumps(work_item_to_payload(item), sort_keys=True)


__all__ = ["LinearDeliveryApplication", "build_linear_application"]
