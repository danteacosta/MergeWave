from __future__ import annotations

import unittest

from mergewave.contracts import (
    DependencyGraphError,
    WorkItemValidationError,
    compile_dependency_graph,
    validate_work_item,
)


def valid_item(item_id: str = "CTRL-1", blocked_by: list[str] | None = None) -> dict[str, object]:
    return {
        "id": item_id,
        "title": "Record a stable delivery failure reason",
        "problem": "Operators cannot recover safely when a blocked item has no structured reason.",
        "scope": {"in": ["Record the reason"], "out": ["Build a dashboard"]},
        "behavior": "When delivery cannot advance, the system records a stable failure code and next action.",
        "technical_context": {
            "summary": "The classifier runs after external observation.",
            "modules": ["scheduler"],
            "constraints": ["Do not infer provider state"],
        },
        "affected_paths": ["src/mergewave"],
        "acceptance_criteria": [{"id": "AC-1", "criterion": "A stable code is persisted."}],
        "test_scenarios": [{"id": "SC-1", "given": "A blocked item", "when": "It is reconciled", "then": "The reason is visible"}],
        "blocked_by": [] if blocked_by is None else blocked_by,
        "estimate_points": 3,
        "risk": {"level": "low", "reason": "The change is local."},
        "rollout": {"strategy": "Enable after simulator passes.", "kill_switch": "Disable the classifier."},
        "observability": {"events": ["delivery.failure_recorded"], "metrics": ["delivery.failures"]},
        "state": "Ready",
    }


class WorkItemContractTests(unittest.TestCase):
    def test_valid_work_item_is_accepted(self) -> None:
        work_item = validate_work_item(valid_item())

        self.assertEqual(work_item.item_id, "CTRL-1")
        self.assertEqual(work_item.blocked_by, ())
        self.assertEqual(work_item.state.value, "Ready")

    def test_missing_blocked_by_is_rejected_before_scheduling(self) -> None:
        item = valid_item()
        del item["blocked_by"]

        with self.assertRaises(WorkItemValidationError) as context:
            validate_work_item(item)

        self.assertEqual(context.exception.issues[0].code, "missing_field")
        self.assertEqual(context.exception.issues[0].field, "blocked_by")

    def test_estimate_above_five_points_is_rejected(self) -> None:
        item = valid_item()
        item["estimate_points"] = 6

        with self.assertRaises(WorkItemValidationError) as context:
            validate_work_item(item)

        self.assertEqual(context.exception.issues[0].code, "estimate_out_of_range")

    def test_missing_nested_scope_field_is_rejected(self) -> None:
        item = valid_item()
        del item["scope"]["in"]  # type: ignore[index]

        with self.assertRaises(WorkItemValidationError) as context:
            validate_work_item(item)

        self.assertEqual(context.exception.issues[0].code, "missing_field")
        self.assertEqual(context.exception.issues[0].field, "scope.in")

    def test_malformed_blocker_id_is_rejected(self) -> None:
        item = valid_item()
        item["blocked_by"] = ["not-an-item-id"]

        with self.assertRaises(WorkItemValidationError) as context:
            validate_work_item(item)

        self.assertEqual(context.exception.issues[0].code, "invalid_blocker_id")

    def test_dependency_graph_reports_the_exact_cycle(self) -> None:
        items = [
            valid_item("CTRL-1", ["CTRL-2"]),
            valid_item("CTRL-2", ["CTRL-1"]),
        ]

        with self.assertRaises(DependencyGraphError) as context:
            compile_dependency_graph(items)

        self.assertEqual(context.exception.cycle, ("CTRL-1", "CTRL-2", "CTRL-1"))


if __name__ == "__main__":
    unittest.main()
