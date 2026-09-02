# MergeWave

> A model-agnostic control plane that schedules implementation agents from an
> explicit dependency graph and releases downstream work only after verified
> CI, review, scope, merge, and base-revision evidence.

MergeWave coordinates delivery; it does not implement application code. The
first milestone is an offline deterministic simulator proving dependency-aware
waves, merge-gated release, and the distinction between normal commits,
workspace drift, and base-revision ancestry failures.

## Research boundary

MergeWave is a separate software-delivery control-plane demonstrator. It is not
the master's primary experimental harness, a producer of confirmatory H1/H2
rows, or evidence that pre-final provenance improves defect detection. Its ARP
`software-delivery/v1` records may demonstrate interoperability and industrial
transfer, but they MUST NOT be pooled with the `agent-smell-degradation/v1`
dataset or used to qualify the thesis runtime producer.

The project is based on the [MergeWave design](docs/superpowers/specs/2026-08-14-mergewave-design.md), which documents the implemented authority,
workspace, event-recovery, and adapter contracts.

The executable Work Item contract is defined in
[docs/work-item.schema.json](docs/work-item.schema.json).
See [docs/work-item.example.json](docs/work-item.example.json) for a complete
authoring example.

## Status

Early open-source implementation; this repository does not make a production
v1 claim. The simulator and contract tests are the offline acceptance gate.
Linear, GitHub, ACP, CLI, and ARP 3.0 adapters are available behind
model-neutral ports; provider credentials and live operations are opt-in. Merge
authority is fixed to `human_only`: MergeWave observes a human merge and has no
auto-merge path.

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
- versioned skill invocations on `RunSpec` with ACP propagation;
- skill-result validation and immutable event-log bindings to attempts, workspaces, and artifact references;
- delivery controller that composes scheduler, workspaces, runtime, tracker, and observer;
- explicit `WorkAttempt`, `ExecutionWave`, `PullRequest`, `ValidationEvidence`, and `HumanGate` entities;
- configurable current-review policy with required approvals and reviewers;
- controller-driven reconciliation, automatic next-frontier dispatch, and event-log state projection;
- Linear bootstrap from executable ticket descriptions to a compiled DAG and live controller;
- explicit attempt retry with workspace destruction, supersession, and isolated retry branches;
- explicit human escape from `wave_barrier` plus controller restart rehydration;
- durable prompt/project snapshot references and restart-safe ARP emission identities;
- ACP session reattachment plus explicit orphan handling for non-reattachable runtimes;
- a long-lived supervisor with bounded runtime retry and continuous delivery reconciliation;
- discoverable ACP/CLI runtime registry with Codex, Claude Code, Gemini, OpenHands, and Aider profiles without provider SDK coupling;
- stable failure classification for workspace, runtime, tracker, delivery, and reconciliation failures;
- content-addressed failure evidence mirrored to tracker comments and capable agent sessions;
- optional ARP 3.0 wire recording from controller runs, gate requests, evidence, and decisions;
- ARP 3.0 value-object mapping with contract validation, episode/lifecycle records, namespaced delivery extensions, and content-addressed evidence artifacts;
- canonical project/DAG snapshot identity in ARP `source.input_ref` and `source.input_hash`;
- human gate requests emitted only after independently observed delivery evidence is ready for merge review;
- CI contract testing against the current ARP repository in addition to the optional packaged dependency;
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

### Writable disposable-provider acceptance

`mergewave --writable-acceptance` exercises a real Git worktree, an existing
GitHub PR/check/review observation, and Linear state/link/comment mutations.
It refuses to start unless the exact disposable-resource sentinel and every
target are supplied, restores the Linear issue's configured original state,
and removes its temporary worktree and branch. The linked PR attachment and
acceptance comment intentionally remain as audit evidence on the disposable
issue. See [the writable acceptance runbook](docs/writable-acceptance.md).

The ARP integration is optional. Install ARP 3.x in the environment before
constructing `Arp3Recorder`; the core remains usable without that dependency.
The repository CI also checks the adapter directly against
`danteacosta/agent-reliability-protocol@main` so cross-repository contract drift
cannot be hidden by the adapter's fake-contract unit tests.

### Agentic skill results

An optional `SkillInvocation` can be assigned when dispatching an item. The
invocation carries the skill name, `skill_version`, lifecycle stage, and
optional manifest identity. ACP runtimes receive this envelope at
`session/start`; the generic CLI fallback keeps the same `RunSpec` boundary.

Runtimes may emit a `skill.result` event using the agentic-skills result
contract. MergeWave validates the item and skill identity, then records the
result and every artifact reference with the `run_id`, `attempt_id`, and
`workspace_id`. A valid skill result is useful evidence for the attempt, but it
never satisfies PR, CI, review, scope, ancestry, merge, or human-gate checks.
See [the skill integration contract](docs/skill-integration.md).

## License

Apache-2.0. See [LICENSE](LICENSE).
