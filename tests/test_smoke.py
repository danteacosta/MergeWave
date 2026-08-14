from __future__ import annotations

import unittest
from unittest.mock import patch

from mergewave.smoke import (
    SmokeConfigurationError,
    github_read_only_smoke,
    linear_read_only_smoke,
)


class FakeLinearTransport:
    def execute(self, query: str, variables: dict[str, object]) -> object:
        return {"data": {"team": {"issues": {"nodes": [{"identifier": "CTRL-1"}]}}}}


class FakeGitHubTransport:
    def request(self, method: str, path: str, body: object | None = None) -> object:
        if path.endswith("pulls?head=mergewave/CTRL-1&state=all"):
            return [{"number": 1, "head": {"sha": "head-1"}, "merged_at": None}]
        if path.endswith("commits/head-1/check-runs"):
            return {"check_runs": [{"head_sha": "head-1", "conclusion": "success"}]}
        if path.endswith("pulls/1/reviews"):
            return [{"user": {"login": "reviewer"}, "state": "APPROVED"}]
        if path.endswith("pulls/1/files"):
            return [{"filename": "src/mergewave/change.py"}]
        if path.endswith("compare/main-0...head-1"):
            return {"status": "ahead"}
        raise AssertionError(path)


class SmokeTests(unittest.TestCase):
    def test_linear_smoke_returns_normalized_read_only_summary(self) -> None:
        result = linear_read_only_smoke("key", "team-1", transport=FakeLinearTransport())

        self.assertEqual(result["provider"], "linear")
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["candidate_ids"], ["CTRL-1"])

    def test_github_smoke_returns_delivery_observation_summary(self) -> None:
        result = github_read_only_smoke(
            "token",
            "acme/demo",
            item_id="CTRL-1",
            branch_ref="mergewave/CTRL-1",
            base_revision="main-0",
            scope_paths=("src/mergewave/",),
            transport=FakeGitHubTransport(),
        )

        self.assertEqual(result["provider"], "github")
        self.assertEqual(result["pr_head_sha"], "head-1")
        self.assertTrue(result["ci_passed"])
        self.assertEqual(result["approvals"], 1)
        self.assertTrue(result["base_is_ancestor"])

    def test_smokes_reject_missing_configuration_before_network_access(self) -> None:
        with self.assertRaises(SmokeConfigurationError):
            linear_read_only_smoke("", "team-1", transport=FakeLinearTransport())
        with self.assertRaises(SmokeConfigurationError):
            github_read_only_smoke(
                "token", "", item_id="CTRL-1", branch_ref="branch", base_revision="main-0",
                scope_paths=(), transport=FakeGitHubTransport(),
            )

    def test_cli_reports_missing_linear_smoke_configuration(self) -> None:
        from mergewave.__main__ import main

        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(main(["--linear-smoke"]), 2)


if __name__ == "__main__":
    unittest.main()
