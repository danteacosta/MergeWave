# MergeWave — Simulator-First Design

Status: approved baseline

## Goal

Build a model-agnostic delivery control plane that dispatches implementation
agents from an explicit dependency graph and releases downstream work only
after independent verification of PR, CI, review, scope, merge, and base
revision state.

The first milestone is an offline deterministic simulator. It proves the
control-plane authority boundary before any Linear, GitHub, Git, ACP, CLI, or
model-provider integration is added.

## Architecture

The core owns normalized domain contracts and policies. External systems enter
through ports:

```text
Tracker → Work-item validator → DAG compiler → Scheduler
                                      ↓
                    Workspace → Agent runtime → Delivery observers
                                      ↓
                       Evidence → Gate evaluator → Reconciliation
```

The first implementation uses fake ports for all external systems. Real
adapters will implement the same contracts later. ARP 3.0 is an optional
reliability adapter and is not imported by the core.

## Critical invariants

- `initial_head_revision == base_revision` when a workspace is created;
- normal agent commits advance `current_head_revision` and are not drift;
- `base_revision` must be an ancestor of `pr.head_sha` at delivery;
- repository, worktree, branch, unexpected reset, and local-history violations
  produce `workspace_drift`;
- failed ancestry produces `base_revision_mismatch`;
- agent completion claims never approve a gate;
- downstream work starts only after independently observed release evidence.

## First acceptance slice

The simulator models three independent items in wave one and a fourth item
blocked by all three. It verifies shared base revision, blocked dispatch,
human-gated release, fresh `origin/main`, next-wave workspace creation, stale
CI, out-of-scope diff, timeout, workspace drift, ancestry mismatch, and
restart reconciliation.

## Testing strategy

Tests are public-behavior contract tests using real fake ports and a
deterministic clock. They do not assert private data structures. The first
five ATDD scenarios are recorded in the implementation plan and become the
simulator's executable contract.

## Deliberate non-goals for the first slice

No real provider calls, webhooks, dashboard, multi-repository scheduling,
dependency inference, conflict resolution, or default auto-merge.
