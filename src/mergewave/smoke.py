"""Opt-in, read-only provider smoke checks."""

from __future__ import annotations

from collections.abc import Sequence

from .github_adapter import GitHubDeliveryObserver, GitHubTransport, UrllibGitHubTransport
from .git_workspace import Workspace
from .linear_adapter import LinearGraphQLAdapter, LinearGraphQLTransport, UrllibLinearTransport


class SmokeConfigurationError(ValueError):
    """Required smoke-test configuration is absent or invalid."""


def linear_read_only_smoke(
    token: str,
    team_id: str,
    *,
    transport: LinearGraphQLTransport | None = None,
) -> dict[str, object]:
    if not token.strip() or not team_id.strip():
        raise SmokeConfigurationError("Linear smoke requires a token and team id")
    adapter = LinearGraphQLAdapter(
        transport or UrllibLinearTransport(token),
        team_id=team_id,
    )
    candidates = adapter.fetch_candidates()
    return {
        "provider": "linear",
        "status": "ok",
        "team_id": team_id,
        "candidate_count": len(candidates),
        "candidate_ids": [str(candidate.get("id", "")) for candidate in candidates],
    }


def github_read_only_smoke(
    token: str,
    repository: str,
    *,
    item_id: str,
    branch_ref: str,
    base_revision: str,
    scope_paths: Sequence[str],
    transport: GitHubTransport | None = None,
) -> dict[str, object]:
    if not token.strip() or not repository.strip() or not item_id.strip():
        raise SmokeConfigurationError("GitHub smoke requires a token, repository, and item id")
    if not branch_ref.strip() or not base_revision.strip() or not tuple(scope_paths):
        raise SmokeConfigurationError(
            "GitHub smoke requires branch, base revision, and at least one scope path"
        )
    workspace = Workspace(
        workspace_id=item_id,
        repository=repository,
        worktree_path=f"/tmp/mergewave-smoke/{item_id}",
        branch_ref=branch_ref,
        base_revision=base_revision,
        initial_head_revision=base_revision,
        current_head_revision=base_revision,
    )
    observation = GitHubDeliveryObserver(
        repository=repository,
        transport=transport or UrllibGitHubTransport(token),
        scope_paths={item_id: tuple(scope_paths)},
    ).observe(item_id, workspace)
    return {
        "provider": "github",
        "status": "ok",
        "repository": repository,
        "item_id": item_id,
        "pr_head_sha": observation.pr_head_sha,
        "ci_head_sha": observation.ci_head_sha,
        "ci_passed": observation.ci_passed,
        "approvals": observation.approvals,
        "scope_ok": observation.scope_ok,
        "merged": observation.merged,
        "merge_revision": observation.merge_revision,
        "base_is_ancestor": observation.base_is_ancestor,
    }


__all__ = [
    "SmokeConfigurationError",
    "github_read_only_smoke",
    "linear_read_only_smoke",
]
