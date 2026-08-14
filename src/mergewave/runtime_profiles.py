"""Provider launch profiles without importing provider SDKs or model clients."""

from __future__ import annotations

from dataclasses import dataclass

from .acp_runtime import AcpAgentRuntime, StdioAcpTransport
from .runtime import RuntimeCapabilities, WorkerProfile


@dataclass(frozen=True)
class AcpProviderProfile:
    name: str
    command: tuple[str, ...]
    worker: WorkerProfile
    capabilities: RuntimeCapabilities


_DEFAULT_COMMANDS: dict[str, tuple[str, ...]] = {
    "codex": ("codex", "acp"),
    "claude-code": ("claude", "--acp"),
    "gemini": ("gemini", "--acp"),
    "openhands": ("openhands", "--acp"),
}


def provider_profile(
    name: str,
    *,
    command: tuple[str, ...] | None = None,
    model: str | None = None,
    permissions: str = "repo-write",
    sandbox: str = "restricted",
    max_cost: float | None = None,
) -> AcpProviderProfile:
    """Describe a provider-specific ACP executable while keeping the core neutral.

    Commands are overridable because installed CLI names and ACP flags vary by
    version. Credentials are intentionally resolved by the child process.
    """
    normalized = name.strip().lower()
    if normalized not in _DEFAULT_COMMANDS:
        raise ValueError(f"unsupported ACP provider profile: {name}")
    selected_command = command or _DEFAULT_COMMANDS[normalized]
    if not selected_command:
        raise ValueError("ACP provider command cannot be empty")
    return AcpProviderProfile(
        name=normalized,
        command=tuple(selected_command),
        worker=WorkerProfile(
            runtime="acp",
            agent=normalized,
            model=model,
            permissions=permissions,
            sandbox=sandbox,
            max_cost=max_cost,
        ),
        capabilities=RuntimeCapabilities(True, True, True, ("acp", "stdio")),
    )


def stdio_runtime(profile: AcpProviderProfile, *, cwd: str | None = None) -> AcpAgentRuntime:
    """Create the generic ACP runtime for a concrete provider profile."""
    return AcpAgentRuntime(
        StdioAcpTransport(profile.command, cwd=cwd),
        capabilities=profile.capabilities,
    )


__all__ = ["AcpProviderProfile", "provider_profile", "stdio_runtime"]
