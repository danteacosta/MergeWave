# MergeWave

> A model-agnostic control plane that schedules implementation agents from an
> explicit dependency graph and releases downstream work only after verified
> CI, review, scope, merge, and base-revision evidence.

MergeWave coordinates delivery; it does not implement application code. The
first milestone is an offline deterministic simulator proving dependency-aware
waves, merge-gated release, and the distinction between normal commits,
workspace drift, and base-revision ancestry failures.

The project is based on the [Merge-Gated, Dependency-Aware Agentic Delivery
Control Plane specification](docs/merge-gated-delivery-control-plane-spec.md).

## Status

Early development. The simulator and contract tests are the first acceptance
gate. Linear, GitHub, ACP, CLI, and ARP 3.0 adapters will be added behind
ports after the offline path is proven.

## Development

```console
python -m unittest discover -s tests -v
```

## License

Apache-2.0. See [LICENSE](LICENSE).
