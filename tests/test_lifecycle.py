from __future__ import annotations

import unittest

from mergewave.lifecycle import DEFAULT_LIFECYCLE_STAGES, LifecycleAgentRuntime, LifecycleRouter
from mergewave.runtime import AgentEvent, RunHandle, RunSpec, RuntimeCapabilities
from mergewave.skills import SkillAuthority, SkillInvocation


def stage_authority(mode: str) -> SkillAuthority:
    operations = ("read", "execute") if mode == "read-only" else ("read", "write", "execute")
    return SkillAuthority(mode, (".",), operations, "lifecycle-test", "2099-01-01T00:00:00+00:00")


class SequentialDelegate:
    def __init__(self, *, fail_stage: str | None = None) -> None:
        self.specs: list[RunSpec] = []
        self.fail_stage = fail_stage

    def start(self, spec: RunSpec) -> RunHandle:
        self.specs.append(spec)
        return RunHandle(spec.run_id, spec)

    def stream(self, handle: RunHandle):
        spec = handle.runtime_ref
        invocation = spec.skill
        assert invocation is not None
        status = "failed" if invocation.stage == self.fail_stage else "completed"
        yield AgentEvent(
            "skill.result",
            {
                "schema_version": "1.0",
                "result_id": f"result:{invocation.stage}",
                "invocation_id": invocation.invocation_id,
                "attempt_id": invocation.attempt_id,
                "case_id": f"{invocation.stage}-case-1",
                "work_item_id": spec.work_item_id,
                "stage": invocation.stage,
                "skill": invocation.skill,
                "skill_version": invocation.skill_version,
                "status": status,
                "summary": f"{invocation.stage} result",
                "evidence": [{"kind": "runtime", "locator": "stage-output", "note": "delegate evidence"}],
                "findings": [],
                "artifacts": [],
                "next_actions": [],
                "metadata": {},
            },
        )
        yield AgentEvent("runtime.exited", {"returncode": 0})

    def continue_run(self, handle: RunHandle, input: str) -> None:
        pass

    def cancel(self, handle: RunHandle) -> AgentEvent:
        return AgentEvent("runtime.cancelled", {})

    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(False, True, True, ("test",), False, True)

    def snapshot(self, handle: RunHandle):
        return {}

    def reattach(self, run_id: str, snapshot):
        raise RuntimeError("not supported")


class LifecycleTests(unittest.TestCase):
    def test_router_skips_debug_without_failure_and_unblocks_stages_in_order(self) -> None:
        router = LifecycleRouter()
        self.assertEqual(router.next(()).stage.id, "intake")
        self.assertEqual(router.next((), skipped_stages={"intake"}).action, "run")

    def test_runtime_executes_full_lifecycle_and_emits_explicit_skip(self) -> None:
        delegate = SequentialDelegate()
        runtime = LifecycleAgentRuntime(delegate, stage_authorities=self._authorities())
        initial = SkillInvocation(
            "intake",
            "0.3.0",
            "intake",
            manifest_ref="agentic-skills/.codex/manifest.json",
            manifest_sha256="sha256:" + "a" * 64,
            authority=stage_authority("read-only"),
        )

        events = tuple(runtime.stream(runtime.start(RunSpec("run-1", "CTRL-1", "do it", ".", skill=initial))))

        started = [event for event in events if event.kind == "skill.stage_started"]
        results = [event for event in events if event.kind == "skill.result"]
        self.assertEqual([event.payload["invocation"]["stage"] for event in started], [
            "intake", "explore", "plan", "implement-test", "verify", "review", "release", "wrap-up"
        ])
        self.assertEqual(len(results), 8)
        self.assertTrue(any(event.kind == "skill.stage_skipped" and event.payload["stage"] == "debug" for event in events))
        self.assertEqual(events[-1].kind, "runtime.exited")
        self.assertEqual(events[-1].payload["returncode"], 0)
        self.assertEqual(len(delegate.specs), 8)

    def test_runtime_routes_failed_implementation_to_debug_and_stops_before_verify(self) -> None:
        delegate = SequentialDelegate(fail_stage="implement-test")
        runtime = LifecycleAgentRuntime(delegate, stage_authorities=self._authorities())
        initial = SkillInvocation(
            "intake", "0.3.0", "intake",
            manifest_ref="agentic-skills/.codex/manifest.json",
            manifest_sha256="sha256:" + "a" * 64,
            authority=stage_authority("read-only"),
        )

        events = tuple(runtime.stream(runtime.start(RunSpec("run-2", "CTRL-1", "do it", ".", skill=initial))))

        stages = [spec.skill.stage for spec in delegate.specs if spec.skill is not None]
        self.assertEqual(stages, ["intake", "explore", "plan", "implement-test", "debug"])
        self.assertEqual(events[-1].kind, "runtime.exited")
        self.assertEqual(events[-1].payload["returncode"], 1)
        self.assertTrue(any(event.kind == "skill.result" and event.payload["result"]["status"] == "failed" for event in events))

    @staticmethod
    def _authorities() -> dict[str, SkillAuthority]:
        return {stage.id: stage_authority(stage.mode) for stage in DEFAULT_LIFECYCLE_STAGES}


if __name__ == "__main__":
    unittest.main()
