from __future__ import annotations

import json
import unittest

from mergewave.bootstrap import build_linear_application
from mergewave.git_workspace import Workspace
from mergewave.runtime import RunHandle, RunSpec


def work_item(item_id: str, blocked_by: list[str]) -> dict[str, object]:
    return {
        "id": item_id,
        "title": f"Implement {item_id}",
        "problem": "A sufficiently detailed problem statement for execution.",
        "scope": {"in": ["src/"], "out": ["docs/"]},
        "behavior": "The implementation has an observable behavior for the caller.",
        "technical_context": {"summary": "Context", "modules": ["src/"], "constraints": ["Keep scope"]},
        "affected_paths": ["src/"],
        "acceptance_criteria": [{"id": "AC-1", "criterion": "It works."}],
        "test_scenarios": [{"id": "SC-1", "given": "a system", "when": "called", "then": "it works"}],
        "blocked_by": blocked_by,
        "estimate_points": 3,
        "risk": {"level": "low", "reason": "bounded"},
        "rollout": {"strategy": "normal", "kill_switch": "none"},
        "observability": {"events": ["done"], "metrics": ["latency"]},
        "state": "Ready",
    }


class Tracker:
    def __init__(self, candidates: list[dict[str, object]]) -> None:
        self.candidates = candidates

    def fetch_candidates(self): return self.candidates
    def fetch_dependencies(self, item_id: str): return []
    def resolve_state_id(self, state: str): return state
    def transition_state(self, item_id: str, state: str): pass
    def link_pull_request(self, item_id: str, url: str): pass
    def post_comment(self, item_id: str, body: str): pass
    def pull_request_linked(self, item_id: str, url: str): return True
    def acceptance_criteria_signal(self, item_id: str): return "unknown"


class Base:
    def current_revision(self): return "main-0"


class WorkspaceFactory:
    def create(self, item_id: str, base_revision: str):
        return Workspace(item_id, "repo", f"/worktrees/{item_id}", f"mergewave/{item_id}", base_revision, base_revision, base_revision)
    def inspect(self, workspace): return workspace
    def destroy(self, workspace): return workspace


class Runtime:
    def start(self, spec: RunSpec): return RunHandle(spec.run_id, object())
    def stream(self, handle): return iter(())
    def continue_run(self, handle, input): pass
    def cancel(self, handle): pass
    def capabilities(self): return None


class Observer:
    def observe(self, item_id: str, workspace: Workspace): raise AssertionError("not reached")


class BootstrapTests(unittest.TestCase):
    def test_bootstrap_validates_ticket_contract_compiles_dag_and_creates_controller(self) -> None:
        raw_a = work_item("CTRL-1", [])
        raw_b = work_item("CTRL-2", ["CTRL-1"])
        candidates = [
            {"id": "CTRL-1", "blocked_by": [], "state": "Todo", "description": json.dumps(raw_a)},
            {"id": "CTRL-2", "blocked_by": ["CTRL-1"], "state": "Todo", "description": json.dumps(raw_b)},
        ]

        application = build_linear_application(
            tracker=Tracker(candidates), base_revision_provider=Base(), workspace_factory=WorkspaceFactory(),
            runtime=Runtime(), observer=Observer(), policy="continuous_frontier",
        )

        self.assertEqual(set(application.graph.items), {"CTRL-1", "CTRL-2"})
        self.assertEqual(tuple(dispatch.work_item_id for dispatch in application.start()), ("CTRL-1",))
        self.assertIn("CTRL-1", application.controller.active_item_ids())

    def test_bootstrap_rejects_a_non_executable_linear_description(self) -> None:
        with self.assertRaisesRegex(ValueError, "work-item JSON contract"):
            build_linear_application(
                tracker=Tracker([{"id": "CTRL-1", "description": "just a title", "blocked_by": [], "state": "Todo"}]),
                base_revision_provider=Base(), workspace_factory=WorkspaceFactory(), runtime=Runtime(), observer=Observer(), policy="continuous_frontier",
            )


if __name__ == "__main__": unittest.main()
