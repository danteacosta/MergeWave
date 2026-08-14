from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest


class CliTests(unittest.TestCase):
    def test_demo_command_prints_the_initial_frontier(self) -> None:
        environment = {**os.environ, "PYTHONPATH": "src"}
        result = subprocess.run(
            [sys.executable, "-m", "mergewave", "--demo"],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["base_revision"], "main-0")
        self.assertEqual({item["work_item_id"] for item in payload["dispatches"]}, {"A", "B", "C"})


if __name__ == "__main__":
    unittest.main()
