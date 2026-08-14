from __future__ import annotations

import sys
import unittest

from mergewave.runtime import CliAgentRuntime, RunSpec


class CliAgentRuntimeTests(unittest.TestCase):
    def test_cli_runtime_captures_output_and_exit_status(self) -> None:
        runtime = CliAgentRuntime(
            (
                sys.executable,
                "-c",
                "print('agent-output')",
            )
        )

        handle = runtime.start(
            RunSpec(
                run_id="run-1",
                work_item_id="CTRL-1",
                prompt="Implement the ticket",
                workspace_path=".",
            )
        )
        events = tuple(runtime.stream(handle))

        self.assertEqual(events[0].kind, "runtime.output")
        self.assertEqual(events[0].payload["line"], "agent-output")
        self.assertEqual(events[-1].kind, "runtime.exited")
        self.assertEqual(events[-1].payload["returncode"], 0)


if __name__ == "__main__":
    unittest.main()
