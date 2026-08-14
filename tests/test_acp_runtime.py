from __future__ import annotations

import unittest

from mergewave.acp_runtime import AcpAgentRuntime
from mergewave.runtime import RunSpec


class FakeAcpTransport:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, object]]] = []

    def request(self, method: str, params: dict[str, object]) -> object:
        self.requests.append((method, params))
        if method == "session/start":
            return {"session_id": "session-1"}
        return {"ok": True}

    def events(self, session_id: str):
        self.requests.append(("events", {"session_id": session_id}))
        return iter(
            (
                {"type": "session/update", "text": "working"},
                {"type": "session/finished", "exit_code": 0},
            )
        )


class AcpAgentRuntimeTests(unittest.TestCase):
    def test_runtime_maps_start_stream_and_cancel_without_model_specific_types(self) -> None:
        transport = FakeAcpTransport()
        runtime = AcpAgentRuntime(transport)
        spec = RunSpec("run-1", "CTRL-1", "Implement it", "/worktrees/CTRL-1")

        handle = runtime.start(spec)
        events = tuple(runtime.stream(handle))
        cancelled = runtime.cancel(handle)

        self.assertEqual(handle.run_id, "run-1")
        self.assertEqual(events[0].kind, "session/update")
        self.assertEqual(events[0].payload["text"], "working")
        self.assertEqual(events[-1].kind, "session/finished")
        self.assertEqual(cancelled.kind, "runtime.cancelled")
        self.assertEqual(transport.requests[0][0], "session/start")
        self.assertEqual(transport.requests[0][1]["work_item_id"], "CTRL-1")

    def test_start_rejects_a_response_without_a_session_id(self) -> None:
        class InvalidTransport(FakeAcpTransport):
            def request(self, method: str, params: dict[str, object]) -> object:
                return {"accepted": True}

        runtime = AcpAgentRuntime(InvalidTransport())

        with self.assertRaisesRegex(RuntimeError, "session_id"):
            runtime.start(RunSpec("run-1", "CTRL-1", "Implement it", "."))


if __name__ == "__main__":
    unittest.main()
