from __future__ import annotations

import unittest

from mergewave.github_adapter import GitHubDeliveryObserver
from mergewave.git_workspace import Workspace


class FakeGitHubTransport:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.requests: list[str] = []

    def request(self, method: str, path: str, body: object | None = None) -> object:
        self.requests.append(f"{method} {path}")
        return self.responses[f"{method} {path}"]


class GitHubDeliveryObserverTests(unittest.TestCase):
    def test_observer_normalizes_pr_ci_review_scope_merge_and_ancestry(self) -> None:
        repository = "acme/demo"
        branch = "mergewave/CTRL-1"
        transport = FakeGitHubTransport(
            {
                f"GET /repos/{repository}/pulls?head={branch}&state=all": [
                    {
                        "number": 7,
                        "head": {"sha": "head-1", "ref": branch},
                        "merged_at": "2026-08-14T20:00:00Z",
                        "merge_commit_sha": "main-1",
                    }
                ],
                f"GET /repos/{repository}/commits/head-1/check-runs": {
                    "check_runs": [{"head_sha": "head-1", "conclusion": "success"}]
                },
                f"GET /repos/{repository}/pulls/7/reviews": [
                    {"user": {"login": "reviewer"}, "state": "APPROVED"}
                ],
                f"GET /repos/{repository}/pulls/7/files": [
                    {"filename": "src/mergewave/change.py"}
                ],
                f"GET /repos/{repository}/compare/main-0...head-1": {"status": "ahead"},
            }
        )
        workspace = Workspace(
            workspace_id="CTRL-1",
            repository="demo-repository",
            worktree_path="/worktrees/CTRL-1",
            branch_ref=branch,
            base_revision="main-0",
            initial_head_revision="main-0",
            current_head_revision="head-1",
        )
        observer = GitHubDeliveryObserver(
            repository=repository,
            transport=transport,
            scope_paths={"CTRL-1": ("src/mergewave/",)},
        )

        observation = observer.observe("CTRL-1", workspace)

        self.assertEqual(observation.pr_head_sha, "head-1")
        self.assertEqual(observation.ci_head_sha, "head-1")
        self.assertTrue(observation.ci_passed)
        self.assertEqual(observation.approvals, 1)
        self.assertTrue(observation.scope_ok)
        self.assertTrue(observation.merged)
        self.assertEqual(observation.merge_revision, "main-1")
        self.assertTrue(observation.base_is_ancestor)

    def test_missing_pull_request_is_explicitly_unmerged(self) -> None:
        repository = "acme/demo"
        branch = "mergewave/CTRL-2"
        transport = FakeGitHubTransport(
            {f"GET /repos/{repository}/pulls?head={branch}&state=all": []}
        )
        workspace = Workspace(
            workspace_id="CTRL-2",
            repository="demo-repository",
            worktree_path="/worktrees/CTRL-2",
            branch_ref=branch,
            base_revision="main-0",
            initial_head_revision="main-0",
            current_head_revision="main-0",
        )
        observer = GitHubDeliveryObserver(
            repository=repository,
            transport=transport,
            scope_paths={"CTRL-2": ("src/",)},
        )

        observation = observer.observe("CTRL-2", workspace)

        self.assertEqual(observation.pr_head_sha, "")
        self.assertFalse(observation.merged)
        self.assertFalse(observation.ci_passed)


if __name__ == "__main__":
    unittest.main()
