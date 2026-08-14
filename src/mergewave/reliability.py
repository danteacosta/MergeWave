"""Optional ARP 3.0 wire adapter for MergeWave evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol


class ArpSink(Protocol):
    def write(self, kind: str, document: dict[str, object]) -> None: ...


class Arp3Recorder:
    """Serialize MergeWave observations to the ARP 3.0 contract boundary."""

    _capture_policies = {"none", "metadata", "redacted", "full"}

    def __init__(self, sink: ArpSink) -> None:
        self._sink = sink

    def record_run(
        self,
        *,
        run_id: str,
        base_revision: str,
        input_ref: str,
        input_hash: str,
        executor_name: str,
        executor_version: str,
        configuration_hash: str,
        capture_policy: str,
        extensions: Mapping[str, object] | None = None,
    ) -> None:
        self._sink.write(
            "manifest",
            {
                "schema_version": "3.0.0",
                "run_id": run_id,
                "source": {
                    "revision": base_revision,
                    "input_ref": input_ref,
                    "input_hash": input_hash,
                },
                "executor": {"name": executor_name, "version": executor_version},
                "configuration_hash": configuration_hash,
                "profile": "software-delivery/v1",
                "capture_policy": self._capture_policy(capture_policy),
                "extensions": dict(extensions or {}),
            },
        )

    def record_evidence(
        self,
        *,
        evidence_id: str,
        run_id: str,
        claim: str,
        observed: bool | int | float | str,
        expected: bool | int | float | str,
        comparator: str,
        stage: str,
        capture_policy: str,
    ) -> None:
        self._sink.write(
            "evidence",
            {
                "schema_version": "3.0.0",
                "evidence_id": evidence_id,
                "run_id": run_id,
                "claim": claim,
                "observed": observed,
                "expected": expected,
                "comparator": comparator,
                "stage": stage,
                "capture_policy": self._capture_policy(capture_policy),
            },
        )

    def record_gate_decision(
        self,
        *,
        gate_id: str,
        run_id: str,
        decision: str,
        checkpoint: str,
        policy_version: str,
        decision_authority: str,
        evidence_ids: Sequence[str],
        capture_policy: str,
    ) -> None:
        self._sink.write(
            "gate.decision",
            {
                "schema_version": "3.0.0",
                "gate_id": gate_id,
                "run_id": run_id,
                "decision": decision,
                "checkpoint": checkpoint,
                "policy_version": policy_version,
                "decision_authority": decision_authority,
                "evidence_ids": list(evidence_ids),
                "capture_policy": self._capture_policy(capture_policy),
            },
        )

    def record_gate_request(
        self,
        *,
        gate_id: str,
        run_id: str,
        checkpoint: str,
        policy_version: str,
        decision_authority: str,
        required_evidence_ids: Sequence[str],
        capture_policy: str,
    ) -> None:
        self._sink.write(
            "gate.request",
            {
                "schema_version": "3.0.0",
                "gate_id": gate_id,
                "run_id": run_id,
                "checkpoint": checkpoint,
                "policy_version": policy_version,
                "decision_authority": decision_authority,
                "required_evidence_ids": list(required_evidence_ids),
                "status": "pending",
                "capture_policy": self._capture_policy(capture_policy),
            },
        )

    def _capture_policy(self, value: str) -> str:
        if value not in self._capture_policies:
            raise ValueError(f"unsupported ARP capture policy: {value}")
        return value
