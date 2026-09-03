from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest

from mergewave.acp_runtime import AcpAgentRuntime
from mergewave.controller import ControllerProjection, DeliveryController
from mergewave.git_workspace import Workspace
from mergewave.persistence import SqliteEventLog
from mergewave.runtime import AgentEvent, RunHandle, RunSpec
from mergewave.skills import (
    FileSkillManifestVerifier,
    GitWorkspaceAuthorityVerifier,
    SkillArtifact,
    SkillAuthority,
    SkillInvocation,
    SkillResult,
    WorkspaceSkillArtifactVerifier,
)
from mergewave.simulator import MergeWaveSimulator


PACK_VERSION = "0.3.0"
MANIFEST_REF = "agentic-skills/.codex/manifest.json"
MANIFEST_SHA = "sha256:" + "a" * 64
ARTIFACT_SHA = "sha256:" + "b" * 64


def authority(mode: str = "read-only") -> SkillAuthority:
    operations = ("read", "execute") if mode == "read-only" else ("read", "write", "execute")
    return SkillAuthority(mode, (".",), operations, "test-policy", "2099-01-01T00:00:00+00:00")


def skill_template(skill: str = "atdd-plan", stage: str = "plan") -> SkillInvocation:
    return SkillInvocation(
        skill,
        PACK_VERSION,
        stage,
        manifest_ref=MANIFEST_REF,
        manifest_sha256=MANIFEST_SHA,
        authority=authority("mutating-with-authority" if stage == "implement-test" else "read-only"),
    )


class StaticManifestVerifier:
    def verify(self, invocation: SkillInvocation) -> None:
        if invocation.manifest_ref != MANIFEST_REF or invocation.manifest_sha256 != MANIFEST_SHA:
            raise ValueError("unexpected test manifest")


class TestArtifactVerifier:
    def verify(self, artifact: SkillArtifact, workspace: Workspace) -> dict[str, object]:
        return {"verified_scope": "test-workspace", "observed_sha256": artifact.sha256}


class TestAuthorityVerifier:
    def capture(self, workspace: Workspace) -> str:
        return f"baseline:{workspace.workspace_id}"

    def verify(self, workspace: Workspace, authority: SkillAuthority, baseline: object) -> dict[str, object]:
        self.last = (workspace.workspace_id, authority.mode, baseline)
        return {"changed_paths": [], "observed_head_revision": workspace.current_head_revision}


