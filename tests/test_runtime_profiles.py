from __future__ import annotations

import unittest
import sys

from mergewave.runtime import RunSpec
from mergewave.runtime_profiles import (
    DEFAULT_RUNTIME_REGISTRY,
    RuntimeAdapterProfile,
    RuntimeAdapterRegistry,
    provider_profile,
    runtime_for,
)


class RuntimeProfileTests(unittest.TestCase):
    def test_supported_providers_have_model_neutral_worker_contracts(self) -> None:
        for name in ("codex", "claude-code", "gemini", "openhands"):
            profile = provider_profile(name, model="test-model")
            self.assertEqual(profile.name, name)
            self.assertTrue(profile.command)
            self.assertEqual(profile.worker.agent, name)
            self.assertEqual(profile.worker.model, "test-model")
            self.assertEqual(profile.capabilities.transports, ("acp", "stdio"))

    def test_command_and_sandbox_are_explicitly_overridable(self) -> None:
        profile = provider_profile("codex", command=("fake-codex", "acp"), sandbox="container", max_cost=2.5)

        self.assertEqual(profile.command, ("fake-codex", "acp"))
        self.assertEqual(profile.worker.sandbox, "container")
        self.assertEqual(profile.worker.max_cost, 2.5)

    def test_unknown_provider_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown runtime adapter"):
            provider_profile("unknown")

    def test_registry_is_discoverable_and_accepts_third_party_adapters(self) -> None:
        registry = RuntimeAdapterRegistry()
        registry.register(
            "custom",
            lambda **_: RuntimeAdapterProfile(
                "custom",
                "cli",
                ("custom-agent",),
                provider_profile("aider").worker,
                provider_profile("aider").capabilities,
            ),
        )

        self.assertEqual(registry.names(), ("custom",))
        self.assertEqual(registry.profile("CUSTOM").name, "custom")
        with self.assertRaisesRegex(ValueError, "already registered"):
            registry.register("custom", lambda **_: registry.profile("custom"))

    def test_aider_profile_uses_the_generic_cli_argument_contract(self) -> None:
        profile = provider_profile(
            "aider",
            command=(sys.executable, "-c", "import sys; print(sys.argv[-1])"),
        )
        runtime = runtime_for(profile)

        events = tuple(runtime.stream(runtime.start(RunSpec("run-1", "CTRL-1", "Implement ticket", "."))))

        self.assertIn("aider", DEFAULT_RUNTIME_REGISTRY.names())
        self.assertEqual(profile.transport, "cli")
        self.assertEqual(events[0].payload["line"], "Implement ticket")
        self.assertEqual(events[-1].payload["returncode"], 0)


if __name__ == "__main__":
    unittest.main()
