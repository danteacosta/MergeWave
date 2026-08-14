from __future__ import annotations

import unittest

from mergewave.reliability import Arp3Recorder


class MemorySink:
    def __init__(self) -> None:
        self.documents: list[tuple[str, dict[str, object]]] = []

    def write(self, kind: str, document: dict[str, object]) -> None:
        self.documents.append((kind, document))


class Arp3RecorderTests(unittest.TestCase):
    def test_run_manifest_uses_generic_v3_fields_and_delivery_profile_extension(self) -> None:
        sink = MemorySink()
        recorder = Arp3Recorder(sink)

        recorder.record_run(
            run_id="run-1",
            base_revision="main-0",
            input_ref="graph-snapshot",
            input_hash="sha256:graph",
            executor_name="cli-runtime",
            executor_version="0.1.0",
            configuration_hash="sha256:config",
            capture_policy="metadata",
            extensions={"software-delivery/v1": {"work_item_id": "CTRL-1"}},
        )

        kind, document = sink.documents[0]
        self.assertEqual(kind, "manifest")
        self.assertEqual(document["schema_version"], "3.0.0")
        self.assertEqual(document["profile"], "software-delivery/v1")
        self.assertEqual(document["source"]["revision"], "main-0")
        self.assertNotIn("model_name", document)

    def test_gate_decision_preserves_evidence_ids_and_authority(self) -> None:
        sink = MemorySink()
        recorder = Arp3Recorder(sink)

        recorder.record_gate_decision(
            gate_id="gate-1",
            run_id="run-1",
            decision="approve",
            checkpoint="release.gate",
            policy_version="human-merge/1",
            decision_authority="human",
            evidence_ids=("evidence-1", "evidence-2"),
            capture_policy="metadata",
        )

        kind, document = sink.documents[0]
        self.assertEqual(kind, "gate.decision")
        self.assertEqual(document["evidence_ids"], ["evidence-1", "evidence-2"])
        self.assertEqual(document["decision_authority"], "human")

    def test_gate_request_is_recorded_before_a_decision(self) -> None:
        sink = MemorySink()
        recorder = Arp3Recorder(sink)

        recorder.record_gate_request(
            gate_id="gate-1",
            run_id="run-1",
            checkpoint="release.gate",
            policy_version="human-merge/1",
            decision_authority="human",
            required_evidence_ids=("evidence-1",),
            capture_policy="metadata",
        )

        kind, document = sink.documents[0]
        self.assertEqual(kind, "gate.request")
        self.assertEqual(document["required_evidence_ids"], ["evidence-1"])
        self.assertEqual(document["status"], "pending")


if __name__ == "__main__":
    unittest.main()
