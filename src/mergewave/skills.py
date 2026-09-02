"""Versioned skill invocations and result artifacts at the runtime boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
import re


SKILL_RESULT_SCHEMA_VERSION = "1.0"
SKILL_RESULT_STATUSES = frozenset({"completed", "blocked", "failed", "needs_input", "skipped"})
_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True)
class SkillInvocation:
    """The immutable skill identity assigned to one runtime attempt."""

    skill: str
    skill_version: str
    stage: str
    manifest_ref: str | None = None
    manifest_sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.skill, str) or not _SKILL_NAME.fullmatch(self.skill):
            raise ValueError("skill must be a normalized lowercase kebab-case name")
        if not isinstance(self.skill_version, str) or not self.skill_version.strip():
            raise ValueError("skill_version cannot be empty")
        if not isinstance(self.stage, str) or not self.stage.strip():
            raise ValueError("skill stage cannot be empty")
        if self.manifest_ref is not None and not self.manifest_ref.strip():
            raise ValueError("manifest_ref cannot be blank")
        if self.manifest_sha256 is not None and not self.manifest_sha256.strip():
            raise ValueError("manifest_sha256 cannot be blank")

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "skill": self.skill,
            "skill_version": self.skill_version,
            "stage": self.stage,
        }
        if self.manifest_ref is not None:
            payload["manifest_ref"] = self.manifest_ref
        if self.manifest_sha256 is not None:
            payload["manifest_sha256"] = self.manifest_sha256
        return payload

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "SkillInvocation":
        return cls(
            skill=str(payload.get("skill", "")),
            skill_version=str(payload.get("skill_version", "")),
            stage=str(payload.get("stage", "")),
            manifest_ref=str(payload["manifest_ref"]) if payload.get("manifest_ref") else None,
            manifest_sha256=str(payload["manifest_sha256"]) if payload.get("manifest_sha256") else None,
        )


@dataclass(frozen=True)
class SkillArtifact:
    """A result artifact reference, optionally content-addressed."""

    uri: str
    sha256: str | None = None
    media_type: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.uri, str) or not self.uri.strip():
            raise ValueError("skill artifact uri cannot be empty")
        if self.sha256 is not None and not self.sha256.strip():
            raise ValueError("skill artifact sha256 cannot be blank")
        if self.media_type is not None and not self.media_type.strip():
            raise ValueError("skill artifact media_type cannot be blank")

    @property
    def artifact_id(self) -> str:
        encoded = json.dumps(
            {"uri": self.uri, "sha256": self.sha256, "media_type": self.media_type},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"artifact:{hashlib.sha256(encoded).hexdigest()}"

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {"uri": self.uri}
        if self.sha256 is not None:
            payload["sha256"] = self.sha256
        if self.media_type is not None:
            payload["media_type"] = self.media_type
        return payload

    @classmethod
    def from_payload(cls, payload: object) -> "SkillArtifact":
        if isinstance(payload, str):
            return cls(payload)
        if not isinstance(payload, Mapping):
            raise ValueError("skill artifact must be a string or object")
        uri = payload.get("uri", payload.get("ref"))
        if not isinstance(uri, str):
            raise ValueError("skill artifact object requires uri")
        sha256 = payload.get("sha256")
        media_type = payload.get("media_type")
        if sha256 is not None and not isinstance(sha256, str):
            raise ValueError("skill artifact sha256 must be a string")
        if media_type is not None and not isinstance(media_type, str):
            raise ValueError("skill artifact media_type must be a string")
        return cls(uri, sha256, media_type)


@dataclass(frozen=True)
class SkillResult:
    """A normalized result emitted by a skill and bound by the controller."""

    schema_version: str
    case_id: str
    work_item_id: str
    skill: str
    skill_version: str
    status: str
    summary: str
    evidence: tuple[Mapping[str, object], ...] = ()
    findings: tuple[Mapping[str, object], ...] = ()
    artifacts: tuple[SkillArtifact, ...] = ()
    next_actions: tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != SKILL_RESULT_SCHEMA_VERSION:
            raise ValueError(f"unsupported skill result schema_version: {self.schema_version!r}")
        if not self.case_id.strip() or not self.work_item_id.strip():
            raise ValueError("skill result case_id and work_item_id are required")
        if not _SKILL_NAME.fullmatch(self.skill):
            raise ValueError("skill result skill must be a normalized lowercase kebab-case name")
        if not self.skill_version.strip():
            raise ValueError("skill result skill_version cannot be empty")
        if self.status not in SKILL_RESULT_STATUSES:
            raise ValueError(f"unsupported skill result status: {self.status!r}")
        if not self.summary.strip():
            raise ValueError("skill result summary cannot be empty")
        if any(not isinstance(item, Mapping) for item in (*self.evidence, *self.findings)):
            raise ValueError("skill result evidence and findings must contain objects")
        if any(not isinstance(action, str) or not action.strip() for action in self.next_actions):
            raise ValueError("skill result next_actions must contain non-empty strings")
        if not isinstance(self.metadata, Mapping):
            raise ValueError("skill result metadata must be an object")

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "SkillResult":
        if not isinstance(payload, Mapping):
            raise ValueError("skill result must be an object")

        def object_tuple(field_name: str) -> tuple[Mapping[str, object], ...]:
            value = payload.get(field_name, [])
            if not isinstance(value, list):
                raise ValueError(f"skill result {field_name} must be a list")
            if any(not isinstance(item, Mapping) for item in value):
                raise ValueError(f"skill result {field_name} must contain objects")
            return tuple(dict(item) for item in value)

        artifacts = payload.get("artifacts", [])
        if not isinstance(artifacts, list):
            raise ValueError("skill result artifacts must be a list")
        next_actions = payload.get("next_actions", [])
        if not isinstance(next_actions, list) or any(
            not isinstance(action, str) for action in next_actions
        ):
            raise ValueError("skill result next_actions must be a list of strings")
        metadata = payload.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise ValueError("skill result metadata must be an object")

        return cls(
            schema_version=str(payload.get("schema_version", "")),
            case_id=str(payload.get("case_id", "")),
            work_item_id=str(payload.get("work_item_id", "")),
            skill=str(payload.get("skill", "")),
            skill_version=str(payload.get("skill_version", "")),
            status=str(payload.get("status", "")),
            summary=str(payload.get("summary", "")),
            evidence=object_tuple("evidence"),
            findings=object_tuple("findings"),
            artifacts=tuple(SkillArtifact.from_payload(item) for item in artifacts),
            next_actions=tuple(next_actions),
            metadata=dict(metadata),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "work_item_id": self.work_item_id,
            "skill": self.skill,
            "skill_version": self.skill_version,
            "status": self.status,
            "summary": self.summary,
            "evidence": [dict(item) for item in self.evidence],
            "findings": [dict(item) for item in self.findings],
            "artifacts": [artifact.to_payload() for artifact in self.artifacts],
            "next_actions": list(self.next_actions),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_event_payload(cls, payload: Mapping[str, object]) -> "SkillResult":
        nested = payload.get("result")
        if isinstance(nested, Mapping):
            return cls.from_payload(nested)
        return cls.from_payload(payload)


__all__ = [
    "SKILL_RESULT_SCHEMA_VERSION",
    "SKILL_RESULT_STATUSES",
    "SkillArtifact",
    "SkillInvocation",
    "SkillResult",
]
