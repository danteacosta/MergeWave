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

- [ ] Bring the authoring validator to full schema/spec parity: strict nested types, minimum lengths, non-empty executable sections, additional-property rejection, imperative-title diagnostics, and test/migration/schema/foundation-only rejection
- [ ] Persist prompt/snapshot references and ARP emission idempotency state so restart cannot duplicate portable records and can dispatch the next frontier without reconstructing prompts in memory
- [ ] Add runtime reattachment or explicit orphan handling after restart; a rehydrated `RunHandle` currently cannot continue or cancel the original process
- [ ] Add a long-lived supervisor that consumes runtime streams, applies timeout/cancellation/retry policy, and reconciles tracker/Git state continuously
- [ ] Replace the provider-name launch-profile allowlist with a discoverable runtime-adapter registry and run one compatibility suite for ACP and CLI adapters, including Aider
- [ ] Add writable sandbox acceptance tests for real Git worktrees, GitHub PR/check/review observations, and Linear state transitions; current provider smoke tests are read-only
- [ ] Persist structured `FailureRecord` evidence IDs and mirror the same code, summary, guidance, and next action into tracker comments and agent continuation messages
- [ ] Decide whether to implement the optional `soft_auto_merge` policy; keep it absent or disabled until authority, race, and reconciliation tests exist
- [ ] Publish a reviewed branch and release only after the remaining v1 claims are either implemented or removed from public status text
