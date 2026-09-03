from __future__ import annotations

import sys
import time
import unittest

from mergewave.runtime import (
    CliAgentRuntime,
    RunSpec,
    RuntimeCapabilities,
    WorkerProfile,
    classify_runtime_event,
)
from mergewave.skills import SkillAuthority, SkillInvocation


def skill() -> SkillInvocation:
    return SkillInvocation(
        "atdd-plan",
        "0.3.0",
        "plan",
        manifest_ref="agentic-skills/.codex/manifest.json",
        manifest_sha256="sha256:" + "a" * 64,
        authority=SkillAuthority(
            "read-only", (".",), ("read", "execute"), "runtime-test", "2099-01-01T00:00:00+00:00"
        ),
    )


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

    def test_cli_runtime_exposes_capabilities_and_explicitly_rejects_continue(self) -> None:
        runtime = CliAgentRuntime((sys.executable, "-c", "print('agent-output')"))

        capabilities = runtime.capabilities()

        self.assertFalse(capabilities.supports_continue)
        with self.assertRaisesRegex(RuntimeError, "continue"):
            runtime.continue_run(None, "follow up")  # type: ignore[arg-type]

    def test_cli_runtime_emits_agent_timeout(self) -> None:
        runtime = CliAgentRuntime(
            (sys.executable, "-c", "import time; time.sleep(1)"),
            timeout_seconds=0.05,
        )

        handle = runtime.start(RunSpec("run-timeout", "CTRL-1", "prompt", "."))
        events = tuple(runtime.stream(handle))

        self.assertTrue(any(event.kind == "runtime.timeout" for event in events))
        self.assertEqual(
            classify_runtime_event(next(event for event in events if event.kind == "runtime.timeout")),
            "agent_timeout",
        )

    def test_cli_runtime_parses_source_bound_json_events_and_exports_invocation(self) -> None:
        runtime = CliAgentRuntime(
            (
                sys.executable,
                "-c",
                "import json, os; print(json.dumps({'type': 'skill.stage_started', 'payload': {'stage': 'plan'}})); print(os.environ['MERGEWAVE_SKILL_INVOCATION'])",
            )
        )

        events = tuple(
            runtime.stream(
                runtime.start(
                    RunSpec(
                        "run-1",
                        "CTRL-1",
                        "prompt",
                        ".",
                        skill=skill().bind(work_item_id="CTRL-1", attempt_id="attempt:1"),
                    )
                )
            )
        )

        self.assertEqual(events[0].kind, "skill.stage_started")
        self.assertEqual(events[0].payload["stage"], "plan")
        self.assertIn('"skill":"atdd-plan"', events[1].payload["line"])
        self.assertTrue(runtime.capabilities().supports_authority)

    def test_run_spec_can_carry_work_item_workspace_and_worker_profile(self) -> None:
        profile = WorkerProfile("cli", "claude-code", "model-1", "repo-write", "restricted", 2.0)
        spec = RunSpec("run-1", "CTRL-1", "prompt", ".", worker_profile=profile)

        self.assertEqual(spec.worker_profile.agent, "claude-code")
        self.assertEqual(spec.worker_profile.max_cost, 2.0)


class RuntimeContractTests(unittest.TestCase):
    def test_capabilities_are_explicit_and_serializable(self) -> None:
        capabilities = RuntimeCapabilities(
            supports_continue=True,
            supports_streaming=True,
            supports_cancel=True,
            transports=("stdio",),
        )

        self.assertTrue(capabilities.supports_continue)
        self.assertEqual(capabilities.transports, ("stdio",))


if __name__ == "__main__":
    unittest.main()
