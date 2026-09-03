# Security policy

MergeWave is a control-plane library, not an authorization service. Runtime
providers are untrusted execution environments and must receive a bounded
authority envelope for every skill-enabled attempt.

Report suspected vulnerabilities privately to the repository owners before
opening a public issue. Do not include credentials, tokens, private repository
contents, or production data in a report.

The following invariants are security-sensitive:

- merge authority remains `human_only`;
- result events must identify their source run, invocation, attempt, and
  workspace;
- manifest and artifact hashes must be verified before evidence is recorded;
- authority expiry and allowed paths/operations must be enforced by the
  controller/runtime boundary;
- external provider state remains authoritative for delivery decisions.
