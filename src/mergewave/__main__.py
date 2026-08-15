"""Command-line entrypoint for the MergeWave offline demo."""

from __future__ import annotations

import argparse
import json
import os
import sys

from .smoke import SmokeConfigurationError, github_read_only_smoke, linear_read_only_smoke
from .live_acceptance import (
    AcceptanceConfigurationError,
    WritableAcceptanceConfig,
    run_writable_acceptance,
)
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
    parser.add_argument("--linear-smoke", action="store_true", help="run a read-only Linear API smoke check")
    parser.add_argument("--github-smoke", action="store_true", help="run a read-only GitHub API smoke check")
    parser.add_argument(
        "--writable-acceptance",
        action="store_true",
        help="mutate explicitly disposable GitHub/Linear acceptance resources",
    )
    args = parser.parse_args(argv)
    modes = sum((args.demo, args.linear_smoke, args.github_smoke, args.writable_acceptance))
    if modes != 1:
        parser.error(
            "choose exactly one of --demo, --linear-smoke, --github-smoke, or --writable-acceptance"
        )
    try:
        if args.demo:
            result = _demo()
        elif args.linear_smoke:
            result = linear_read_only_smoke(
                os.environ.get("MERGEWAVE_LINEAR_API_KEY", ""),
                os.environ.get("MERGEWAVE_LINEAR_TEAM_ID", ""),
            )
        elif args.github_smoke:
            result = github_read_only_smoke(
                os.environ.get("MERGEWAVE_GITHUB_TOKEN", ""),
                os.environ.get("MERGEWAVE_GITHUB_REPOSITORY", ""),
                item_id=os.environ.get("MERGEWAVE_GITHUB_ITEM_ID", ""),
                branch_ref=os.environ.get("MERGEWAVE_GITHUB_BRANCH", ""),
                base_revision=os.environ.get("MERGEWAVE_GITHUB_BASE_REVISION", ""),
                scope_paths=tuple(
                    path.strip()
                    for path in os.environ.get("MERGEWAVE_GITHUB_SCOPE_PATHS", "").split(",")
                    if path.strip()
                ),
            )
        else:
            result = run_writable_acceptance(WritableAcceptanceConfig.from_env())
    except (SmokeConfigurationError, AcceptanceConfigurationError) as error:
        print(json.dumps({"error": "configuration", "message": str(error)}), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
