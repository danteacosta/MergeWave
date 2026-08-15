"""Opt-in writable acceptance against explicitly disposable provider resources."""

from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path
import subprocess
from uuid import uuid4

from .git_workspace import GitWorkspaceFactory, Workspace
from .github_adapter import GitHubDeliveryObserver, GitHubTransport, UrllibGitHubTransport
from .linear_adapter import LinearGraphQLAdapter, LinearGraphQLTransport, UrllibLinearTransport

_WRITE_SENTINEL = "DISPOSABLE_RESOURCES_ONLY"


class AcceptanceConfigurationError(ValueError):
    """Writable acceptance configuration is missing or unsafe."""


@dataclass(frozen=True)
class WritableAcceptanceConfig:
    repository_path: Path
    workspace_root: Path
    base_revision: str
    github_token: str
    github_repository: str
    github_branch: str
    github_scope_paths: tuple[str, ...]
    linear_token: str
    linear_team_id: str
    linear_issue_id: str
    linear_restore_state: str
    linear_in_progress_state: str = "In Progress"
    linear_in_review_state: str = "In Review"

    @classmethod
    def from_env(cls) -> "WritableAcceptanceConfig":
        if os.environ.get("MERGEWAVE_ACCEPT_WRITES") != _WRITE_SENTINEL:
            raise AcceptanceConfigurationError(
                "writable acceptance requires MERGEWAVE_ACCEPT_WRITES=" + _WRITE_SENTINEL
            )
        required = {
            "repository_path": "MERGEWAVE_ACCEPTANCE_REPOSITORY_PATH",
            "workspace_root": "MERGEWAVE_ACCEPTANCE_WORKSPACE_ROOT",
            "base_revision": "MERGEWAVE_GITHUB_BASE_REVISION",
            "github_token": "MERGEWAVE_GITHUB_TOKEN",
            "github_repository": "MERGEWAVE_GITHUB_REPOSITORY",
            "github_branch": "MERGEWAVE_GITHUB_BRANCH",
            "linear_token": "MERGEWAVE_LINEAR_API_KEY",
            "linear_team_id": "MERGEWAVE_LINEAR_TEAM_ID",
            "linear_issue_id": "MERGEWAVE_LINEAR_ISSUE_ID",
            "linear_restore_state": "MERGEWAVE_LINEAR_RESTORE_STATE",
        }
        values = {field: os.environ.get(variable, "").strip() for field, variable in required.items()}
        missing = [variable for field, variable in required.items() if not values[field]]
        scope_paths = tuple(
            value.strip()
            for value in os.environ.get("MERGEWAVE_GITHUB_SCOPE_PATHS", "").split(",")
            if value.strip()
        )
        if missing or not scope_paths:
            detail = ", ".join(missing + ([] if scope_paths else ["MERGEWAVE_GITHUB_SCOPE_PATHS"]))
            raise AcceptanceConfigurationError("writable acceptance is missing: " + detail)
        return cls(
            repository_path=Path(values["repository_path"]),
            workspace_root=Path(values["workspace_root"]),
            base_revision=values["base_revision"],
            github_token=values["github_token"],
            github_repository=values["github_repository"],
            github_branch=values["github_branch"],
            github_scope_paths=scope_paths,
            linear_token=values["linear_token"],
            linear_team_id=values["linear_team_id"],
            linear_issue_id=values["linear_issue_id"],
            linear_restore_state=values["linear_restore_state"],
            linear_in_progress_state=os.environ.get(
                "MERGEWAVE_LINEAR_IN_PROGRESS_STATE", "In Progress"
            ),
            linear_in_review_state=os.environ.get(
                "MERGEWAVE_LINEAR_IN_REVIEW_STATE", "In Review"
            ),
        )


def run_writable_acceptance(
    config: WritableAcceptanceConfig,
    *,
    github_transport: GitHubTransport | None = None,
    linear_transport: LinearGraphQLTransport | None = None,
) -> dict[str, object]:
    """Exercise writes only after explicit disposable-resource configuration."""

    workspace_factory = GitWorkspaceFactory(config.repository_path, config.workspace_root)
    workspace = workspace_factory.create(
        f"acceptance-{uuid4().hex[:12]}", config.base_revision
    )
    tracker = LinearGraphQLAdapter(
        linear_transport or UrllibLinearTransport(config.linear_token),
        team_id=config.linear_team_id,
    )
    observer = GitHubDeliveryObserver(
        repository=config.github_repository,
        transport=github_transport or UrllibGitHubTransport(config.github_token),
        scope_paths={config.linear_issue_id: config.github_scope_paths},
    )
    state_changed = False
    try:
        inspected = workspace_factory.inspect(workspace)
        observation_workspace = replace(inspected, branch_ref=config.github_branch)
        observation = observer.observe(config.linear_issue_id, observation_workspace)
        if not observation.pr_id or not observation.pr_url or not observation.pr_head_sha:
            raise RuntimeError("writable acceptance requires an existing disposable pull request")
        if not observation.ci_head_sha:
            raise RuntimeError("writable acceptance did not observe a check run for the PR head")
        tracker.transition_state(config.linear_issue_id, config.linear_in_progress_state)
        state_changed = True
        tracker.link_pull_request(config.linear_issue_id, observation.pr_url)
        tracker.post_comment(
            config.linear_issue_id,
            "MergeWave writable acceptance: observed PR, checks, reviews, scope, and ancestry.",
        )
        tracker.transition_state(config.linear_issue_id, config.linear_in_review_state)
        if not tracker.pull_request_linked(config.linear_issue_id, observation.pr_url):
            raise RuntimeError("Linear did not expose the newly linked pull request")
        return {
            "status": "passed",
            "git": {
                "base_revision": inspected.base_revision,
                "initial_head_revision": inspected.initial_head_revision,
                "current_head_revision": inspected.current_head_revision,
            },
            "github": {
                "repository": config.github_repository,
                "pr_id": observation.pr_id,
                "pr_head_sha": observation.pr_head_sha,
                "ci_head_sha": observation.ci_head_sha,
                "ci_passed": observation.ci_passed,
                "approvals": observation.approvals,
                "reviews_resolved": observation.reviews_resolved,
                "scope_ok": observation.scope_ok,
                "base_is_ancestor": observation.base_is_ancestor,
            },
            "linear": {
                "issue_id": config.linear_issue_id,
                "states_exercised": [
                    config.linear_in_progress_state,
                    config.linear_in_review_state,
                    config.linear_restore_state,
                ],
                "pr_linked": True,
            },
        }
    finally:
        try:
            if state_changed:
                tracker.transition_state(config.linear_issue_id, config.linear_restore_state)
        finally:
            _destroy_acceptance_workspace(workspace_factory, workspace, config.repository_path)


def _destroy_acceptance_workspace(
    factory: GitWorkspaceFactory,
    workspace: Workspace,
    repository_path: Path,
) -> None:
    factory.destroy(workspace)
    subprocess.run(
        ["git", "branch", "-D", workspace.branch_ref],
        cwd=repository_path,
        check=True,
        capture_output=True,
        text=True,
    )


__all__ = [
    "AcceptanceConfigurationError",
    "WritableAcceptanceConfig",
    "run_writable_acceptance",
]
