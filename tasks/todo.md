# MergeWave remaining work

## Implemented baseline

- [x] Linear bootstrap, explicit DAG compilation, state IDs, and polling
- [x] Attempt retry lifecycle and stale-PR isolation
- [x] Human `wave_barrier` escape
- [x] Controller restart rehydration for attempts and active wave membership
- [x] ACP and generic CLI runtime paths
- [x] Read-only real-provider smoke checks
- [x] ARP 3.0 manifests, episode/lifecycle records, evidence, gate records, and cross-repository CI

## Gaps before a production v1 claim

- [x] Bring the authoring validator to full schema/spec parity: strict nested types, minimum lengths, non-empty executable sections, additional-property rejection, imperative-title diagnostics, and test/migration/schema/foundation-only rejection
- [x] Persist prompt/snapshot references and ARP emission idempotency state so restart cannot duplicate portable records and can dispatch the next frontier without reconstructing prompts in memory
- [x] Add runtime reattachment or explicit orphan handling after restart; ACP snapshots can reattach and non-reattachable processes become explicit orphans
- [x] Add a long-lived supervisor that consumes runtime streams, applies timeout/cancellation/retry policy, and reconciles tracker/Git state continuously
- [x] Replace the provider-name launch-profile allowlist with a discoverable runtime-adapter registry and run one compatibility suite for ACP and CLI adapters, including Aider
- [x] Add a fail-closed, opt-in writable sandbox acceptance command for real Git worktrees, GitHub PR/check/review observations, and reversible Linear state transitions; live execution remains operator-supplied release evidence
- [x] Persist structured `FailureRecord` evidence IDs and mirror the same code, summary, guidance, and next action into tracker comments and agent continuation messages
- [x] Keep `soft_auto_merge` absent: controller configuration accepts only `human_only` merge authority and tests reject auto-merge requests
- [x] Remove the production-v1 claim from public status text; publication remains an early open-source release with live acceptance explicitly opt-in
