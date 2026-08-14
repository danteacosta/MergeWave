from __future__ import annotations

import unittest

from mergewave.persistence import EventRecord, SqliteEventLog


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


if __name__ == "__main__":
    unittest.main()
