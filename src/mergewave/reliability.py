"""Optional ARP 3.0 wire adapter for MergeWave evidence.

The control-plane core depends only on ``ReliabilityRecorder``.  This module
is the integration boundary: when ARP 3.0 is installed it constructs the
canonical v3 value objects, serializes them, and validates every payload
before handing it to the configured sink.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib
import json
from typing import Any, Callable, Protocol


class ArpSink(Protocol):
    def write(self, kind: str, document: dict[str, object]) -> None: ...


@dataclass(frozen=True)
class Arp3Contracts:
    """The small ARP API surface needed by this adapter.

    Keeping it injectable lets MergeWave test its mapping without importing
    ARP in the core package, while production uses the real ARP 3.0 classes.
    """

    RunManifestV3: type
    EpisodeIdentityV3: type
    LifecycleEventV3: type
    SourceIdentity: type
    ExecutorIdentity: type
    EvidenceRecord: type
    GateReason: type
    GateRequestV3: type
    GateDecisionV3: type
    CapturePolicy: type
    check_contract: Callable[[str, Mapping[str, Any]], list[str]]

    @classmethod
    def load(cls) -> "Arp3Contracts":
        arp = importlib.import_module("agent_reliability_protocol")
        v3 = importlib.import_module("agent_reliability_protocol.v3")
        return cls(
            RunManifestV3=v3.RunManifest,
            EpisodeIdentityV3=v3.EpisodeIdentity,
            LifecycleEventV3=v3.LifecycleEvent,
            SourceIdentity=v3.SourceIdentity,
            ExecutorIdentity=v3.ExecutorIdentity,
            EvidenceRecord=v3.EvidenceRecord,
            GateReason=v3.GateReason,
            GateRequestV3=v3.GateRequest,
            GateDecisionV3=v3.GateDecision,
            CapturePolicy=arp.CapturePolicy,
            check_contract=arp.check_contract,
        )


class Arp3Recorder:
    """Serialize MergeWave observations to the ARP 3.0 contract boundary."""

    _capture_policies = {"none", "metadata", "redacted", "full"}

    def __init__(
        self,
        sink: ArpSink,
        *,
        contracts: Arp3Contracts | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sink = sink
        self._contracts = contracts or Arp3Contracts.load()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sequence_by_run: dict[str, int] = {}
        self._episode_by_run: dict[str, str] = {}

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
        environment: Mapping[str, object] | None = None,
    ) -> None:
        c = self._contracts
        manifest = c.RunManifestV3(
            run_id=run_id,
            created_at=self._now(),
            source=c.SourceIdentity(
                revision=base_revision,
                input_ref=input_ref,
                input_hash=input_hash,
            ),
            executor=c.ExecutorIdentity(name=executor_name, version=executor_version),
            configuration_hash=configuration_hash,
            environment=dict(environment or {}),
            profile="software-delivery/v1",
            capture_policy=self._capture_policy(capture_policy),
            extensions=self._extensions(extensions),
        )
        self._emit("manifest", manifest.to_dict(), "manifest")
        episode_id = f"episode:{run_id}"
        self._episode_by_run[run_id] = episode_id
        episode = c.EpisodeIdentityV3(run_id=run_id, episode_id=episode_id)
        self._emit("episode", episode.to_dict(), "episode")
        self._emit_lifecycle(run_id, "episode.started", extensions)
        self._emit_lifecycle(run_id, "execution.started", extensions)

    def restore_run(self, run_id: str, *, sequence_number: int) -> None:
        """Restore local lifecycle state without re-emitting portable records."""
        if sequence_number < 0:
            raise ValueError("sequence_number cannot be negative")
        self._episode_by_run[run_id] = f"episode:{run_id}"
        self._sequence_by_run[run_id] = sequence_number

    def record_evidence(
        self,
        *,
        evidence_id: str,
        run_id: str,
        claim: str,
        observed: bool | int | float | str | None,
        expected: bool | int | float | str | None,
        comparator: str,
        stage: str,
        capture_policy: str,
        artifact_payload: Mapping[str, object] | None = None,
        artifact_uri: str | None = None,
        artifact_hash: str | None = None,
        extensions: Mapping[str, object] | None = None,
    ) -> None:
        artifact_uri, artifact_hash = self._materialize_artifact(
            artifact_payload, artifact_uri, artifact_hash, capture_policy
        )
        evidence = self._contracts.EvidenceRecord(
            evidence_id=evidence_id,
            run_id=run_id,
            claim=claim,
            observed=observed,
            expected=expected,
            comparator=comparator,
            stage=stage,
            artifact_uri=artifact_uri,
            artifact_hash=artifact_hash,
            capture_policy=self._capture_policy(capture_policy),
            extensions=self._extensions(extensions),
        )
        self._emit("evidence", evidence.to_dict(), "evidence")

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
        reasons: Sequence[tuple[str, str]] = (),
        decided_at: str | None = None,
        extensions: Mapping[str, object] | None = None,
    ) -> None:
        normalized = {"approved": "approve", "blocked": "block"}.get(decision, decision)
        if normalized == "pending":
            return
        arp_reasons = tuple(self._contracts.GateReason(code, message) for code, message in reasons)
        if normalized == "block" and not arp_reasons:
            arp_reasons = (self._contracts.GateReason("delivery_blocked", "The delivery gate was not satisfied."),)
        gate_decision = self._contracts.GateDecisionV3(
            gate_id=gate_id,
            run_id=run_id,
            checkpoint=checkpoint,
            decision=normalized,
            decided_at=decided_at or self._now(),
            policy_version=policy_version,
            decision_authority=decision_authority,
            reasons=arp_reasons,
            evidence_ids=tuple(evidence_ids),
            capture_policy=self._capture_policy(capture_policy),
            extensions=self._extensions(extensions),
        )
        self._emit("gate.decision", gate_decision.to_dict(), "decision")
        self._emit_lifecycle(run_id, "gate.decided", extensions)
        self._emit_lifecycle(run_id, "episode.completed", extensions)

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
        requested_at: str | None = None,
        extensions: Mapping[str, object] | None = None,
    ) -> None:
        gate_request = self._contracts.GateRequestV3(
            gate_id=gate_id,
            run_id=run_id,
            checkpoint=checkpoint,
            requested_at=requested_at or self._now(),
            policy_version=policy_version,
            required_evidence=tuple(required_evidence_ids),
            decision_authority=decision_authority,
            capture_policy=self._capture_policy(capture_policy),
            extensions=self._extensions(extensions),
        )
        self._emit("gate.request", gate_request.to_dict(), "gate-request")
        self._emit_lifecycle(run_id, "gate.requested", extensions)

    def _emit(self, kind: str, payload: dict[str, object], contract_kind: str) -> None:
        errors = self._contracts.check_contract(contract_kind, payload)
        if errors:
            raise ValueError(f"invalid ARP 3.0 {contract_kind}: {'; '.join(errors)}")
        self._sink.write(kind, payload)

    def _emit_lifecycle(
        self,
        run_id: str,
        checkpoint: str,
        extensions: Mapping[str, object] | None,
    ) -> None:
        try:
            episode_id = self._episode_by_run[run_id]
        except KeyError as error:
            raise ValueError(f"ARP run was not recorded before lifecycle event: {run_id}") from error
        sequence = self._sequence_by_run.get(run_id, 0)
        observed_at = self._now()
        event = self._contracts.LifecycleEventV3(
            event_id=f"event:{run_id}:{sequence}:{checkpoint}",
            run_id=run_id,
            episode_id=episode_id,
            sequence_number=sequence,
            checkpoint=checkpoint,
            started_at=observed_at,
            ended_at=observed_at,
            attributes={},
            extensions=self._extensions(extensions),
        )
        self._emit("lifecycle", event.to_dict(), "event")
        self._sequence_by_run[run_id] = sequence + 1

    def _materialize_artifact(
        self,
        payload: Mapping[str, object] | None,
        uri: str | None,
        digest: str | None,
        capture_policy: str,
    ) -> tuple[str | None, str | None]:
        if payload is None and (uri is None or digest is None):
            return uri, digest
        encoded = json.dumps(dict(payload or {}), sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = digest or f"sha256:{hashlib.sha256(encoded).hexdigest()}"
        uri = uri or f"urn:mergewave:artifact:{digest.removeprefix('sha256:')}"
        self._sink.write(
            "artifact",
            {
                "uri": uri,
                "sha256": digest.removeprefix("sha256:"),
                "capture_policy": self._capture_policy(capture_policy),
                "content": dict(payload) if capture_policy == "full" else None,
            },
        )
        return uri, digest

    def _capture_policy(self, value: str) -> str:
        if value not in self._capture_policies:
            raise ValueError(f"unsupported ARP capture policy: {value}")
        return value

    @staticmethod
    def _extensions(value: Mapping[str, object] | None) -> dict[str, dict[str, object]]:
        raw = dict(value or {})
        namespace = raw.pop("software-delivery/v1", None)
        if namespace is not None and not isinstance(namespace, Mapping):
            raise ValueError("software-delivery/v1 extension must be an object")
        delivery = dict(namespace or {})
        delivery.update(raw)
        return {"software-delivery/v1": delivery}

    def _now(self) -> str:
        return self._clock().astimezone(timezone.utc).isoformat()


__all__ = ["Arp3Contracts", "Arp3Recorder", "ArpSink"]
