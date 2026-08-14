"""Idempotent reconciliation boundary for external delivery state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .persistence import EventRecord, SqliteEventLog


@dataclass(frozen=True)
class ReconciliationResult:
    item_id: str
    revision: str
    state: Mapping[str, object]
    event: EventRecord


class ReconciliationLoop:
    def __init__(self, event_log: SqliteEventLog) -> None:
        self._event_log = event_log

    def reconcile(
        self,
        *,
        item_id: str,
        revision: str,
        state: Mapping[str, object],
    ) -> ReconciliationResult:
        event = self._event_log.append(
            "external_state.reconciled",
            {"item_id": item_id, "revision": revision, "state": dict(state)},
            idempotency_key=f"reconcile:{item_id}:{revision}",
        )
        return ReconciliationResult(item_id, revision, state, event)
