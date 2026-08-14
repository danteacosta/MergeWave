# MergeWave — Merge-Gated, Dependency-Aware Agentic Delivery Control Plane

Status: implemented v0.2 core slice

## Goal

MergeWave is a model-agnostic delivery control plane. It schedules agents from
an explicit dependency graph and releases downstream work only after
independent observation of the pull request, tracker link, CI, reviews, scope,
merge, and base revision.

The agent proposes a result. MergeWave verifies external state. A human merge
is the default release authority. No agent self-report can release a blocker.

## Architecture

```text
TrackerAdapter → WorkItem validator → DependencyGraph → Scheduler
       │                                  │              │
       └── dependency/link/criteria       └── waves       └── fresh base
                                                          │
WorkspaceFactory → AgentRuntime → DeliveryObserver → Evidence → Gate
       │                  │                 │             │       │
       └── drift/reset    └── timeout       └── PR/CI      └── ARP 3.0
                                                                      │
                                           Human merge ← Controller ← Event log
                                                                      │
                                           reconciliation → next frontier
```

The core has no Linear, GitHub, model-provider, or ARP import. ARP 3.0 is
implemented by the optional `Arp3Recorder` port adapter. Core entities are
`PullRequest`, `WorkAttempt`, `ExecutionWave`, `ValidationEvidence`, and
`HumanGate`; provider identifiers belong in adapter payloads or extensions.

## Work-item and ticket states

The executable work-item schema requires `state` with these values:

`Blocked`, `Ready`, `InProgress`, `InReview`, `NeedsAttention`, `Done`, and
`Cancelled`.

The controller moves a dispatched item to `InProgress`, publishes `InReview`
when a PR is observed, moves a merge-approved item to `Done`, and moves a
failed gate or runtime failure to `NeedsAttention`. `Ready`, `Blocked`, and
`Cancelled` remain tracker-owned lifecycle states in this slice.

## Scheduling and waves

Both `continuous_frontier` and `wave_barrier` are supported. Every dispatch
batch is represented by an `ExecutionWave` with its base revision and item
IDs. Every item creates a `WorkAttempt` with workspace, runtime, base, and
state metadata.

`continuous_frontier` can release a newly unblocked frontier after each merge.
`wave_barrier` waits for every item in the active wave. In either policy, a
new frontier is created from the observed current target-base revision, never
from an unmerged agent branch.

## Gate contract

The controller enriches GitHub delivery observations with independent Linear
checks for the PR attachment and acceptance-criteria checklist. The gate
requires:

- workspace identity and ancestry invariants;
- a PR linked to the tracker item independently of its branch name;
- `base_revision` ancestral to the PR head;
- CI successful for the current PR head;
- current, resolved reviews satisfying configurable approval and required-reviewer policy;
- observed human merge into the target base.

Acceptance criteria are recorded as a weak signal (`complete`, `partial`, or
`unknown`) and are not treated as a substitute for delivery evidence.

Scope violations are visible `out_of_scope_diff` warnings in v1. They are not
silently ignored and do not hard-block the gate; adopters may promote the
warning to a policy decision later.

The default gate remains human-controlled. Auto-merge is out of scope.

## Workspace invariants

Workspace creation records `base_revision`, `initial_head_revision`,
`current_head_revision`, `work_item_id`, `created_at`, and later `destroyed_at`.
Creation requires `initial_head_revision == base_revision`.

Normal agent commits advance HEAD and are valid. `workspace_drift` means wrong
repository/worktree/branch, detached or missing workspace, unexpected reset,
or divergent local history. A PR whose history does not descend from the base
is classified separately as `base_revision_mismatch`.

## Runtime contract

`AgentRuntime` exposes `start`, `stream`, `continue_run`, `cancel`, and
`capabilities`. `RunSpec` carries the prompt plus optional normalized
`WorkItem`, `Workspace`, and `WorkerProfile` (permissions, sandbox, and cost
limit). The repository includes a generic CLI runtime with timeout detection
and a stdio JSON-RPC ACP transport boundary. Provider-specific session
semantics remain runtime adapter responsibilities.

## Failure classification and recovery

Operational failures are normalized into stable records with code, phase,
severity, retryability, human explanation, agent guidance, and next action.
The current vocabulary includes `workspace_missing`, `workspace_drift`,
`base_revision_mismatch`, `pull_request_unlinked`, `stale_ci`,
`review_changes_requested`, `required_reviewer_missing`, `agent_timeout`,
`runtime_failed`, `tracker_authentication_failed`, `tracker_unavailable`,
`retry_exhaustion`, and `reconciliation_interrupted`.

The SQLite event log is idempotent and can reduce its ordered event stream into
a `ControllerProjection` containing ticket states, base revision, attempt
states, and wave states. `ReconciliationLoop.reconcile_controller()` connects
the durable reconciliation boundary to the controller. Successful approval
refreshes the target base and dispatches the next frontier when prompts are
available; otherwise the event log records that the frontier is waiting for
prompts.

## Adapters

The current ports and adapters are:

- Linear GraphQL: paginated candidate reads, explicit `fetch_dependencies`, PR
  attachment verification, acceptance-criteria signal, state transitions,
  comments, and retry/backoff for rate-limit/server failures;
- GitHub: PR lookup, `base_sha_at_open`, current-head CI, latest review state,
  required reviewers, changed paths, merge, and ancestry observation;
- CLI and ACP runtimes;
- optional ARP 3.0 recorder.

Webhooks, multi-repository scheduling, dashboard UI, dependency inference,
conflict resolution, and auto-merge remain non-goals for this release.

## Acceptance slice

The executable tests cover three independent roots and a dependent item,
shared/fresh bases, merge-gated release, stale CI, PR unlinking, resolved and
required-reviewer policy, scope warnings, acceptance-criteria capture,
timeouts, runtime failure, workspace reset/drift, ancestry mismatch, event-log
recovery, automatic frontier dispatch, and provider adapter pagination/retry.
