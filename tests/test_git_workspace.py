from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from mergewave.git_workspace import GitWorkspaceFactory, WorkspaceDriftError


def git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "MergeWave Test",
            "GIT_AUTHOR_EMAIL": "test@mergewave.local",
            "GIT_COMMITTER_NAME": "MergeWave Test",
            "GIT_COMMITTER_EMAIL": "test@mergewave.local",
        },
    )
    return result.stdout.strip()


class GitWorkspaceFactoryTests(unittest.TestCase):
    def test_creation_records_initial_head_and_inspection_tracks_normal_commits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            repository = root / "repository"
            repository.mkdir()
            git("init", "-q", cwd=repository)
            (repository / "README.md").write_text("initial\n")
            git("add", "README.md", cwd=repository)
            git("commit", "-qm", "initial", cwd=repository)
            base_revision = git("rev-parse", "HEAD", cwd=repository)

            factory = GitWorkspaceFactory(repository, root / "worktrees")
            workspace = factory.create("CTRL-1", base_revision)
            (Path(workspace.worktree_path) / "change.txt").write_text("change\n")
            git("add", "change.txt", cwd=Path(workspace.worktree_path))
            git("commit", "-qm", "change", cwd=Path(workspace.worktree_path))

            inspected = factory.inspect(workspace)

            self.assertEqual(workspace.base_revision, base_revision)
            self.assertEqual(workspace.initial_head_revision, base_revision)
            self.assertNotEqual(inspected.current_head_revision, base_revision)
            self.assertTrue(factory.is_ancestor(base_revision, inspected.current_head_revision))

            git("checkout", "--orphan", "unrelated", cwd=repository)
            (repository / "unrelated.txt").write_text("unrelated\n")
            git("add", "unrelated.txt", cwd=repository)
            git("commit", "-qm", "unrelated", cwd=repository)
            unrelated_revision = git("rev-parse", "HEAD", cwd=repository)

            self.assertFalse(factory.is_ancestor(base_revision, unrelated_revision))

    def test_inspection_rejects_a_workspace_on_the_wrong_branch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            repository = root / "repository"
            repository.mkdir()
            git("init", "-q", cwd=repository)
            (repository / "README.md").write_text("initial\n")
            git("add", "README.md", cwd=repository)
            git("commit", "-qm", "initial", cwd=repository)
            base_revision = git("rev-parse", "HEAD", cwd=repository)

            factory = GitWorkspaceFactory(repository, root / "worktrees")
            workspace = factory.create("CTRL-1", base_revision)
            git("checkout", "-qb", "wrong-branch", cwd=Path(workspace.worktree_path))

            with self.assertRaises(WorkspaceDriftError):
                factory.inspect(workspace)


if __name__ == "__main__":
    unittest.main()
