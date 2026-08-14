"""Dependency-aware scheduling policies used by MergeWave."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace

from .contracts import DependencyGraph
from .domain import ExecutionWave


@dataclass(frozen=True)
class Dispatch:
    work_item_id: str
    base_revision: str


class Scheduler:
    def __init__(self, graph: DependencyGraph, *, policy: str, base_revision: str) -> None:
        if policy not in {"continuous_frontier", "wave_barrier"}:
            raise ValueError(f"unsupported scheduling policy: {policy}")
        self._graph = graph
        self._policy = policy
        self._base_revision = base_revision
        self._dispatched: set[str] = set()
        self._released: set[str] = set()
        self._active_wave: set[str] = set()
        self._barrier_open = True
        self._wave_sequence = 0
        self._current_wave: ExecutionWave | None = None
        self._waves: list[ExecutionWave] = []

    def dispatch_ready(self) -> tuple[Dispatch, ...]:
        ready = self.preview_ready()
        for dispatch in ready:
            self._dispatched.add(dispatch.work_item_id)

        if self._policy == "wave_barrier" and ready:
            self._active_wave = {dispatch.work_item_id for dispatch in ready}
            self._barrier_open = False
        if ready:
            self._wave_sequence += 1
            self._current_wave = ExecutionWave(
                wave_id=f"wave-{self._wave_sequence}",
                base_sha=self._base_revision,
                work_item_ids=tuple(dispatch.work_item_id for dispatch in ready),
                state="active",
            )
            self._waves.append(self._current_wave)
        return ready

    def preview_ready(self) -> tuple[Dispatch, ...]:
        if self._policy == "wave_barrier" and not self._barrier_open:
            return ()

        ready = []
        for item in self._graph.ready(self._released):
            if item.item_id in self._dispatched:
                continue
            dispatch = Dispatch(item.item_id, self._base_revision)
            ready.append(dispatch)
        return tuple(ready)

    def release(self, item_id: str) -> None:
        if item_id not in self._dispatched:
            raise ValueError(f"item was not dispatched: {item_id}")
        self._released.add(item_id)
        if self._current_wave and set(self._current_wave.work_item_ids).issubset(self._released):
            self._current_wave = replace(self._current_wave, state="released")
            self._waves[-1] = self._current_wave

    def refresh_target_base(self, revision: str) -> None:
        if self._policy == "wave_barrier":
            if not self._active_wave or not self._active_wave.issubset(self._released):
                raise ValueError("cannot refresh target base before the active wave is released")
            self._active_wave = set()
            self._barrier_open = True
        self._base_revision = revision

    def current_execution_wave(self) -> ExecutionWave | None:
        return self._current_wave

    def execution_waves(self) -> tuple[ExecutionWave, ...]:
        return tuple(self._waves)
