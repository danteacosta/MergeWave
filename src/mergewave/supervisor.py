"""Long-lived supervision for runtime streams and authoritative delivery state."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from queue import Empty, SimpleQueue
from threading import Thread
import time

from .controller import DeliveryController
from .runtime import AgentEvent
from .simulator import FailureRecord, GateDecision


@dataclass(frozen=True)
class SupervisorPolicy:
    poll_interval_seconds: float = 5.0
    max_attempts: int = 2
    retry_runtime_failures: bool = True
    runtime_timeout_seconds: float | None = 1800.0

    def __post_init__(self) -> None:
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if self.runtime_timeout_seconds is not None and self.runtime_timeout_seconds <= 0:
            raise ValueError("runtime_timeout_seconds must be positive")


@dataclass(frozen=True)
class SupervisionCycle:
    decisions: Mapping[str, GateDecision]
    runtime_failures: Mapping[str, FailureRecord]
    retried_item_ids: tuple[str, ...]


class DeliverySupervisor:
    """Consume each runtime stream once and continuously reconcile delivery."""

    def __init__(
        self,
        controller: DeliveryController,
        *,
        policy: SupervisorPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._controller = controller
        self._policy = policy or SupervisorPolicy()
        self._sleep = sleep
        self._monotonic = monotonic
        self._consumed_runs: set[str] = set()
        self._stream_threads: dict[str, Thread] = {}
        self._stream_started_at: dict[str, float] = {}
        self._events: SimpleQueue[tuple[str, str, AgentEvent | None]] = SimpleQueue()

    def run_once(self) -> SupervisionCycle:
        failures: dict[str, FailureRecord] = {}
        retried: list[str] = []
        for item_id in self._controller.active_item_ids():
            assignment = self._controller.active_assignment(item_id)
            run_id = assignment.handle.run_id
            if assignment.runtime_attached and run_id not in self._consumed_runs and run_id not in self._stream_threads:
                self._start_stream(item_id, run_id)

        events_by_item: dict[str, list[AgentEvent]] = defaultdict(list)
        while True:
            try:
                item_id, run_id, event = self._events.get_nowait()
            except Empty:
                break
            if event is None:
                self._consumed_runs.add(run_id)
                self._stream_threads.pop(run_id, None)
                self._stream_started_at.pop(run_id, None)
                continue
            if item_id in self._controller.active_item_ids() and self._controller.active_assignment(item_id).handle.run_id == run_id:
                events_by_item[item_id].append(event)

        timeout = self._policy.runtime_timeout_seconds
        if timeout is not None:
            now = self._monotonic()
            for item_id in self._controller.active_item_ids():
                run_id = self._controller.active_assignment(item_id).handle.run_id
                started_at = self._stream_started_at.get(run_id)
                if started_at is not None and now - started_at >= timeout:
                    events_by_item[item_id].append(AgentEvent("runtime.timeout", {"timeout_seconds": timeout}))
                    self._consumed_runs.add(run_id)

        for item_id, events in events_by_item.items():
            failure = self._controller.observe_runtime_events(item_id, events)
            if failure is not None:
                assignment = self._controller.active_assignment(item_id)
                failures[item_id] = failure
                if (
                    self._policy.retry_runtime_failures
                    and failure.retryable
                    and assignment.dispatch.attempt_number < self._policy.max_attempts
                ):
                    self._controller.retry(item_id)
                    retried.append(item_id)

        decisions: dict[str, GateDecision] = {}
        for item_id in self._controller.active_item_ids():
            decisions[item_id] = self._controller.reconcile(item_id)
        return SupervisionCycle(decisions, failures, tuple(retried))

    def _start_stream(self, item_id: str, run_id: str) -> None:
        def consume() -> None:
            try:
                for event in self._controller.runtime_events(item_id):
                    self._events.put((item_id, run_id, event))
            except TimeoutError:
                self._events.put((item_id, run_id, AgentEvent("runtime.timeout", {})))
            except Exception as error:
                self._events.put(
                    (item_id, run_id, AgentEvent("runtime.exited", {"returncode": 1, "error": str(error)}))
                )
            finally:
                self._events.put((item_id, run_id, None))

        thread = Thread(target=consume, name=f"mergewave-runtime-{run_id}", daemon=True)
        self._stream_threads[run_id] = thread
        self._stream_started_at[run_id] = self._monotonic()
        thread.start()

    def run_forever(self, *, should_stop: Callable[[], bool]) -> None:
        while not should_stop():
            self.run_once()
            if not should_stop():
                self._sleep(self._policy.poll_interval_seconds)


__all__ = ["DeliverySupervisor", "SupervisionCycle", "SupervisorPolicy"]
