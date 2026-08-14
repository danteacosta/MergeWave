"""Concrete Git provider for fresh target revisions and merge verification."""

from __future__ import annotations

from pathlib import Path
import subprocess


class GitProviderError(RuntimeError):
    """The local repository could not refresh or verify the target branch."""


class GitBaseRevisionProvider:
    """Fetch ``origin/main`` and verify merge commits against that fetched ref."""

    def __init__(self, repository: str | Path, *, remote: str = "origin", target_branch: str = "main") -> None:
        self._repository = Path(repository).resolve()
        self._remote = remote
        self._target_branch = target_branch
        if not (self._repository / ".git").exists():
            raise ValueError(f"repository is not a Git worktree: {self._repository}")

    @property
    def target_ref(self) -> str:
        return f"refs/remotes/{self._remote}/{self._target_branch}"

    def refresh(self) -> str:
        self._run("fetch", "--prune", self._remote, self._target_branch)
        return self._run("rev-parse", self.target_ref)

    def current_revision(self) -> str:
        return self.refresh()

    def contains_revision(self, revision: str) -> bool:
        if not revision.strip():
            return False
        target_revision = self.refresh()
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", revision, target_revision],
            cwd=self._repository,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode not in {0, 1}:
            raise GitProviderError(result.stderr.strip() or "Git ancestry verification failed")
        return result.returncode == 0

    def _run(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self._repository,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise GitProviderError(result.stderr.strip() or f"git {' '.join(args)} failed")
        return result.stdout.strip()


__all__ = ["GitBaseRevisionProvider", "GitProviderError"]
