"""MergeWave's model-agnostic delivery control-plane core."""

from .contracts import (
    DependencyGraph,
    DependencyGraphError,
    ProjectSnapshot,
    ValidationIssue,
    WorkItem,
    WorkItemValidationError,
    compile_dependency_graph,
    validate_work_item,
    work_item_to_payload,
)
from .acp_runtime import AcpAgentRuntime, AcpTransport, StdioAcpTransport
from .controller import ActiveAssignment, ControllerProjection, DeliveryController
from .bootstrap import LinearDeliveryApplication, build_linear_application
from .domain import ExecutionWave, GateStatus, HumanGate, PullRequest, ValidationEvidence, WorkAttempt
from .git_workspace import GitWorkspaceFactory, Workspace, WorkspaceDriftError
from .git_provider import GitBaseRevisionProvider, GitProviderError
from .github_adapter import GitHubDeliveryObserver, GitHubTransport, UrllibGitHubTransport
from .linear_adapter import LinearGraphQLAdapter, LinearGraphQLTransport, UrllibLinearTransport
from .live_acceptance import (
    AcceptanceConfigurationError,
    WritableAcceptanceConfig,
    run_writable_acceptance,
)
from .persistence import EventRecord, IdempotencyConflictError, SqliteEventLog
from .reliability import Arp3Contracts, Arp3Recorder
from .reconciliation import ReconciliationLoop, ReconciliationResult
from .runtime import AgentEvent, CliAgentRuntime, RunHandle, RunSpec, RuntimeCapabilities, WorkerProfile, classify_runtime_event
from .skills import SkillArtifact, SkillInvocation, SkillResult
from .runtime_profiles import (
    AcpProviderProfile,
    DEFAULT_RUNTIME_REGISTRY,
    RuntimeAdapterProfile,
    RuntimeAdapterRegistry,
    provider_profile,
    runtime_for,
    stdio_runtime,
)
from .supervisor import DeliverySupervisor, SupervisionCycle, SupervisorPolicy
from .scheduler import Scheduler
from .smoke import SmokeConfigurationError, github_read_only_smoke, linear_read_only_smoke
from .simulator import (
    DeliveryObservation,
    Dispatch,
    Event,
    FailureRecord,
    GateDecision,
    MergeWaveSimulator,
    ReviewPolicy,
)

__all__ = [
    "DependencyGraph",
    "DependencyGraphError",
    "ProjectSnapshot",
    "AgentEvent",
    "AcpAgentRuntime",
    "AcpTransport",
    "AcpProviderProfile",
    "DEFAULT_RUNTIME_REGISTRY",
    "StdioAcpTransport",
    "ActiveAssignment",
    "ControllerProjection",
    "LinearDeliveryApplication",
    "build_linear_application",
    "Arp3Recorder",
    "Arp3Contracts",
    "CliAgentRuntime",
    "DeliveryObservation",
    "DeliveryController",
    "DeliverySupervisor",
    "ExecutionWave",
    "GateStatus",
    "HumanGate",
    "PullRequest",
    "Dispatch",
    "Event",
    "EventRecord",
    "IdempotencyConflictError",
    "FailureRecord",
    "GateDecision",
    "GitWorkspaceFactory",
    "GitBaseRevisionProvider",
    "GitProviderError",
    "GitHubDeliveryObserver",
    "GitHubTransport",
    "LinearGraphQLAdapter",
    "LinearGraphQLTransport",
    "AcceptanceConfigurationError",
    "WritableAcceptanceConfig",
    "run_writable_acceptance",
    "MergeWaveSimulator",
    "ReviewPolicy",
    "RunHandle",
    "RunSpec",
    "SkillArtifact",
    "SkillInvocation",
    "SkillResult",
    "RuntimeCapabilities",
    "RuntimeAdapterProfile",
    "RuntimeAdapterRegistry",
    "SupervisionCycle",
    "SupervisorPolicy",
    "ReconciliationLoop",
    "ReconciliationResult",
    "Scheduler",
    "SmokeConfigurationError",
    "SqliteEventLog",
    "ValidationIssue",
    "ValidationEvidence",
    "WorkItem",
    "WorkItemValidationError",
    "WorkAttempt",
    "Workspace",
    "WorkspaceDriftError",
    "WorkerProfile",
    "provider_profile",
    "runtime_for",
    "stdio_runtime",
    "classify_runtime_event",
    "UrllibGitHubTransport",
    "UrllibLinearTransport",
    "compile_dependency_graph",
    "github_read_only_smoke",
    "linear_read_only_smoke",
    "validate_work_item",
    "work_item_to_payload",
]
