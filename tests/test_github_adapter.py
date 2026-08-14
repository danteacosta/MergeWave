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
                        "html_url": "https://github.com/acme/demo/pull/7",
                        "base": {"sha": "main-0"},
                        "merged_at": "2026-08-14T20:00:00Z",
                        "merge_commit_sha": "main-1",
                        "merged_by": {"login": "maintainer"},
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
        self.assertEqual(observation.pr_id, "7")
        self.assertEqual(observation.pr_url, "https://github.com/acme/demo/pull/7")
        self.assertEqual(observation.base_sha_at_open, "main-0")
        self.assertTrue(observation.reviews_resolved)
        self.assertEqual(observation.approval_reviewers, ("reviewer",))
        self.assertFalse(observation.changes_requested)
        self.assertTrue(observation.required_reviewers_satisfied)
        self.assertEqual(observation.merged_by, "maintainer")

    def test_requested_changes_are_not_resolved_reviews(self) -> None:
        repository = "acme/demo"
        branch = "mergewave/CTRL-3"
        transport = FakeGitHubTransport(
            {
                f"GET /repos/{repository}/pulls?head={branch}&state=all": [
                    {"number": 8, "head": {"sha": "head-8"}, "merged_at": None}
                ],
                f"GET /repos/{repository}/commits/head-8/check-runs": {
                    "check_runs": [{"head_sha": "head-8", "conclusion": "success"}]
                },
                f"GET /repos/{repository}/pulls/8/reviews": [
                    {"user": {"login": "reviewer"}, "state": "APPROVED"},
                    {"user": {"login": "reviewer"}, "state": "CHANGES_REQUESTED"},
                ],
                f"GET /repos/{repository}/pulls/8/files": [],
                f"GET /repos/{repository}/compare/main-0...head-8": {"status": "ahead"},
            }
        )
        workspace = Workspace(
            workspace_id="CTRL-3", repository="demo-repository", worktree_path="/worktrees/CTRL-3",
            branch_ref=branch, base_revision="main-0", initial_head_revision="main-0", current_head_revision="head-8",
        )

        observation = GitHubDeliveryObserver(
            repository=repository, transport=transport, scope_paths={"CTRL-3": ()}
        ).observe("CTRL-3", workspace)

        self.assertEqual(observation.approvals, 0)
        self.assertFalse(observation.reviews_resolved)

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
