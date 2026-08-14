"""Provider boundaries owned by the MergeWave core."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from .runtime import AgentEvent, AgentRuntime, RunHandle, RunSpec
from .git_workspace import Workspace
from .simulator import DeliveryObservation


class TrackerAdapter(Protocol):
    def fetch_candidates(self) -> Sequence[dict[str, object]]: ...

    def transition_state(self, item_id: str, state: str) -> None: ...

    def link_pull_request(self, item_id: str, url: str) -> None: ...

    def post_comment(self, item_id: str, body: str) -> None: ...


class WorkspaceFactory(Protocol):
    def create(self, item_id: str, base_revision: str) -> Workspace: ...

    def inspect(self, workspace: Workspace) -> Workspace: ...


class DeliveryObserver(Protocol):
    def observe(self, item_id: str, workspace: Workspace) -> DeliveryObservation: ...


class ReliabilityRecorder(Protocol):
    def record_run(self, **kwargs: object) -> None: ...

    def record_evidence(self, **kwargs: object) -> None: ...

    def record_gate_request(self, **kwargs: object) -> None: ...

    def record_gate_decision(self, **kwargs: object) -> None: ...


__all__ = [
    "AgentEvent",
    "AgentRuntime",
    "DeliveryObserver",
    "ReliabilityRecorder",
    "RunHandle",
    "RunSpec",
    "TrackerAdapter",
    "WorkspaceFactory",
]
