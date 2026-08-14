"""Model-neutral Agent Client Protocol runtime boundary."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Protocol

from .runtime import AgentEvent, RunHandle, RunSpec


class AcpTransport(Protocol):
    def request(self, method: str, params: dict[str, object]) -> object: ...

    def events(self, session_id: str) -> Iterable[object]: ...


class AcpAgentRuntime:
    """Map a generic request/event transport onto MergeWave's AgentRuntime port."""

    def __init__(self, transport: AcpTransport) -> None:
        self._transport = transport

    def start(self, spec: RunSpec) -> RunHandle:
        response = self._transport.request(
            "session/start",
            {
                "run_id": spec.run_id,
                "work_item_id": spec.work_item_id,
                "prompt": spec.prompt,
                "workspace_path": spec.workspace_path,
            },
        )
        if not isinstance(response, Mapping) or not isinstance(response.get("session_id"), str):
            raise RuntimeError("ACP session/start did not return a session_id")
        return RunHandle(spec.run_id, response["session_id"])

    def stream(self, handle: RunHandle) -> Iterable[AgentEvent]:
        if not isinstance(handle.runtime_ref, str):
            raise RuntimeError("ACP run handle does not contain a session_id")
        for event in self._transport.events(handle.runtime_ref):
            if isinstance(event, Mapping):
                kind = str(event.get("type", "runtime.event"))
                payload = {str(key): value for key, value in event.items() if key != "type"}
            else:
                kind = "runtime.event"
                payload = {"value": event}
            yield AgentEvent(kind, payload)

    def cancel(self, handle: RunHandle) -> AgentEvent:
        if not isinstance(handle.runtime_ref, str):
            raise RuntimeError("ACP run handle does not contain a session_id")
        self._transport.request("session/cancel", {"session_id": handle.runtime_ref})
        return AgentEvent("runtime.cancelled", {"session_id": handle.runtime_ref})


__all__ = ["AcpAgentRuntime", "AcpTransport"]
