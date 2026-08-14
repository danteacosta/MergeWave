"""Local Git worktree operations with explicit revision invariants."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import subprocess


@dataclass(frozen=True)
class Workspace:
    workspace_id: str
    repository: str
    worktree_path: str
    branch_ref: str
    base_revision: str
    initial_head_revision: str
    current_head_revision: str


class WorkspaceDriftError(RuntimeError):
    """The assigned repository, worktree, or branch no longer matches."""


class GitWorkspaceFactory:
    def __init__(self, repository: str | Path, workspace_root: str | Path) -> None:
        self._repository = Path(repository).resolve()
        self._workspace_root = Path(workspace_root).resolve()
        if not (self._repository / ".git").exists():
            raise ValueError(f"repository is not a Git worktree: {self._repository}")

    def create(self, workspace_id: str, base_revision: str) -> Workspace:
        self._workspace_root.mkdir(parents=True, exist_ok=True)
        worktree_path = self._workspace_root / workspace_id
        if worktree_path.exists():
            raise FileExistsError(f"workspace already exists: {worktree_path}")
        branch_ref = f"mergewave/{workspace_id}"
        self._git(
            "worktree",
            "add",
            "-b",
            branch_ref,
            str(worktree_path),
            base_revision,
            cwd=self._repository,
        )
        initial_head = self._rev_parse(worktree_path)
        if initial_head != base_revision:
            raise RuntimeError(
                "workspace creation violated initial_head_revision == base_revision"
            )
        return Workspace(
            workspace_id=workspace_id,
            repository=str(self._repository),
            worktree_path=str(worktree_path),
            branch_ref=branch_ref,
            base_revision=base_revision,
            initial_head_revision=initial_head,
            current_head_revision=initial_head,
        )

    def inspect(self, workspace: Workspace) -> Workspace:
        worktree_path = Path(workspace.worktree_path).resolve()
        try:
            actual_worktree = Path(
                self._git("rev-parse", "--show-toplevel", cwd=worktree_path)
            ).resolve()
            actual_common_dir = Path(
                self._git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=worktree_path)
            ).resolve()
            actual_branch = self._git("symbolic-ref", "--short", "HEAD", cwd=worktree_path)
        except subprocess.CalledProcessError as error:
            raise WorkspaceDriftError("workspace is detached or no longer a valid Git worktree") from error
        if actual_worktree != worktree_path:
            raise WorkspaceDriftError("workspace worktree path does not match its assignment")
        if actual_common_dir != self._repository / ".git":
            raise WorkspaceDriftError("workspace repository does not match its assignment")
        if actual_branch != workspace.branch_ref:
            raise WorkspaceDriftError("workspace branch does not match its assignment")
        current_head = self._rev_parse(worktree_path)
        return replace(workspace, current_head_revision=current_head)

    def is_ancestor(self, base_revision: str, descendant_revision: str) -> bool:
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", base_revision, descendant_revision],
            cwd=self._repository,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode not in {0, 1}:
            raise RuntimeError(result.stderr.strip() or "Git ancestry check failed")
        return result.returncode == 0

    def _rev_parse(self, worktree_path: Path) -> str:
        return self._git("rev-parse", "HEAD", cwd=worktree_path)

    @staticmethod
    def _git(*args: str, cwd: Path) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
