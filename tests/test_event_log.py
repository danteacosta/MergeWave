from __future__ import annotations

import unittest

from mergewave.persistence import EventRecord, SqliteEventLog
from mergewave.controller import ControllerProjection


class EventLogTests(unittest.TestCase):
    def test_idempotency_prevents_duplicate_delivery_events(self) -> None:
        log = SqliteEventLog(":memory:")
        self.addCleanup(log.close)

        first = log.append(
            "dispatch.created",
            {"work_item_id": "A", "base_revision": "main-0"},
            idempotency_key="dispatch:A:main-0",
        )
        duplicate = log.append(
            "dispatch.created",
            {"work_item_id": "A", "base_revision": "main-0"},
            idempotency_key="dispatch:A:main-0",
        )

        self.assertEqual(first, duplicate)
        self.assertEqual(log.events(), (first,))

    def test_events_are_replayed_in_sequence_order(self) -> None:
        log = SqliteEventLog(":memory:")
        self.addCleanup(log.close)
        log.append("dispatch.created", {"work_item_id": "A"}, idempotency_key="dispatch:A")
        log.append("delivery.observed", {"work_item_id": "A"}, idempotency_key="delivery:A")

        events = log.events()

        self.assertEqual([event.sequence for event in events], [1, 2])
        self.assertIsInstance(events[0], EventRecord)
        self.assertEqual(events[1].kind, "delivery.observed")

    def test_event_log_reduces_events_into_recoverable_state(self) -> None:
        log = SqliteEventLog(":memory:")
        self.addCleanup(log.close)
        log.append("ticket.state_changed", {"item_id": "A", "state": "InProgress"}, idempotency_key="state:A:1")
        log.append("base_revision.refreshed", {"revision": "main-1"}, idempotency_key="base:main-1")
        log.append("ticket.state_changed", {"item_id": "A", "state": "Done"}, idempotency_key="state:A:2")

        state = log.reduce({"ticket_states": {}, "base_revision": None}, self._reduce)

        self.assertEqual(state, {"ticket_states": {"A": "Done"}, "base_revision": "main-1"})

    def test_controller_projection_rebuilds_ticket_and_attempt_state(self) -> None:
        log = SqliteEventLog(":memory:")
        self.addCleanup(log.close)
        log.append("ticket.state_changed", {"item_id": "A", "state": "Done"}, idempotency_key="state:A")
        log.append("work_attempt.started", {"run_id": "run:A"}, idempotency_key="attempt:A")
        log.append("work_attempt.state_changed", {"run_id": "run:A", "state": "released"}, idempotency_key="attempt:A:released")
        log.append("execution_wave.started", {"wave_id": "wave-1"}, idempotency_key="wave:1")
        log.append("execution_wave.state_changed", {"wave_id": "wave-1", "state": "released"}, idempotency_key="wave:1:released")

        projection = ControllerProjection.from_event_log(log)

        self.assertEqual(projection.ticket_states["A"], "Done")
        self.assertEqual(projection.attempt_states["run:A"], "released")
        self.assertEqual(projection.wave_states["wave-1"], "released")

    @staticmethod
    def _reduce(state: dict[str, object], event: EventRecord) -> dict[str, object]:
        next_state = {"ticket_states": dict(state["ticket_states"]), "base_revision": state["base_revision"]}
        if event.kind == "ticket.state_changed":
            next_state["ticket_states"][event.payload["item_id"]] = event.payload["state"]
        elif event.kind == "base_revision.refreshed":
            next_state["base_revision"] = event.payload["revision"]
        return next_state


if __name__ == "__main__":
    unittest.main()
