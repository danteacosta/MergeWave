"""MergeWave's model-agnostic delivery control-plane core."""

from .contracts import (
    DependencyGraph,
    DependencyGraphError,
    ValidationIssue,
    WorkItem,
    WorkItemValidationError,
    compile_dependency_graph,
    validate_work_item,
)
from .git_workspace import GitWorkspaceFactory, Workspace, WorkspaceDriftError
from .persistence import EventRecord, SqliteEventLog
from .reliability import Arp3Recorder
from .reconciliation import ReconciliationLoop, ReconciliationResult
from .runtime import AgentEvent, CliAgentRuntime, RunHandle, RunSpec
from .scheduler import Scheduler
from .simulator import (
    DeliveryObservation,
    Dispatch,
    Event,
    FailureRecord,
    GateDecision,
    MergeWaveSimulator,
)

__all__ = [
    "DependencyGraph",
    "DependencyGraphError",
    "AgentEvent",
    "Arp3Recorder",
    "CliAgentRuntime",
    "DeliveryObservation",
    "Dispatch",
    "Event",
    "EventRecord",
    "FailureRecord",
    "GateDecision",
    "GitWorkspaceFactory",
    "MergeWaveSimulator",
    "RunHandle",
    "RunSpec",
    "ReconciliationLoop",
    "ReconciliationResult",
    "Scheduler",
    "SqliteEventLog",
    "ValidationIssue",
    "WorkItem",
    "WorkItemValidationError",
    "Workspace",
    "WorkspaceDriftError",
    "compile_dependency_graph",
    "validate_work_item",
]
