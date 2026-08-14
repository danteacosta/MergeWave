from __future__ import annotations

import unittest

from mergewave.runtime_profiles import provider_profile


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
        with self.assertRaisesRegex(ValueError, "unsupported ACP provider"):
            provider_profile("unknown")


if __name__ == "__main__":
    unittest.main()
