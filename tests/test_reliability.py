from __future__ import annotations

import unittest

from mergewave.reliability import Arp3Contracts, Arp3Recorder


class MemorySink:
    def __init__(self) -> None:
        self.documents: list[tuple[str, dict[str, object]]] = []

    def write(self, kind: str, document: dict[str, object]) -> None:
        self.documents.append((kind, document))


class FakeValue:
    def __init__(self, **values: object) -> None:
        self.values = values

    def to_dict(self) -> dict[str, object]:
        def serialize(value: object) -> object:
            if hasattr(value, "to_dict"):
                return value.to_dict()
            if isinstance(value, (list, tuple)):
                return [serialize(item) for item in value]
            if isinstance(value, dict):
                return {key: serialize(item) for key, item in value.items()}
            return value

        return {key: serialize(value) for key, value in self.values.items()}


class FakeReason:
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class FakeV3Value(FakeValue):
    def __init__(self, **values: object) -> None:
        values.setdefault("schema_version", "3.0.0")
        super().__init__(**values)


class FakeContracts:
    RunManifestV3 = FakeV3Value
    SourceIdentity = FakeValue
    ExecutorIdentity = FakeValue
    EvidenceRecord = FakeV3Value
    GateReason = FakeReason
    GateRequestV3 = FakeV3Value
    GateDecisionV3 = FakeV3Value
    CapturePolicy = str

    @staticmethod
    def check_contract(kind: str, payload: dict[str, object]) -> list[str]:
        return []


class Arp3RecorderTests(unittest.TestCase):
    def recorder(self) -> tuple[Arp3Recorder, MemorySink]:
        sink = MemorySink()
        return Arp3Recorder(sink, contracts=FakeContracts), sink

    def test_run_manifest_uses_v3_fields_and_namespaced_delivery_extension(self) -> None:
        recorder, sink = self.recorder()

        recorder.record_run(
            run_id="run-1",
            base_revision="main-0",
            input_ref="graph-snapshot",
            input_hash="sha256:graph",
            executor_name="cli-runtime",
            executor_version="0.1.0",
            configuration_hash="sha256:config",
            capture_policy="metadata",
            extensions={"work_item_id": "CTRL-1"},
        )

        kind, document = sink.documents[0]
        self.assertEqual(kind, "manifest")
        self.assertEqual(document["schema_version"], "3.0.0")
        self.assertIn("created_at", document)
        self.assertIn("environment", document)
        self.assertEqual(document["extensions"], {"software-delivery/v1": {"work_item_id": "CTRL-1"}})

    def test_evidence_uses_valid_stage_and_emits_content_addressed_artifact(self) -> None:
        recorder, sink = self.recorder()

        recorder.record_evidence(
            evidence_id="evidence-1",
            run_id="run-1",
            claim="delivery_gate_status",
            observed="approved",
            expected="approved",
            comparator="equals",
            stage="final_artifact",
            capture_policy="metadata",
            artifact_payload={"head_sha": "head-1"},
        )

        kinds = [kind for kind, _ in sink.documents]
        self.assertEqual(kinds, ["artifact", "evidence"])
        self.assertEqual(sink.documents[-1][1]["stage"], "final_artifact")
        self.assertTrue(sink.documents[-1][1]["artifact_uri"])
        self.assertTrue(sink.documents[-1][1]["artifact_hash"])

    def test_gate_request_is_pending_and_decision_uses_arp_vocabulary(self) -> None:
        recorder, sink = self.recorder()

        recorder.record_gate_request(
            gate_id="gate-1",
            run_id="run-1",
            checkpoint="merge",
            policy_version="human-merge/1",
            decision_authority="human",
            required_evidence_ids=("evidence-1",),
            capture_policy="metadata",
            requested_at="2026-08-14T00:00:00+00:00",
        )
        recorder.record_gate_decision(
            gate_id="gate-1",
            run_id="run-1",
            decision="blocked",
            checkpoint="merge",
            policy_version="human-merge/1",
            decision_authority="human",
            evidence_ids=("evidence-1",),
            capture_policy="metadata",
            reasons=(("stale_ci", "CI is stale."),),
            decided_at="2026-08-14T00:01:00+00:00",
        )

        request = sink.documents[0][1]
        decision = sink.documents[1][1]
        self.assertEqual(request["requested_at"], "2026-08-14T00:00:00+00:00")
        self.assertNotIn("status", request)
        self.assertEqual(decision["decision"], "block")
        self.assertEqual(decision["decided_at"], "2026-08-14T00:01:00+00:00")

    def test_pending_decision_is_not_emitted(self) -> None:
        recorder, sink = self.recorder()
        recorder.record_gate_decision(
            gate_id="gate-1", run_id="run-1", decision="pending", checkpoint="merge",
            policy_version="human-merge/1", decision_authority="human", evidence_ids=(), capture_policy="metadata",
        )
        self.assertEqual(sink.documents, [])

    def test_real_arp_3_contracts_validate_when_arp_3_is_installed(self) -> None:
        try:
            contracts = Arp3Contracts.load()
        except ModuleNotFoundError:
            self.skipTest("ARP 3.0 is not installed in this test environment")
        sink = MemorySink()
        recorder = Arp3Recorder(sink, contracts=contracts)
        recorder.record_run(
            run_id="run-1", base_revision="main-0", input_ref="graph", input_hash="sha256:graph",
            executor_name="runtime", executor_version="1", configuration_hash="sha256:config", capture_policy="metadata",
        )
        recorder.record_evidence(
            evidence_id="evidence-1", run_id="run-1", claim="delivery_gate_status",
            observed="approved", expected="approved", comparator="equals", stage="final_artifact",
            capture_policy="metadata", artifact_payload={"head_sha": "head-1"},
        )
        recorder.record_gate_request(
            gate_id="gate-1", run_id="run-1", checkpoint="merge", policy_version="delivery/1",
            decision_authority="human", required_evidence_ids=("evidence-1",), capture_policy="metadata",
        )
        recorder.record_gate_decision(
            gate_id="gate-1", run_id="run-1", decision="approved", checkpoint="merge", policy_version="delivery/1",
            decision_authority="human", evidence_ids=("evidence-1",), capture_policy="metadata",
        )
        self.assertEqual(sink.documents[0][1]["schema_version"], "3.0.0")
        by_kind = {kind: document for kind, document in sink.documents if kind != "artifact"}
        self.assertEqual(contracts.check_contract("manifest", by_kind["manifest"]), [])
        self.assertEqual(contracts.check_contract("evidence", by_kind["evidence"]), [])
        self.assertEqual(contracts.check_contract("gate-request", by_kind["gate.request"]), [])
        self.assertEqual(contracts.check_contract("decision", by_kind["gate.decision"]), [])


if __name__ == "__main__":
    unittest.main()
