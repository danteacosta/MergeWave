"""Provider-neutral delivery entities from the MergeWave contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class GateStatus(StrEnum):
    PENDING = "pending"
    SATISFIED = "satisfied"


@dataclass(frozen=True)
class WorkAttempt:
    id: str
    work_item_id: str
    base_sha: str
    workspace_id: str
    agent_runtime: str
    started_at: datetime
    state: str


@dataclass(frozen=True)
class ExecutionWave:
    wave_id: str
    base_sha: str
    work_item_ids: tuple[str, ...]
    state: str


@dataclass(frozen=True)
class PullRequest:
    id: str
    work_item_id: str
    url: str
    head_sha: str
    base_sha_at_open: str
    ci_status: str
    ci_checked_head_sha: str
    reviews_resolved: bool
    merged: bool
    merge_commit_sha: str | None = None


@dataclass(frozen=True)
class ValidationEvidence:
    work_item_id: str
    pr_linked: bool
    base_sha_verified: bool
    ci_verified_against_head: bool
    reviews_resolved: bool
    scope_check: str
    acceptance_criteria_signal: str
    collected_at: datetime


@dataclass(frozen=True)
class HumanGate:
    work_item_id: str
    required: bool
    satisfied: bool
    merged_by: str | None = None
    satisfied_at: datetime | None = None

    @property
    def status(self) -> GateStatus:
        return GateStatus.SATISFIED if self.satisfied else GateStatus.PENDING


__all__ = [
    "ExecutionWave",
    "GateStatus",
    "HumanGate",
    "PullRequest",
    "ValidationEvidence",
    "WorkAttempt",
]
