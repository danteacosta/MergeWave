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

The `base_revision` is captured and fixed at dispatch. Workspace creation MUST
check out exactly that revision; it MUST NOT replace it with whatever HEAD is
current when a queued workspace is finally created. If the fixed revision is
unavailable, creation fails and no attempt is started.

There is at most one active `WorkAttempt` per work item. A retry is a new
attempt and must first cancel or terminally mark the previous attempt, destroy
or quarantine its workspace, and keep a reference to the superseded attempt.
An old attempt or its PR cannot satisfy the new attempt's gate. PR and
validation-evidence records carry the current `attempt_id` in addition to the
isolated branch/workspace identity. Automatic retry
or concurrent attempts are not enabled in v0.2; a retry is an explicit
operator/controller operation.

`wave_barrier` is intentionally strict. A slow or blocked item can hold the
barrier even when it is not a dependency of every remaining item. The escape
path is an explicit human decision to cancel or remove that item from the
project graph, followed by a new reconciliation and wave calculation; timeout
alone never silently skips an item.

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
warning to a policy decision later. The GitHub adapter implements the v1
best-effort check as an allowlist of declared path prefixes from the work-item
scope. Every changed file must match a prefix; an empty or unavailable
allowlist produces a warning rather than an approval claim. This is not a
complete semantic or generated-file analysis.

The default gate remains human-controlled. Auto-merge is out of scope.

An observation that is still progressing is not an operational failure: CI
running, stale CI awaiting a current-head result, review approval pending, and
the human merge not yet visible produce a `pending` gate and leave the ticket
in its current delivery state. `NeedsAttention` is reserved for a failed or
invalid observation that requires intervention, such as workspace drift,
failed CI, unresolved requested changes, or a merge revision that cannot be
proved to exist in the fetched target branch.

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
`WorkItem`, `Workspace`, `WorkerProfile` (permissions, sandbox, and cost
limit), and one versioned `SkillInvocation` (skill name, `skill_version`,
lifecycle stage, and optional manifest identity). The repository includes a
generic CLI runtime with timeout detection and a stdio JSON-RPC ACP transport
boundary. ACP propagates the skill envelope at session start; provider-specific
session semantics remain runtime adapter responsibilities.

A runtime may emit a `skill.result` event using the versioned skill-result
contract. The controller validates the result identity against the assigned
invocation and records `skill.result.recorded` with the `run_id`,
`attempt_id`, `workspace_id`, normalized result, and artifact bindings. Result
events are idempotent and are evidence for the attempt only. They never
satisfy delivery or human-merge gates; PR, CI, review, scope, ancestry, merge,
and release authority remain external to the skill.

## Failure classification and recovery

Operational failures are normalized into stable records with code, phase,
severity, retryability, human explanation, agent guidance, and next action.
The current vocabulary includes `workspace_missing`, `workspace_drift`,
`base_revision_mismatch`, `pull_request_unlinked`, `ci_pending`, `stale_ci`,
`merge_revision_not_in_target`,
`review_changes_requested`, `required_reviewer_missing`, `agent_timeout`,
`runtime_failed`, `tracker_authentication_failed`, `tracker_unavailable`,
`invalid_skill_result`, `retry_exhaustion`, and `reconciliation_interrupted`.

The SQLite event log is idempotent and can reduce its ordered event stream into
a `ControllerProjection` containing ticket states, base revision, attempt
states, active assignments, waves, gate states, validation evidence, pull
requests, and bound skill results. `DeliveryController.from_event_log()` rebuilds those durable
entities after restart. Runtime handles are intentionally not trusted across a
restart: the recovered controller requires a fresh workspace and delivery
observation before it can release an item. `ReconciliationLoop.reconcile_controller()`
connects the durable reconciliation boundary to the controller. Successful
approval refreshes the target base and dispatches the next frontier when
prompts are available; otherwise the event log records that the frontier is
waiting for prompts.

## Adapters

The current ports and adapters are:

- Linear GraphQL: paginated candidate reads, explicit `fetch_dependencies`, PR
  attachment verification, acceptance-criteria signal, state transitions,
  comments, and retry/backoff for rate-limit/server failures;
- GitHub: PR lookup, `base_sha_at_open`, current-head CI, latest review state,
  required reviewers, changed paths, merge, and ancestry observation;
- Git: `GitBaseRevisionProvider` fetches `origin/main` and proves that an
  observed `merge_revision` is an ancestor of that fetched target revision;
- CLI and ACP runtimes;
- ACP launch profiles for Codex, Claude Code, Gemini, and OpenHands. Profiles
  describe commands, capabilities, sandbox, permissions, and cost limits but
  do not import provider SDKs or resolve credentials;
- optional ARP 3.0 recorder.

Webhooks, multi-repository scheduling, dashboard UI, dependency inference,
conflict resolution, auto-merge, project-wide agent concurrency limits, and
project-wide cost budgets remain non-goals for this release. Runtime-level
cost limits and sandbox permissions exist in `WorkerProfile`; centralized
budget admission is deferred to a later policy layer. The real provider smoke
commands are opt-in, read-only checks and require operator-supplied
credentials plus a test project/repository; they are not executed in normal
CI.

## Naming and public positioning

Internal orchestrator names are intentionally not public project-name
candidates. MergeWave is the public project name. The one-line positioning is:

> A merge-gated, dependency-aware agentic delivery control plane.

This distinguishes MergeWave from implementation agents, issue trackers, and
generic workflow engines without claiming that it replaces them.

## ARP 3.0 integration

`Arp3Recorder` is the optional adapter boundary. It constructs
`RunManifestV3`, `EvidenceRecord`, `GateRequestV3`, and `GateDecisionV3`, calls
`.to_dict()`, and validates every payload with ARP `check_contract` before
writing it to the sink. It records `created_at`, `environment`, requested and
decided timestamps, valid evidence stages, `approve`/`block` decisions,
namespaced `software-delivery/v1` extensions, and content-addressed artifact
URI/hash references. A pending gate emits a request but no decision.

## Acceptance slice

The executable tests cover three independent roots and a dependent item,
shared/fresh bases, merge-gated release, stale CI, PR unlinking, resolved and
required-reviewer policy, scope warnings, acceptance-criteria capture,
timeouts, runtime failure, workspace reset/drift, ancestry mismatch, event-log
recovery, automatic frontier dispatch, and provider adapter pagination/retry.
