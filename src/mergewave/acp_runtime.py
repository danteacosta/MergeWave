"""Model-neutral Agent Client Protocol runtime boundary."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import itertools
import json
import subprocess
from typing import Protocol

from .runtime import AgentEvent, RunHandle, RunSpec, RuntimeCapabilities


class AcpTransport(Protocol):
    def request(self, method: str, params: dict[str, object]) -> object: ...

    def events(self, session_id: str) -> Iterable[object]: ...


class AcpAgentRuntime:
    """Map a generic request/event transport onto MergeWave's AgentRuntime port."""

    def __init__(self, transport: AcpTransport, *, capabilities: RuntimeCapabilities | None = None) -> None:
        self._transport = transport
        self._capabilities = capabilities or RuntimeCapabilities(True, True, True, ("acp",), True, True)

    def start(self, spec: RunSpec) -> RunHandle:
        params: dict[str, object] = {
            "run_id": spec.run_id,
            "work_item_id": spec.work_item_id,
            "prompt": spec.prompt,
            "workspace_path": spec.workspace_path,
        }
        if spec.skill is not None:
            params["skill"] = spec.skill.to_payload()
        response = self._transport.request(
            "session/start",
            params,
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
                raw_payload = event.get("payload")
                if isinstance(raw_payload, Mapping):
                    payload = {str(key): value for key, value in raw_payload.items()}
                else:
                    payload = {str(key): value for key, value in event.items() if key != "type"}
            else:
                kind = "runtime.event"
                payload = {"value": event}
            yield AgentEvent(kind, payload)

    def continue_run(self, handle: RunHandle, input: str) -> None:
        if not isinstance(handle.runtime_ref, str):
            raise RuntimeError("ACP run handle does not contain a session_id")
        self._transport.request(
            "session/continue",
            {"session_id": handle.runtime_ref, "input": input},
        )

    def cancel(self, handle: RunHandle) -> AgentEvent:
        if not isinstance(handle.runtime_ref, str):
            raise RuntimeError("ACP run handle does not contain a session_id")
        self._transport.request("session/cancel", {"session_id": handle.runtime_ref})
        return AgentEvent("runtime.cancelled", {"session_id": handle.runtime_ref})

    def capabilities(self) -> RuntimeCapabilities:
        return self._capabilities

    def snapshot(self, handle: RunHandle) -> Mapping[str, object]:
        if not isinstance(handle.runtime_ref, str):
            raise RuntimeError("ACP run handle does not contain a session_id")
        return {"session_id": handle.runtime_ref}

    def reattach(self, run_id: str, snapshot: Mapping[str, object]) -> RunHandle:
        session_id = snapshot.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise RuntimeError("ACP runtime snapshot does not contain a session_id")
        return RunHandle(run_id, session_id)


class StdioAcpTransport:
    """Minimal JSON-RPC-over-stdio transport for ACP-style providers.

    Provider-specific session semantics remain in the server; this class only
    owns process lifecycle and request/event framing.
    """

    def __init__(self, command: tuple[str, ...], *, cwd: str | None = None) -> None:
        if not command:
            raise ValueError("ACP command cannot be empty")
        self._process = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        if self._process.stdin is None or self._process.stdout is None:
            raise RuntimeError("ACP process did not expose stdio")
        self._ids = itertools.count(1)

    def request(self, method: str, params: dict[str, object]) -> object:
        request_id = next(self._ids)
        self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        while True:
            message = self._read()
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise RuntimeError(f"ACP request failed: {message['error']}")
            return message.get("result", {})

    def events(self, session_id: str) -> Iterable[object]:
        while True:
            message = self._read()
            if message.get("method"):
                params = message.get("params", {})
                if isinstance(params, Mapping):
                    yield dict(params)
                else:
                    yield params
                continue
            result = message.get("result")
            if isinstance(result, Mapping) and result.get("session_id") == session_id:
                yield dict(result)

    def close(self) -> None:
        if self._process.poll() is None:
            self._process.terminate()
        self._process.wait()

    def _write(self, message: dict[str, object]) -> None:
        assert self._process.stdin is not None
        self._process.stdin.write(json.dumps(message) + "\n")
        self._process.stdin.flush()

    def _read(self) -> dict[str, object]:
        assert self._process.stdout is not None
        line = self._process.stdout.readline()
        if not line:
            raise RuntimeError("ACP process ended before sending a response")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise RuntimeError("ACP message must be a JSON object")
        return value


__all__ = ["AcpAgentRuntime", "AcpTransport", "StdioAcpTransport"]
