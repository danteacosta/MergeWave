# MergeWave

> A model-agnostic control plane that schedules implementation agents from an
> explicit dependency graph and releases downstream work only after verified
> CI, review, scope, merge, and base-revision evidence.

MergeWave coordinates delivery; it does not implement application code. The
first milestone is an offline deterministic simulator proving dependency-aware
waves, merge-gated release, and the distinction between normal commits,
workspace drift, and base-revision ancestry failures.

The project is based on the [MergeWave simulator-first design](docs/superpowers/specs/2026-08-14-mergewave-design.md), which implements the
authority and workspace invariants from the Merge-Gated, Dependency-Aware
Agentic Delivery Control Plane specification.

The executable Work Item contract is defined in
[docs/work-item.schema.json](docs/work-item.schema.json).
See [docs/work-item.example.json](docs/work-item.example.json) for a complete
authoring example.

## Status

Early development. The simulator and contract tests are the first acceptance
gate. Linear, GitHub, and ACP adapters are available behind ports; the generic
CLI runtime and ARP 3.0 recorder remain available for deployments that do not
use those adapters.

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
- optional ARP 3.0 wire recording from controller runs, gate requests, evidence, and decisions;
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

## License

Apache-2.0. See [LICENSE](LICENSE).
