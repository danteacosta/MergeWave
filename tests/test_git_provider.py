from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from mergewave.git_provider import GitBaseRevisionProvider


class GitBaseRevisionProviderTests(unittest.TestCase):
    def test_refresh_fetches_origin_main_and_proves_merge_revision_is_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            (repository / ".git").mkdir()
            completed = lambda stdout="", returncode=0: subprocess.CompletedProcess([], returncode, stdout, "")
            with patch(
                "mergewave.git_provider.subprocess.run",
                side_effect=[completed(), completed("main-1\n"), completed(), completed("main-1\n"), completed()],
            ) as run:
                provider = GitBaseRevisionProvider(repository)

                self.assertEqual(provider.current_revision(), "main-1")
                self.assertTrue(provider.contains_revision("merge-1"))

            commands = [call.args[0] for call in run.call_args_list]
            self.assertEqual(commands[0], ["git", "fetch", "--prune", "origin", "main"])
            self.assertEqual(commands[1], ["git", "rev-parse", "refs/remotes/origin/main"])
            self.assertEqual(commands[-1], ["git", "merge-base", "--is-ancestor", "merge-1", "main-1"])


if __name__ == "__main__":
    unittest.main()
