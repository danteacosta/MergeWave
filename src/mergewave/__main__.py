"""Command-line entrypoint for the MergeWave offline demo."""

from __future__ import annotations

import argparse
import json

from .simulator import MergeWaveSimulator


def _demo() -> dict[str, object]:
    simulator = MergeWaveSimulator(
        [
            {"id": "A", "blocked_by": []},
            {"id": "B", "blocked_by": []},
            {"id": "C", "blocked_by": []},
            {"id": "D", "blocked_by": ["A", "B", "C"]},
        ],
        policy="wave_barrier",
        base_revision="main-0",
    )
    dispatches = simulator.dispatch_ready()
    return {
        "base_revision": "main-0",
        "dispatches": [
            {"work_item_id": dispatch.work_item_id, "base_revision": dispatch.base_revision}
            for dispatch in dispatches
        ],
        "trace": [{"kind": event.kind, "item_id": event.item_id} for event in simulator.trace()],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mergewave")
    parser.add_argument("--version", action="version", version="0.1.0")
    parser.add_argument("--demo", action="store_true", help="run the offline frontier demo")
    args = parser.parse_args(argv)
    if not args.demo:
        parser.error("--demo is required for the offline simulator")
    print(json.dumps(_demo(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
