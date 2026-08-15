from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from mergewave.live_acceptance import (
    AcceptanceConfigurationError,
    WritableAcceptanceConfig,
    run_writable_acceptance,
)


class FakeGitHubTransport:
    def request(self, method: str, path: str, body: object | None = None) -> object:
        if path.endswith("pulls?head=agent/test&state=all"):
            return [
                {
                    "number": 17,
                    "head": {"sha": "head-17"},
                    "base": {"sha": "base-17"},
                    "html_url": "https://github.test/acme/repo/pull/17",
                    "merged_at": None,
                    "merge_commit_sha": None,
                }
            ]
        if path.endswith("commits/head-17/check-runs"):
            return {
                "check_runs": [
                    {
                        "head_sha": "head-17",
                        "status": "completed",
                        "conclusion": "success",
                    }
                ]
            }
        if path.endswith("pulls/17/reviews"):
            return [{"user": {"login": "reviewer"}, "state": "APPROVED"}]
        if path.endswith("pulls/17/files"):
            return [{"filename": "src/mergewave/change.py"}]
        if "/compare/" in path:
            return {"status": "ahead"}
        raise AssertionError(path)


class FakeLinearTransport:
    def __init__(self) -> None:
        self.transitions: list[str] = []

    def execute(self, query: str, variables: dict[str, object]) -> object:
        if "MergeWaveTeamWorkflowStates" in query:
            return {
                "data": {
                    "team": {
                        "states": {
                            "nodes": [
                                {"id": "progress", "name": "In Progress", "type": "started"},
                                {"id": "review", "name": "In Review", "type": "started"},
                                {"id": "todo", "name": "Todo", "type": "unstarted"},
                            ]
                        }
                    }
                }
            }
        if "MergeWaveIssueUpdate" in query:
            self.transitions.append(str(variables["state_id"]))
            return {"data": {"issueUpdate": {"success": True}}}
        if "attachmentCreate" in query:
            return {"data": {"attachmentCreate": {"success": True}}}
        if "MergeWaveComment" in query:
            return {"data": {"commentCreate": {"success": True}}}
        if "MergeWaveAttachment" in query:
            return {
                "data": {
                    "attachmentsForURL": {
                        "nodes": [{"issue": {"id": "linear-1", "identifier": "CTRL-1"}}]
                    }
                }
            }
        raise AssertionError(query)


class WritableAcceptanceTests(unittest.TestCase):
    def test_configuration_fails_before_writes_without_exact_sentinel(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(AcceptanceConfigurationError, "DISPOSABLE_RESOURCES_ONLY"):
                WritableAcceptanceConfig.from_env()

    def test_acceptance_exercises_real_worktree_and_provider_write_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = root / "repository"
            repository.mkdir()
            subprocess.run(["git", "init"], cwd=repository, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "acceptance@example.test"],
                cwd=repository,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "MergeWave Acceptance"],
                cwd=repository,
                check=True,
            )
            (repository / "README.md").write_text("acceptance\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repository, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=repository, check=True, capture_output=True)
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repository,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            linear = FakeLinearTransport()
            result = run_writable_acceptance(
                WritableAcceptanceConfig(
                    repository_path=repository,
                    workspace_root=root / "workspaces",
                    base_revision=base,
                    github_token="secret-not-returned",
                    github_repository="acme/repo",
                    github_branch="agent/test",
                    github_scope_paths=("src/mergewave/",),
                    linear_token="secret-not-returned",
                    linear_team_id="team-1",
                    linear_issue_id="CTRL-1",
                    linear_restore_state="Todo",
                ),
                github_transport=FakeGitHubTransport(),
                linear_transport=linear,
            )

            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["git"]["initial_head_revision"], base)
            self.assertEqual(result["github"]["pr_id"], "17")
            self.assertEqual(result["linear"]["states_exercised"], ["In Progress", "In Review", "Todo"])
            self.assertEqual(linear.transitions, ["progress", "review", "todo"])
            branches = subprocess.run(
                ["git", "branch", "--list", "mergewave/acceptance-*"],
                cwd=repository,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            self.assertEqual(branches.strip(), "")


if __name__ == "__main__":
    unittest.main()