class RejectingAuthorityVerifier(TestAuthorityVerifier):
    def verify(self, workspace: Workspace, authority: SkillAuthority, baseline: object) -> dict[str, object]:
        raise ValueError("changed path outside authority")


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

    def destroy(self, workspace: Workspace) -> Workspace:
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
    def test_invocation_and_artifact_are_strict_and_serializable(self) -> None:
        invocation = skill_template().bind(work_item_id="CTRL-1", attempt_id="attempt:1")
        artifact = SkillArtifact("attempts/CTRL-1/plan.json", ARTIFACT_SHA, "application/json")

        self.assertEqual(invocation.to_payload()["schema_version"], "1.0")
        self.assertEqual(invocation.to_payload()["skill_version"], PACK_VERSION)
        self.assertEqual(SkillInvocation.from_payload(invocation.to_payload()), invocation)
        self.assertEqual(artifact.artifact_id, f"artifact:{ARTIFACT_SHA}")
        self.assertEqual(SkillArtifact.from_payload(artifact.to_payload()), artifact)
        with self.assertRaises(ValueError):
            SkillArtifact.from_payload("report.md")

    def test_acp_start_carries_versioned_skill_identity_and_authority(self) -> None:
        transport = SkillTransport()
        runtime = AcpAgentRuntime(transport)
        skill = skill_template("tdd-writer", "implement-test").bind(work_item_id="CTRL-1", attempt_id="attempt:1")

        runtime.start(RunSpec("run-1", "CTRL-1", "Implement", ".", skill=skill))

        self.assertEqual(transport.requests[0][1]["skill"], skill.to_payload())
        self.assertTrue(runtime.capabilities().supports_authority)

    def test_controller_binds_result_to_source_attempt_workspace_and_verified_artifact(self) -> None:
        event_log = SqliteEventLog(":memory:")
        self.addCleanup(event_log.close)
        runtime = Runtime()
        controller = self._controller(runtime=runtime, event_log=event_log)
        controller.dispatch_ready({"CTRL-1": "Implement"})

        attempt = controller.work_attempt("CTRL-1")
        invocation = runtime.specs[-1].skill
        assert invocation is not None
        result = self._result(invocation, attempt, result_id="result:plan:1", artifact=True)
        source_event = self._source_event(controller, result, event_id="event:skill:1")

        self.assertIsNone(controller.observe_runtime_events("CTRL-1", [AgentEvent("skill.result", source_event)]))
        record = controller.skill_results("CTRL-1")[0]
        self.assertEqual(record["event_id"], "event:skill:1")
        self.assertEqual(record["attempt_id"], attempt.id)
        self.assertEqual(record["workspace_id"], controller.active_assignment("CTRL-1").workspace.workspace_id)
        self.assertEqual(record["artifact_bindings"][0]["verified_scope"], "test-workspace")
        self.assertEqual(record["artifact_bindings"][0]["artifact_id"], f"artifact:{ARTIFACT_SHA}")

        projection = ControllerProjection.from_event_log(event_log)
        self.assertEqual(projection.skill_results["CTRL-1"][0]["result_id"], "result:plan:1")

    def test_controller_rejects_late_result_from_superseded_attempt(self) -> None:
        runtime = Runtime()
        tracker = Tracker()
        controller = self._controller(runtime=runtime, tracker=tracker)
        controller.dispatch_ready({"CTRL-1": "Implement"})
        first_attempt = controller.work_attempt("CTRL-1")
        first_invocation = runtime.specs[-1].skill
        assert first_invocation is not None
        old_event = self._source_event(
            controller,
            self._result(first_invocation, first_attempt, result_id="result:old"),
            event_id="event:old",
        )

        controller.retry("CTRL-1")
        self.assertNotEqual(controller.work_attempt("CTRL-1").id, first_attempt.id)
        failure = controller.observe_runtime_events("CTRL-1", [AgentEvent("skill.result", old_event)])

        self.assertIsNotNone(failure)
        self.assertEqual(failure.code, "invalid_skill_result")
        self.assertEqual(controller.skill_results("CTRL-1"), ())
        self.assertIn(("CTRL-1", "NeedsAttention"), tracker.transitions)

    def test_controller_rejects_empty_evidence_and_unverified_artifact(self) -> None:
        controller = self._controller(runtime=Runtime())
        controller.dispatch_ready({"CTRL-1": "Implement"})
        invocation = controller.active_assignment("CTRL-1").handle
        current = controller._skill_invocations["CTRL-1"]  # noqa: SLF001 - contract test
        result = self._result(current, controller.work_attempt("CTRL-1"), result_id="result:bad", artifact=True)
        result["evidence"] = []
        failure = controller.observe_runtime_events(
            "CTRL-1",
            [AgentEvent("skill.result", self._source_event(controller, result, event_id="event:bad"))],
        )

        self.assertIsNotNone(failure)
        self.assertEqual(failure.code, "invalid_skill_result")
        self.assertIsNotNone(invocation)

    def test_controller_rejects_runtime_exit_without_result_or_with_authority_violation(self) -> None:
        missing = self._controller(runtime=Runtime())
        missing.dispatch_ready({"CTRL-1": "Implement"})
        missing_failure = missing.observe_runtime_events("CTRL-1", [AgentEvent("runtime.exited", {"returncode": 0})])
        self.assertIsNotNone(missing_failure)
        self.assertEqual(missing_failure.code, "missing_skill_result")

        violating = self._controller(runtime=Runtime(), authority_verifier=RejectingAuthorityVerifier())
        violating.dispatch_ready({"CTRL-1": "Implement"})
        violation = violating.observe_runtime_events("CTRL-1", [AgentEvent("runtime.exited", {"returncode": 0})])
        self.assertIsNotNone(violation)
        self.assertEqual(violation.code, "authority_violation")

    def test_dispatch_requires_manifest_provenance_and_authority(self) -> None:
        controller = self._controller(runtime=Runtime(), manifest_verifier=None, skill_invocations={"CTRL-1": SkillInvocation("atdd-plan", PACK_VERSION, "plan")})

        with self.assertRaisesRegex(ValueError, "manifest_ref"):
            controller.dispatch_ready({"CTRL-1": "Implement"})

    def test_workspace_artifact_and_authority_verifiers_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file_path = root / "report.json"
            file_path.write_text("{}", encoding="utf-8")
            digest = "sha256:" + hashlib.sha256(file_path.read_bytes()).hexdigest()
            workspace = Workspace("ws-1", "demo", directory, "main", "base", "base", "base")
            verifier = WorkspaceSkillArtifactVerifier()
            self.assertEqual(verifier.verify(SkillArtifact("report.json", digest), workspace)["verified_scope"], "workspace")
            with self.assertRaises(ValueError):
                verifier.verify(SkillArtifact("https://example.test/report.json", digest), workspace)

            authority_verifier = GitWorkspaceAuthorityVerifier()
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "MergeWave Test"], cwd=root, check=True)
            subprocess.run(["git", "add", "report.json"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, capture_output=True)
            baseline = authority_verifier.capture(workspace)
            (root / "src").mkdir()
            (root / "src" / "allowed.txt").write_text("allowed", encoding="utf-8")
            scoped = authority("mutating-with-authority")
            scoped = SkillAuthority(
                scoped.mode, ("src",), scoped.allowed_operations, scoped.approved_by, scoped.expires_at
            )
            self.assertEqual(
                authority_verifier.verify(workspace, scoped, baseline)["changed_paths"],
                ["src/allowed.txt"],
            )
            (root / "outside.txt").write_text("outside", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "exceed authority"):
                authority_verifier.verify(workspace, scoped, baseline)

    def _controller(
        self,
        *,
        runtime: Runtime,
        tracker: Tracker | None = None,
        event_log: SqliteEventLog | None = None,
        manifest_verifier: object | None = StaticManifestVerifier(),
        skill_invocations: dict[str, SkillInvocation] | None = None,
        authority_verifier: object | None = TestAuthorityVerifier(),
    ) -> DeliveryController:
        return DeliveryController(
            simulator=MergeWaveSimulator(
                [{"id": "CTRL-1", "blocked_by": []}],
                policy="continuous_frontier",
                base_revision="main-0",
            ),
            tracker=tracker or Tracker(),
            workspace_factory=WorkspaceFactory(),
            runtime=runtime,
            observer=object(),
            event_log=event_log,
            skill_invocations=skill_invocations or {"CTRL-1": skill_template()},
            manifest_verifier=manifest_verifier,  # type: ignore[arg-type]
            artifact_verifier=TestArtifactVerifier(),
            authority_verifier=authority_verifier,  # type: ignore[arg-type]
        )

    @staticmethod
    def _result(invocation: SkillInvocation, attempt: object, *, result_id: str, artifact: bool = False) -> dict[str, object]:
        return {
            "schema_version": "1.0",
            "result_id": result_id,
            "invocation_id": invocation.invocation_id,
            "attempt_id": invocation.attempt_id,
            "case_id": f"{invocation.stage}-CTRL-1-1",
            "work_item_id": "CTRL-1",
            "stage": invocation.stage,
            "skill": invocation.skill,
            "skill_version": PACK_VERSION,
            "status": "completed",
            "summary": "Structured result recorded.",
            "evidence": [{"kind": "file", "locator": "README.md", "note": "Contract"}],
            "findings": [],
            "artifacts": [{"uri": "report.json", "sha256": ARTIFACT_SHA}] if artifact else [],
            "next_actions": ["Continue."],
            "metadata": {},
        }

    @staticmethod
    def _source_event(controller: DeliveryController, result: dict[str, object], *, event_id: str) -> dict[str, object]:
        assignment = controller.active_assignment("CTRL-1")
        return {
            "event_id": event_id,
            "run_id": assignment.handle.run_id,
            "invocation_id": result["invocation_id"],
            "attempt_id": result["attempt_id"],
            "workspace_id": assignment.workspace.workspace_id,
            "result": result,
        }


class ManifestVerifierTests(unittest.TestCase):
    def test_file_manifest_verifier_checks_content_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text("{}", encoding="utf-8")
            digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            invocation = SkillInvocation(
                "atdd-plan",
                PACK_VERSION,
                "plan",
                manifest_ref="manifest.json",
                manifest_sha256=digest,
                authority=authority(),
            )
            FileSkillManifestVerifier(path, manifest_ref="manifest.json").verify(invocation)


if __name__ == "__main__":
    unittest.main()
