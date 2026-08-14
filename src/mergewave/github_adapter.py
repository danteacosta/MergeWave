"""GitHub pull-request, CI, review, scope, and merge observer."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from typing import Protocol
from urllib.request import Request, urlopen

from .git_workspace import Workspace
from .simulator import DeliveryObservation


class GitHubTransport(Protocol):
    def request(self, method: str, path: str, body: object | None = None) -> object:
        ...


class UrllibGitHubTransport:
    def __init__(self, token: str, *, api_url: str = "https://api.github.com") -> None:
        self._token = token
        self._api_url = api_url.rstrip("/")

    def request(self, method: str, path: str, body: object | None = None) -> object:
        request = Request(
            f"{self._api_url}{path}",
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))


class GitHubDeliveryObserver:
    def __init__(
        self,
        *,
        repository: str,
        transport: GitHubTransport,
        scope_paths: Mapping[str, Sequence[str]],
        required_approvals: int = 1,
        required_reviewers: Sequence[str] = (),
    ) -> None:
        if required_approvals < 1:
            raise ValueError("required_approvals must be positive")
        self._repository = repository
        self._transport = transport
        self._scope_paths = scope_paths
        self._required_approvals = required_approvals
        self._required_reviewers = frozenset(required_reviewers)

    def observe(self, item_id: str, workspace: Workspace) -> DeliveryObservation:
        pull_requests = self._get(
            f"/repos/{self._repository}/pulls?head={workspace.branch_ref}&state=all"
        )
        if not isinstance(pull_requests, list) or not pull_requests:
            return DeliveryObservation(
                repository=workspace.repository,
                worktree_path=workspace.worktree_path,
                branch_ref=workspace.branch_ref,
                base_revision=workspace.base_revision,
                initial_head_revision=workspace.initial_head_revision,
                current_head_revision=workspace.current_head_revision,
                pr_head_sha="",
                ci_head_sha="",
                ci_passed=False,
                approvals=0,
                scope_ok=True,
                merged=False,
                merge_revision=None,
                base_is_ancestor=False,
            )

        pull_request = pull_requests[0]
        number = int(pull_request["number"])
        head_sha = str(pull_request["head"]["sha"])
        checks = self._get(f"/repos/{self._repository}/commits/{head_sha}/check-runs")
        check_runs = checks.get("check_runs", []) if isinstance(checks, dict) else []
        ci_head_sha = str(check_runs[0].get("head_sha", "")) if check_runs else ""
        ci_pending = not check_runs or any(
            (run.get("status") is not None and run.get("status") != "completed")
            or run.get("conclusion") is None
            for run in check_runs
        )
        ci_passed = bool(check_runs) and all(
            run.get("conclusion") == "success" and run.get("status", "completed") == "completed"
            for run in check_runs
        )
        reviews = self._get(f"/repos/{self._repository}/pulls/{number}/reviews")
        latest_review_by_reviewer: dict[str, str] = {}
        if isinstance(reviews, list):
            for review in reviews:
                if not isinstance(review, dict):
                    continue
                reviewer = review.get("user", {}).get("login")
                state = review.get("state")
                if isinstance(reviewer, str) and isinstance(state, str):
                    latest_review_by_reviewer[reviewer] = state
        approved_reviewers = {
            reviewer for reviewer, state in latest_review_by_reviewer.items() if state == "APPROVED"
        }
        changes_requested = any(state == "CHANGES_REQUESTED" for state in latest_review_by_reviewer.values())
        reviews_resolved = not changes_requested
        required_reviewers_satisfied = self._required_reviewers.issubset(approved_reviewers)
        files = self._get(f"/repos/{self._repository}/pulls/{number}/files")
        declared_paths = tuple(self._scope_paths.get(item_id, ()))
        scope_ok = isinstance(files, list) and all(
            any(str(file.get("filename", "")).startswith(path) for path in declared_paths)
            for file in files
        )
        comparison = self._get(
            f"/repos/{self._repository}/compare/{workspace.base_revision}...{head_sha}"
        )
        status = comparison.get("status") if isinstance(comparison, dict) else None
        return DeliveryObservation(
            repository=workspace.repository,
            worktree_path=workspace.worktree_path,
            branch_ref=workspace.branch_ref,
            base_revision=workspace.base_revision,
            initial_head_revision=workspace.initial_head_revision,
            current_head_revision=workspace.current_head_revision,
            pr_head_sha=head_sha,
            ci_head_sha=ci_head_sha,
            ci_passed=ci_passed,
            approvals=len(approved_reviewers),
            scope_ok=scope_ok,
            merged=pull_request.get("merged_at") is not None,
            merge_revision=pull_request.get("merge_commit_sha"),
            base_is_ancestor=status in {"ahead", "identical"},
            pr_id=str(pull_request.get("number", "")),
            pr_url=str(pull_request.get("html_url", pull_request.get("url", ""))),
            base_sha_at_open=str(pull_request.get("base", {}).get("sha", "")),
            reviews_resolved=reviews_resolved,
            merged_by=(pull_request.get("merged_by", {}) or {}).get("login"),
            approval_reviewers=tuple(sorted(approved_reviewers)),
            changes_requested=changes_requested,
            required_reviewers_satisfied=required_reviewers_satisfied,
            ci_pending=ci_pending,
        )

    def _get(self, path: str) -> object:
        return self._transport.request("GET", path)
