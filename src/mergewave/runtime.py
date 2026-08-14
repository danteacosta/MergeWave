"""Model-agnostic agent runtime ports and the generic CLI fallback."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable, Sequence
import subprocess
from typing import Protocol, cast


@dataclass(frozen=True)
class RunSpec:
    run_id: str
    work_item_id: str
    prompt: str
    workspace_path: str


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

    def cancel(self, handle: RunHandle) -> AgentEvent: ...


class CliAgentRuntime:
    """Execute any compatible agent command inside an assigned workspace."""

    def __init__(self, command: Sequence[str]) -> None:
        if not command:
            raise ValueError("CLI runtime command cannot be empty")
        self._command = tuple(command)

    def start(self, spec: RunSpec) -> RunHandle:
        process = subprocess.Popen(
            self._command,
            cwd=spec.workspace_path,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        if process.stdin is None:
            raise RuntimeError("CLI runtime did not expose stdin")
        process.stdin.write(spec.prompt)
        process.stdin.close()
        return RunHandle(spec.run_id, process)

    def stream(self, handle: RunHandle) -> Iterable[AgentEvent]:
        process = cast(subprocess.Popen[str], handle.runtime_ref)
        if process.stdout is None:
            raise RuntimeError("CLI runtime did not expose stdout")
        try:
            for line in process.stdout:
                yield AgentEvent("runtime.output", {"line": line.rstrip("\n")})
        finally:
            process.stdout.close()
        returncode = process.wait()
        yield AgentEvent("runtime.exited", {"returncode": returncode})

    def cancel(self, handle: RunHandle) -> AgentEvent:
        process = cast(subprocess.Popen[str], handle.runtime_ref)
        if process.poll() is None:
            process.terminate()
            process.wait()
        return AgentEvent("runtime.cancelled", {"returncode": process.returncode})
