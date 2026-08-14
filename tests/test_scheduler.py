from __future__ import annotations

import unittest

from mergewave.contracts import DependencyGraph, WorkItem
from mergewave.scheduler import Scheduler


def graph() -> DependencyGraph:
    return DependencyGraph(
        {
            "A": WorkItem("A", ()),
            "B": WorkItem("B", ()),
            "C": WorkItem("C", ()),
            "D": WorkItem("D", ("A", "B", "C")),
        }
    )


class SchedulerTests(unittest.TestCase):
    def test_continuous_frontier_dispatches_dependents_from_fresh_base(self) -> None:
        scheduler = Scheduler(graph(), policy="continuous_frontier", base_revision="main-0")

        first = scheduler.dispatch_ready()
        for item_id in ("A", "B", "C"):
            scheduler.release(item_id)
        scheduler.refresh_target_base("main-3")
        second = scheduler.dispatch_ready()

        self.assertEqual({dispatch.work_item_id for dispatch in first}, {"A", "B", "C"})
        self.assertEqual([(dispatch.work_item_id, dispatch.base_revision) for dispatch in second], [("D", "main-3")])

    def test_wave_barrier_does_not_refresh_until_every_item_is_released(self) -> None:
        scheduler = Scheduler(graph(), policy="wave_barrier", base_revision="main-0")
        scheduler.dispatch_ready()
        scheduler.release("A")
        scheduler.release("B")

        with self.assertRaises(ValueError):
            scheduler.refresh_target_base("main-2")

        scheduler.release("C")
        scheduler.refresh_target_base("main-3")

        self.assertEqual(
            [(dispatch.work_item_id, dispatch.base_revision) for dispatch in scheduler.dispatch_ready()],
            [("D", "main-3")],
        )


if __name__ == "__main__":
    unittest.main()
