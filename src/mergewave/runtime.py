"""Model-agnostic agent runtime ports and the generic CLI fallback."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable, Mapping, Sequence
import subprocess
from typing import Protocol, cast

from .contracts import WorkItem
from .git_workspace import Workspace


@dataclass(frozen=True)
class WorkerProfile:
    runtime: str
    agent: str
    model: str | None = None
    permissions: str = "repo-write"
    sandbox: str = "restricted"
    max_cost: float | None = None


@dataclass(frozen=True)
class RuntimeCapabilities:
    supports_continue: bool
    supports_streaming: bool
    supports_cancel: bool
    transports: tuple[str, ...] = ()
    supports_reattach: bool = False


@dataclass(frozen=True)
class RunSpec:
    run_id: str
    work_item_id: str
    prompt: str
    workspace_path: str
    work_item: WorkItem | None = None
    workspace: Workspace | None = None
    worker_profile: WorkerProfile | None = None


@dataclass(frozen=True)
class RunHandle:
    run_id: str
    runtime_ref: object


@dataclass(frozen=True)
class AgentEvent:
    kind: str
    payload: dict[str, object]


class AgentRuntime(Protocol):
    def start(self, spec: RunSpec) -> RunHandle: ...

    def stream(self, handle: RunHandle) -> Iterable[AgentEvent]: ...

    def continue_run(self, handle: RunHandle, input: str) -> None: ...

    def cancel(self, handle: RunHandle) -> AgentEvent: ...

    def capabilities(self) -> RuntimeCapabilities: ...

    def snapshot(self, handle: RunHandle) -> Mapping[str, object]: ...

    def reattach(self, run_id: str, snapshot: Mapping[str, object]) -> RunHandle: ...


class CliAgentRuntime:
    """Execute any compatible agent command inside an assigned workspace."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        timeout_seconds: float | None = None,
        prompt_transport: str = "stdin",
    ) -> None:
        if not command:
            raise ValueError("CLI runtime command cannot be empty")
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if prompt_transport not in {"stdin", "argument"}:
            raise ValueError("prompt_transport must be stdin or argument")
        self._command = tuple(command)
        self._timeout_seconds = timeout_seconds
        self._prompt_transport = prompt_transport

    def start(self, spec: RunSpec) -> RunHandle:
        command = self._command + ((spec.prompt,) if self._prompt_transport == "argument" else ())
        process = subprocess.Popen(
            command,
            cwd=spec.workspace_path,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        if process.stdin is None:
            raise RuntimeError("CLI runtime did not expose stdin")
        if self._prompt_transport == "stdin":
            process.stdin.write(spec.prompt)
        process.stdin.close()
        return RunHandle(spec.run_id, process)

    def stream(self, handle: RunHandle) -> Iterable[AgentEvent]:
        process = cast(subprocess.Popen[str], handle.runtime_ref)
        if process.stdout is None:
            raise RuntimeError("CLI runtime did not expose stdout")
        if self._timeout_seconds is not None:
            yield from self._stream_with_timeout(process)
            return
        try:
            for line in process.stdout:
                yield AgentEvent("runtime.output", {"line": line.rstrip("\n")})
        finally:
            process.stdout.close()
        returncode = process.wait()
        yield AgentEvent("runtime.exited", {"returncode": returncode})

    def continue_run(self, handle: RunHandle, input: str) -> None:
        raise RuntimeError("CLI runtime does not support continue after stdin is closed")

    def cancel(self, handle: RunHandle) -> AgentEvent:
        process = cast(subprocess.Popen[str], handle.runtime_ref)
        if process.poll() is None:
            process.terminate()
            process.wait()
        return AgentEvent("runtime.cancelled", {"returncode": process.returncode})

    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(False, True, True, ("subprocess",))

    def snapshot(self, handle: RunHandle) -> Mapping[str, object]:
        process = cast(subprocess.Popen[str], handle.runtime_ref)
        return {"pid": process.pid, "reattachable": False}

    def _stream_with_timeout(self, process: subprocess.Popen[str]) -> Iterable[AgentEvent]:
        timed_out = False
        try:
            process.wait(timeout=self._timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.terminate()
            process.wait()
        output = process.stdout.read() if process.stdout is not None else ""
        if process.stdout is not None:
            process.stdout.close()
        if timed_out:
            yield AgentEvent("runtime.timeout", {"timeout_seconds": self._timeout_seconds})
        for line in output.splitlines():
            yield AgentEvent("runtime.output", {"line": line})
        yield AgentEvent("runtime.exited", {"returncode": process.returncode})


def classify_runtime_event(event: AgentEvent) -> str | None:
    if event.kind == "runtime.timeout":
        return "agent_timeout"
    if event.kind == "runtime.exited" and event.payload.get("returncode") not in {0, None}:
        return "runtime_failed"
    return None
