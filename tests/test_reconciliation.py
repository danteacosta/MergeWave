from __future__ import annotations

import unittest

from mergewave.persistence import SqliteEventLog
from mergewave.reconciliation import ReconciliationLoop


class ReconciliationLoopTests(unittest.TestCase):
    def test_same_external_revision_is_reconciled_idempotently(self) -> None:
        log = SqliteEventLog(":memory:")
        self.addCleanup(log.close)
        loop = ReconciliationLoop(log)

        first = loop.reconcile(
            item_id="CTRL-1",
            revision="main-1",
            state={"merged": True, "head_sha": "a-1"},
        )
        second = loop.reconcile(
            item_id="CTRL-1",
            revision="main-1",
            state={"merged": True, "head_sha": "a-1"},
        )

        self.assertEqual(first.event, second.event)
        self.assertEqual(len(log.events()), 1)

    def test_controller_reconciliation_persists_the_gate_outcome(self) -> None:
        class Controller:
            def reconcile(self, item_id: str):
                from mergewave.simulator import GateDecision

                return GateDecision("blocked")

        log = SqliteEventLog(":memory:")
        self.addCleanup(log.close)
        result = ReconciliationLoop(log).reconcile_controller(Controller(), "CTRL-1")

        self.assertEqual(result.state["gate_status"], "blocked")
        self.assertEqual(log.events()[-1].kind, "external_state.reconciled")


if __name__ == "__main__":
    unittest.main()
