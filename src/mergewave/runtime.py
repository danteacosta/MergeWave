"""Model-agnostic agent runtime ports and the generic CLI fallback."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable, Mapping, Sequence
import json
import os
import subprocess
from typing import Protocol, cast

from .contracts import WorkItem
from .git_workspace import Workspace
from .skills import SkillInvocation


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
    supports_authority: bool = False


@dataclass(frozen=True)
class RunSpec:
    run_id: str
    work_item_id: str
    prompt: str
    workspace_path: str
    work_item: WorkItem | None = None
    workspace: Workspace | None = None
    worker_profile: WorkerProfile | None = None
    skill: SkillInvocation | None = None


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
        environment = os.environ.copy()
        environment.update(
            {
                "MERGEWAVE_RUN_ID": spec.run_id,
                "MERGEWAVE_WORK_ITEM_ID": spec.work_item_id,
                "MERGEWAVE_WORKSPACE_PATH": spec.workspace_path,
            }
        )
        if spec.workspace is not None:
            environment["MERGEWAVE_WORKSPACE_ID"] = spec.workspace.workspace_id
        if spec.skill is not None:
            environment["MERGEWAVE_SKILL_INVOCATION"] = json.dumps(
                spec.skill.to_payload(), sort_keys=True, separators=(",", ":")
            )
            if spec.skill.authority is not None:
                environment["MERGEWAVE_SKILL_AUTHORITY"] = json.dumps(
                    spec.skill.authority.to_payload(), sort_keys=True, separators=(",", ":")
                )
        process = subprocess.Popen(
            command,
            cwd=spec.workspace_path,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=environment,
        )
        if process.stdin is None:
            raise RuntimeError("CLI runtime did not expose stdin")
        if self._prompt_transport == "stdin":
            process.stdin.write(spec.prompt)
        process.stdin.close()
        return RunHandle(spec.run_id, _CliSession(process, spec))

    def stream(self, handle: RunHandle) -> Iterable[AgentEvent]:
        session = cast(_CliSession, handle.runtime_ref)
        process = session.process
        if process.stdout is None:
            raise RuntimeError("CLI runtime did not expose stdout")
        if self._timeout_seconds is not None:
            yield from self._stream_with_timeout(process)
            return
        try:
            for line in process.stdout:
                yield self._parse_line(line)
        finally:
            process.stdout.close()
        returncode = process.wait()
        yield AgentEvent("runtime.exited", {"returncode": returncode})

    def continue_run(self, handle: RunHandle, input: str) -> None:
        raise RuntimeError("CLI runtime does not support continue after stdin is closed")

    def cancel(self, handle: RunHandle) -> AgentEvent:
        session = cast(_CliSession, handle.runtime_ref)
        process = session.process
        if process.poll() is None:
            process.terminate()
            process.wait()
        return AgentEvent("runtime.cancelled", {"returncode": process.returncode})

    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(False, True, True, ("subprocess",), False, True)

    def snapshot(self, handle: RunHandle) -> Mapping[str, object]:
        session = cast(_CliSession, handle.runtime_ref)
        process = session.process
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
            yield self._parse_line(line)
        yield AgentEvent("runtime.exited", {"returncode": process.returncode})

    @staticmethod
    def _parse_line(line: str) -> AgentEvent:
        text = line.rstrip("\n")
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return AgentEvent("runtime.output", {"line": text})
        if not isinstance(value, Mapping) or not isinstance(value.get("type"), str):
            return AgentEvent("runtime.output", {"line": text})
        raw_payload = value.get("payload")
        if isinstance(raw_payload, Mapping):
            payload = {str(key): item for key, item in raw_payload.items()}
        else:
            payload = {str(key): item for key, item in value.items() if key != "type"}
        return AgentEvent(value["type"], payload)


@dataclass(frozen=True)
class _CliSession:
    process: subprocess.Popen[str]
    spec: RunSpec


def classify_runtime_event(event: AgentEvent) -> str | None:
    if event.kind == "runtime.timeout":
        return "agent_timeout"
    if event.kind == "authority.violation":
        return "authority_violation"
    if event.kind == "runtime.exited" and event.payload.get("returncode") not in {0, None}:
        return "runtime_failed"
    return None
