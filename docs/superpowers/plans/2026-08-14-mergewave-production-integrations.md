# MergeWave Production Integrations Implementation Plan

> **For agentic workers:** Execute this plan task-by-task with ATDD tests and a verification checkpoint after each task.

**Goal:** Close the remaining operational gaps between the MergeWave core and real Linear, agent runtimes, retries, restart recovery, and provider smoke checks.

**Architecture:** Keep the core provider-neutral. Add a composition/bootstrap layer for Linear and explicit lifecycle operations to the scheduler/controller. Provider-specific runtimes are profiles over the existing `AgentRuntime` port; real provider smoke checks remain opt-in and read-only.

**Tech Stack:** Python 3.11+, dataclasses, Protocol ports, SQLite event log, Linear GraphQL, GitHub REST, JSONL/ARP 3.0.

---

### Task 1: Linear bootstrap and state IDs

**Files:**
- Create: `src/mergewave/bootstrap.py`
- Modify: `src/mergewave/linear_adapter.py`
- Modify: `src/mergewave/ports.py`
- Modify: `tests/test_linear_adapter.py`
- Create: `tests/test_bootstrap.py`

- [x] Add failing tests for state-name-to-ID resolution, candidate normalization, dependency graph compilation, and controller construction.
- [x] Add explicit Linear state lookup and cache it per team.
- [x] Add a bootstrap factory that fetches candidates, validates work items, compiles the DAG, and creates the controller from injected ports.
- [x] Add a polling method that reconciles active items and refreshes the frontier without mutating provider state except ticket transitions/comments.
- [x] Run focused and full tests.

### Task 2: Attempt retry lifecycle

**Files:**
- Modify: `src/mergewave/domain.py`
- Modify: `src/mergewave/controller.py`
- Modify: `src/mergewave/scheduler.py`
- Modify: `src/mergewave/git_workspace.py`
- Create: `tests/test_retry.py`

- [x] Add failing tests proving an old active attempt is cancelled, its workspace is destroyed/quarantined, and its state becomes superseded.
- [x] Add a controller retry operation that creates a new attempt with a new ID and preserves the predecessor reference.
- [x] Bind PR/gate evidence to attempt ID so an old PR cannot satisfy a new attempt.
- [x] Run retry-focused tests and the full suite.

### Task 3: Human escape from `wave_barrier`

**Files:**
- Modify: `src/mergewave/scheduler.py`
- Modify: `src/mergewave/simulator.py`
- Modify: `src/mergewave/controller.py`
- Create: `tests/test_wave_escape.py`

- [x] Add failing tests for a human cancellation/removal decision and its durable event.
- [x] Implement explicit `cancel_from_wave(item_id, reason)`; timeout alone must not skip an item.
- [x] Recompute the ready frontier only after the cancellation is recorded.
- [x] Run focused and full tests.

### Task 4: Restart rehydration

**Files:**
- Modify: `src/mergewave/persistence.py`
- Modify: `src/mergewave/controller.py`
- Modify: `src/mergewave/reconciliation.py`
- Create: `tests/test_rehydration.py`

- [x] Add failing tests that close/reopen SQLite and recover ticket states, waves, attempts, gates, and target base.
- [x] Extend the projection with serializable active assignments and gate evidence references.
- [x] Add `DeliveryController.from_event_log(...)` that rehydrates state while requiring fresh external observations before release.
- [x] Run restart-focused and full tests.

### Task 5: ACP runtime profiles

**Files:**
- Create: `src/mergewave/runtime_profiles.py`
- Modify: `src/mergewave/acp_runtime.py`
- Modify: `src/mergewave/runtime.py`
- Create: `tests/test_runtime_profiles.py`

- [x] Add contract tests for Codex, Claude Code, Gemini, and OpenHands launch/profile metadata without importing their SDKs.
- [x] Implement command/transport profiles over `WorkerProfile` and `RuntimeCapabilities`.
- [x] Keep credentials, model names, and provider-specific fields outside core contracts.
- [x] Run focused and full tests.

### Task 6: Real smoke checks and release documentation

**Files:**
- Modify: `src/mergewave/smoke.py`
- Modify: `src/mergewave/__main__.py`
- Modify: `tests/test_smoke.py`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-08-14-mergewave-design.md`

- [x] Add opt-in Linear and GitHub smoke commands that perform read-only observation and emit normalized summaries.
- [x] Add explicit configuration validation and redaction; never print tokens or review content.
- [x] Run mocked smoke tests in CI and real smoke tests only when credentials/configuration are present.
- [ ] Run full tests, compile, schema checks, diff review, and CI.
