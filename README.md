# MergeWave

> A model-agnostic control plane that schedules implementation agents from an
> explicit dependency graph and releases downstream work only after verified
> CI, review, scope, merge, and base-revision evidence.

MergeWave coordinates delivery; it does not implement application code. The
first milestone is an offline deterministic simulator proving dependency-aware
waves, merge-gated release, and the distinction between normal commits,
workspace drift, and base-revision ancestry failures.

The project is based on the [MergeWave design](docs/superpowers/specs/2026-08-14-mergewave-design.md), which documents the implemented authority,
workspace, event-recovery, and adapter contracts.

The executable Work Item contract is defined in
[docs/work-item.schema.json](docs/work-item.schema.json).
See [docs/work-item.example.json](docs/work-item.example.json) for a complete
authoring example.

## Status

Early open-source implementation. The simulator and contract tests are the
acceptance gate. Linear, GitHub, ACP, CLI, and ARP 3.0 adapters are available
behind model-neutral ports; provider credentials and live merge operations are
opt-in.

The current offline slice includes:

- Work Item validation and explicit DAG cycle detection;
- `continuous_frontier` and `wave_barrier` scheduling;
- SQLite event-log idempotency;
- idempotent reconciliation keyed by item and observed base revision;
- Git worktree creation, HEAD tracking, and ancestry checks;
- generic CLI runtime fallback;
- Linear GraphQL tracker adapter with explicit blocker mapping;
- GitHub delivery observer for PR, CI, review, scope, merge, and ancestry evidence;
- ACP runtime adapter for model-neutral session/event transports;
- delivery controller that composes scheduler, workspaces, runtime, tracker, and observer;
- explicit `WorkAttempt`, `ExecutionWave`, `PullRequest`, `ValidationEvidence`, and `HumanGate` entities;
- configurable current-review policy with required approvals and reviewers;
- controller-driven reconciliation, automatic next-frontier dispatch, and event-log state projection;
- Linear bootstrap from executable ticket descriptions to a compiled DAG and live controller;
- explicit attempt retry with workspace destruction, supersession, and isolated retry branches;
- explicit human escape from `wave_barrier` plus controller restart rehydration;
- ACP launch profiles for Codex, Claude Code, Gemini, and OpenHands without provider SDK coupling;
- stable failure classification for workspace, runtime, tracker, delivery, and reconciliation failures;
- optional ARP 3.0 wire recording from controller runs, gate requests, evidence, and decisions;
- ARP 3.0 value-object mapping with contract validation, namespaced delivery extensions, and content-addressed evidence artifacts;
- deterministic simulator trace and CLI demo.

## Development

```console
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m mergewave --demo
```

### Read-only provider smoke checks

The smoke commands perform observation only; they do not change Linear issues,
GitHub pull requests, branches, reviews, or merges. They require credentials
only when invoked and are safe to omit from CI.

```console
MERGEWAVE_LINEAR_API_KEY=... \
MERGEWAVE_LINEAR_TEAM_ID=... \
PYTHONPATH=src python -m mergewave --linear-smoke

MERGEWAVE_GITHUB_TOKEN=... \
MERGEWAVE_GITHUB_REPOSITORY=owner/repository \
MERGEWAVE_GITHUB_ITEM_ID=CTRL-1 \
MERGEWAVE_GITHUB_BRANCH=mergewave/CTRL-1 \
MERGEWAVE_GITHUB_BASE_REVISION=main \
MERGEWAVE_GITHUB_SCOPE_PATHS=src/mergewave/,tests/ \
PYTHONPATH=src python -m mergewave --github-smoke
```

The commands emit one JSON summary suitable for attaching to a run record or
using as input to a later reconciliation step.

These are real provider checks when invoked with credentials. They are
deliberately read-only: run them against a disposable Linear team and GitHub
repository before enabling them in an operational environment. No credential
or review body is printed.

The ARP integration is optional. Install ARP 3.x in the environment before
constructing `Arp3Recorder`; the core remains usable without that dependency.

## License

Apache-2.0. See [LICENSE](LICENSE).
