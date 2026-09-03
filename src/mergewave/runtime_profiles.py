"""Discoverable model-neutral runtime adapter registry."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .acp_runtime import AcpAgentRuntime, StdioAcpTransport
from .runtime import AgentRuntime, CliAgentRuntime, RuntimeCapabilities, WorkerProfile


@dataclass(frozen=True)
class RuntimeAdapterProfile:
    name: str
    transport: str
    command: tuple[str, ...]
    worker: WorkerProfile
    capabilities: RuntimeCapabilities
    prompt_transport: str = "stdin"


AcpProviderProfile = RuntimeAdapterProfile
ProfileFactory = Callable[..., RuntimeAdapterProfile]


class RuntimeAdapterRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, ProfileFactory] = {}

    def register(self, name: str, factory: ProfileFactory, *, replace: bool = False) -> None:
        normalized = name.strip().lower()
        if not normalized:
            raise ValueError("runtime adapter name cannot be empty")
        if normalized in self._factories and not replace:
            raise ValueError(f"runtime adapter is already registered: {normalized}")
        self._factories[normalized] = factory

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))

    def profile(self, name: str, **options: object) -> RuntimeAdapterProfile:
        normalized = name.strip().lower()
        try:
            factory = self._factories[normalized]
        except KeyError as error:
            raise ValueError(f"unknown runtime adapter: {name}; available: {', '.join(self.names())}") from error
        return factory(name=normalized, **options)

    def create_runtime(
        self,
        profile: RuntimeAdapterProfile,
        *,
        cwd: str | None = None,
        timeout_seconds: float | None = None,
    ) -> AgentRuntime:
        if profile.transport == "acp":
            return AcpAgentRuntime(
                StdioAcpTransport(profile.command, cwd=cwd),
                capabilities=profile.capabilities,
            )
        if profile.transport == "cli":
            return CliAgentRuntime(
                profile.command,
                timeout_seconds=timeout_seconds,
                prompt_transport=profile.prompt_transport,
            )
        raise ValueError(f"unsupported runtime transport: {profile.transport}")


def _profile_factory(
    *,
    transport: str,
    default_command: tuple[str, ...],
    prompt_transport: str = "stdin",
) -> ProfileFactory:
    def build(
        *,
        name: str,
        command: tuple[str, ...] | None = None,
        model: str | None = None,
        permissions: str = "repo-write",
        sandbox: str = "restricted",
        max_cost: float | None = None,
    ) -> RuntimeAdapterProfile:
        selected_command = command or default_command
        if not selected_command:
            raise ValueError("runtime adapter command cannot be empty")
        capabilities = (
            RuntimeCapabilities(True, True, True, ("acp", "stdio"), True, True)
            if transport == "acp"
            else RuntimeCapabilities(False, True, True, ("cli", "subprocess"), False, True)
        )
        return RuntimeAdapterProfile(
            name=name,
            transport=transport,
            command=tuple(selected_command),
            worker=WorkerProfile(
                runtime=transport,
                agent=name,
                model=model,
                permissions=permissions,
                sandbox=sandbox,
                max_cost=max_cost,
            ),
            capabilities=capabilities,
            prompt_transport=prompt_transport,
        )

    return build


DEFAULT_RUNTIME_REGISTRY = RuntimeAdapterRegistry()
for _name, _command in {
    "codex": ("codex", "acp"),
    "claude-code": ("claude", "--acp"),
    "gemini": ("gemini", "--acp"),
    "openhands": ("openhands", "--acp"),
}.items():
    DEFAULT_RUNTIME_REGISTRY.register(_name, _profile_factory(transport="acp", default_command=_command))
DEFAULT_RUNTIME_REGISTRY.register(
    "aider",
    _profile_factory(
        transport="cli",
        default_command=("aider", "--yes-always", "--message"),
        prompt_transport="argument",
    ),
)


def provider_profile(name: str, **options: object) -> RuntimeAdapterProfile:
    """Compatibility entrypoint backed by the discoverable default registry."""
    return DEFAULT_RUNTIME_REGISTRY.profile(name, **options)


def runtime_for(
    profile: RuntimeAdapterProfile,
    *,
    cwd: str | None = None,
    timeout_seconds: float | None = None,
) -> AgentRuntime:
    return DEFAULT_RUNTIME_REGISTRY.create_runtime(
        profile,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
    )


def stdio_runtime(profile: RuntimeAdapterProfile, *, cwd: str | None = None) -> AcpAgentRuntime:
    """Compatibility helper for ACP-only callers."""
    if profile.transport != "acp":
        raise ValueError(f"profile {profile.name} does not use ACP")
    runtime = runtime_for(profile, cwd=cwd)
    assert isinstance(runtime, AcpAgentRuntime)
    return runtime


__all__ = [
    "AcpProviderProfile",
    "DEFAULT_RUNTIME_REGISTRY",
    "RuntimeAdapterProfile",
    "RuntimeAdapterRegistry",
    "provider_profile",
    "runtime_for",
    "stdio_runtime",
]
