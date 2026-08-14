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

## Status

Early development. The simulator and contract tests are the first acceptance
gate. Linear, GitHub, ACP, CLI, and ARP 3.0 adapters will be added behind
ports after the offline path is proven.

## Development

```console
PYTHONPATH=src python -m unittest discover -s tests -v
```

## License

Apache-2.0. See [LICENSE](LICENSE).
