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
from .acp_runtime import AcpAgentRuntime, AcpTransport
from .controller import ActiveAssignment, DeliveryController
from .git_workspace import GitWorkspaceFactory, Workspace, WorkspaceDriftError
from .github_adapter import GitHubDeliveryObserver, GitHubTransport, UrllibGitHubTransport
from .linear_adapter import LinearGraphQLAdapter, LinearGraphQLTransport, UrllibLinearTransport
from .persistence import EventRecord, SqliteEventLog
from .reliability import Arp3Recorder
from .reconciliation import ReconciliationLoop, ReconciliationResult
from .runtime import AgentEvent, CliAgentRuntime, RunHandle, RunSpec
from .scheduler import Scheduler
from .smoke import SmokeConfigurationError, github_read_only_smoke, linear_read_only_smoke
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
    "AcpAgentRuntime",
    "AcpTransport",
    "ActiveAssignment",
    "Arp3Recorder",
    "CliAgentRuntime",
    "DeliveryObservation",
    "DeliveryController",
    "Dispatch",
    "Event",
    "EventRecord",
    "FailureRecord",
    "GateDecision",
    "GitWorkspaceFactory",
    "GitHubDeliveryObserver",
    "GitHubTransport",
    "LinearGraphQLAdapter",
    "LinearGraphQLTransport",
    "MergeWaveSimulator",
    "RunHandle",
    "RunSpec",
    "ReconciliationLoop",
    "ReconciliationResult",
    "Scheduler",
    "SmokeConfigurationError",
    "SqliteEventLog",
    "ValidationIssue",
    "WorkItem",
    "WorkItemValidationError",
    "Workspace",
    "WorkspaceDriftError",
    "UrllibGitHubTransport",
    "UrllibLinearTransport",
    "compile_dependency_graph",
    "github_read_only_smoke",
    "linear_read_only_smoke",
    "validate_work_item",
]
