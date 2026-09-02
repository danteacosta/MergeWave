from __future__ import annotations

import unittest

from mergewave.acp_runtime import AcpAgentRuntime
from mergewave.controller import ControllerProjection, DeliveryController
from mergewave.git_workspace import Workspace
from mergewave.persistence import SqliteEventLog
from mergewave.runtime import AgentEvent, RunHandle, RunSpec
from mergewave.skills import SkillArtifact, SkillInvocation
from mergewave.simulator import MergeWaveSimulator


class Tracker:
    def __init__(self) -> None:
        self.transitions: list[tuple[str, str]] = []

    def transition_state(self, item_id: str, state: str) -> None:
        self.transitions.append((item_id, state))

    def post_comment(self, item_id: str, body: str) -> None:
        pass

    def pull_request_linked(self, item_id: str, url: str) -> bool:
        return True

    def acceptance_criteria_signal(self, item_id: str) -> str:
        return "unknown"


class WorkspaceFactory:
    def create(self, workspace_id: str, base_revision: str) -> Workspace:
        return Workspace(
            workspace_id,
            "demo",
            f"/tmp/{workspace_id}",
            f"mergewave/{workspace_id}",
            base_revision,
            base_revision,
            base_revision,
        )

    def inspect(self, workspace: Workspace) -> Workspace:
        return workspace


class Runtime:
    def __init__(self) -> None:
        self.specs: list[RunSpec] = []

    def start(self, spec: RunSpec) -> RunHandle:
        self.specs.append(spec)
        return RunHandle(spec.run_id, object())

    def cancel(self, handle: RunHandle) -> AgentEvent:
        return AgentEvent("runtime.cancelled", {})


class SkillTransport:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, object]]] = []

    def request(self, method: str, params: dict[str, object]) -> object:
        self.requests.append((method, params))
        return {"session_id": "session-1"} if method == "session/start" else {"ok": True}

    def events(self, session_id: str):
        return iter(())


class SkillContractTests(unittest.TestCase):
    def test_invocation_and_artifact_are_stable_and_serializable(self) -> None:
        invocation = SkillInvocation(
            "atdd-plan",
            "0.1.0",
            "plan",
            manifest_ref="urn:agentic-skills:manifest:0.1.0",
        )
        artifact = SkillArtifact("attempts/CTRL-1/plan.json", "sha256:abc", "application/json")

        self.assertEqual(invocation.to_payload()["skill_version"], "0.1.0")
        self.assertTrue(artifact.artifact_id.startswith("artifact:"))
        self.assertEqual(SkillArtifact.from_payload("report.md").uri, "report.md")

    def test_acp_start_carries_versioned_skill_identity(self) -> None:
        transport = SkillTransport()
        runtime = AcpAgentRuntime(transport)
        skill = SkillInvocation("tdd-writer", "0.1.0", "implement-test")

        runtime.start(RunSpec("run-1", "CTRL-1", "Implement", ".", skill=skill))

        self.assertEqual(transport.requests[0][1]["skill"], skill.to_payload())

    def test_controller_binds_skill_result_to_attempt_workspace_and_artifact(self) -> None:
        event_log = SqliteEventLog(":memory:")
        self.addCleanup(event_log.close)
        skill = SkillInvocation("atdd-plan", "0.1.0", "plan")
        runtime = Runtime()
        controller = DeliveryController(
            simulator=MergeWaveSimulator(
                [{"id": "CTRL-1", "blocked_by": []}],
                policy="continuous_frontier",
                base_revision="main-0",
            ),
            tracker=Tracker(),
            workspace_factory=WorkspaceFactory(),
            runtime=runtime,
            observer=object(),
            event_log=event_log,
            skill_invocations={"CTRL-1": skill},
        )
        controller.dispatch_ready({"CTRL-1": "Implement"})

        result = {
            "schema_version": "1.0",
            "case_id": "plan-CTRL-1-1",
            "work_item_id": "CTRL-1",
            "skill": "atdd-plan",
            "skill_version": "0.1.0",
            "status": "completed",
            "summary": "Plan recorded.",
            "evidence": [{"kind": "file", "locator": "README.md", "note": "Contract"}],
            "findings": [],
            "artifacts": [{"uri": "attempts/CTRL-1/plan.json", "sha256": "sha256:abc"}],
            "next_actions": ["Run implementation."],
            "metadata": {"stage": "plan"},
        }

        self.assertIsNone(controller.observe_runtime_events("CTRL-1", [AgentEvent("skill.result", result)]))
        records = controller.skill_results("CTRL-1")
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["attempt_id"], controller.work_attempt("CTRL-1").id)
        self.assertEqual(record["workspace_id"], controller.active_assignment("CTRL-1").workspace.workspace_id)
        self.assertEqual(record["skill_version"], "0.1.0")
        self.assertEqual(record["artifact_bindings"][0]["attempt_id"], record["attempt_id"])
        self.assertEqual(record["artifact_bindings"][0]["workspace_id"], record["workspace_id"])

        projection = ControllerProjection.from_event_log(event_log)
        self.assertEqual(len(projection.skill_results["CTRL-1"]), 1)
        self.assertEqual(projection.skill_results["CTRL-1"][0]["result"]["case_id"], "plan-CTRL-1-1")

    def test_controller_rejects_result_from_a_different_skill_version(self) -> None:
        skill = SkillInvocation("atdd-plan", "0.1.0", "plan")
        tracker = Tracker()
        controller = DeliveryController(
            simulator=MergeWaveSimulator(
                [{"id": "CTRL-1", "blocked_by": []}],
                policy="continuous_frontier",
                base_revision="main-0",
            ),
            tracker=tracker,
            workspace_factory=WorkspaceFactory(),
            runtime=Runtime(),
            observer=object(),
            skill_invocations={"CTRL-1": skill},
        )
        controller.dispatch_ready({"CTRL-1": "Implement"})
        result = {
            "schema_version": "1.0",
            "case_id": "plan-CTRL-1-1",
            "work_item_id": "CTRL-1",
            "skill": "atdd-plan",
            "skill_version": "0.0.1",
            "status": "completed",
            "summary": "Plan recorded.",
            "evidence": [],
            "findings": [],
            "artifacts": [],
            "next_actions": [],
        }

        failure = controller.observe_runtime_events("CTRL-1", [AgentEvent("skill.result", result)])

        self.assertIsNotNone(failure)
        self.assertEqual(failure.code, "invalid_skill_result")
        self.assertEqual(controller.skill_results("CTRL-1"), ())
        self.assertIn(("CTRL-1", "NeedsAttention"), tracker.transitions)


if __name__ == "__main__":
    unittest.main()
