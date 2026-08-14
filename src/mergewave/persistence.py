"""Durable local event log for scheduling and reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
from collections.abc import Callable
from typing import Mapping, TypeVar


State = TypeVar("State")


@dataclass(frozen=True)
class EventRecord:
    sequence: int
    kind: str
    payload: Mapping[str, object]
    idempotency_key: str


class SqliteEventLog:
    def __init__(self, database: str) -> None:
        self._connection = sqlite3.connect(database)
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                payload TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE
            )
            """
        )
        self._connection.commit()

    def append(
        self,
        kind: str,
        payload: Mapping[str, object],
        *,
        idempotency_key: str,
    ) -> EventRecord:
        encoded_payload = json.dumps(payload, sort_keys=True)
        self._connection.execute(
            "INSERT OR IGNORE INTO events(kind, payload, idempotency_key) VALUES (?, ?, ?)",
            (kind, encoded_payload, idempotency_key),
        )
        self._connection.commit()
        row = self._connection.execute(
            "SELECT sequence, kind, payload, idempotency_key FROM events WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"event was not persisted: {idempotency_key}")
        return EventRecord(row[0], row[1], json.loads(row[2]), row[3])

    def events(self) -> tuple[EventRecord, ...]:
        rows = self._connection.execute(
            "SELECT sequence, kind, payload, idempotency_key FROM events ORDER BY sequence"
        ).fetchall()
        return tuple(EventRecord(row[0], row[1], json.loads(row[2]), row[3]) for row in rows)

    def reduce(self, initial: State, reducer: Callable[[State, EventRecord], State]) -> State:
        """Rebuild a projection solely from the durable event stream."""
        state = initial
        for event in self.events():
            state = reducer(state, event)
        return state

    def close(self) -> None:
        self._connection.close()
