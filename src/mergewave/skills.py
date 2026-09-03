"""Strict, source-bound skill invocations and result artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Protocol
from urllib.parse import urlsplit


SKILL_RESULT_SCHEMA_VERSION = "1.0"
SKILL_INVOCATION_SCHEMA_VERSION = "1.0"
SKILL_PACK_VERSION = "0.3.0"
SKILL_RESULT_STATUSES = frozenset({"completed", "blocked", "failed", "needs_input", "skipped"})
SKILL_AUTHORITY_MODES = frozenset({"read-only", "mutating-with-authority", "read-only-with-disposable-data"})
SKILL_AUTHORITY_OPERATIONS = frozenset({"read", "write", "execute", "network"})
_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_EXTERNAL_SCHEMES = frozenset({"https", "s3", "urn", "artifact"})
_EVIDENCE_KEYS = {"kind", "locator", "note"}
_FINDING_KEYS = {"id", "severity", "title", "confidence", "evidence", "recommendation", "blocking"}
_SEVERITIES = frozenset({"critical", "high", "medium", "low", "info"})


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _id(value: object, field: str) -> str:
    value = _string(value, field)
    if not _ID.fullmatch(value):
        raise ValueError(f"{field} contains unsupported characters")
    return value


def normalize_sha256(value: object, field: str = "sha256") -> str:
    value = _string(value, field).lower()
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{field} must match sha256:<64 lowercase hex characters>")
    return value


def _scope_path(value: object, field: str) -> str:
    value = _string(value, field).replace("\\", "/")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\x00" in value:
        raise ValueError(f"{field} must stay inside the workspace")
    return value.rstrip("/") or "."


def validate_artifact_uri(value: object) -> str:
    uri = _string(value, "artifact uri")
    if "\x00" in uri:
        raise ValueError("artifact uri cannot contain NUL")
    parsed = urlsplit(uri)
    if parsed.scheme:
        if parsed.scheme not in _EXTERNAL_SCHEMES:
            raise ValueError(f"unsupported artifact URI scheme: {parsed.scheme}")
        return uri
    _scope_path(uri, "artifact uri")
    return uri.replace("\\", "/")


@dataclass(frozen=True)
class SkillAuthority:
    """A controller-issued capability envelope for one skill invocation."""

    mode: str
    allowed_paths: tuple[str, ...]
    allowed_operations: tuple[str, ...]
    approved_by: str
    expires_at: str
    policy_ref: str | None = None

    def __post_init__(self) -> None:
        if self.mode not in SKILL_AUTHORITY_MODES:
            raise ValueError(f"unsupported skill authority mode: {self.mode!r}")
        if not self.allowed_paths:
            raise ValueError("skill authority needs at least one allowed path")
        paths = tuple(_scope_path(path, "allowed path") for path in self.allowed_paths)
        if len(paths) != len(set(paths)):
            raise ValueError("skill authority allowed paths must be unique")
        operations = tuple(_string(operation, "allowed operation") for operation in self.allowed_operations)
        if not operations or not set(operations) <= SKILL_AUTHORITY_OPERATIONS:
            raise ValueError("skill authority has unsupported operations")
        if len(operations) != len(set(operations)):
            raise ValueError("skill authority allowed operations must be unique")
        _string(self.approved_by, "approved_by")
        _string(self.expires_at, "expires_at")
        try:
            expires_at = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("expires_at must be an ISO-8601 timestamp") from error
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError("expires_at must include a timezone")
        if self.mode == "read-only" and "write" in operations:
            raise ValueError("read-only authority cannot allow write operations")
        if self.mode == "mutating-with-authority" and "write" not in operations:
            raise ValueError("mutating authority must allow write operations")
        if self.policy_ref is not None:
            _string(self.policy_ref, "policy_ref")
        object.__setattr__(self, "allowed_paths", paths)
        object.__setattr__(self, "allowed_operations", operations)

    @property
    def expires_datetime(self) -> datetime:
        return datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))

    def is_expired(self, *, now: datetime | None = None) -> bool:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("now must include a timezone")
        return self.expires_datetime <= current

    def allows(self, path: str, operation: str) -> bool:
        if operation not in self.allowed_operations:
            return False
        normalized = _scope_path(path, "path")
        return any(
            allowed == "." or normalized == allowed or normalized.startswith(f"{allowed}/")
            for allowed in self.allowed_paths
        )

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "mode": self.mode,
            "allowed_paths": list(self.allowed_paths),
            "allowed_operations": list(self.allowed_operations),
            "approved_by": self.approved_by,
            "expires_at": self.expires_at,
        }
        if self.policy_ref is not None:
            payload["policy_ref"] = self.policy_ref
        return payload

    @classmethod
    def from_payload(cls, payload: object) -> "SkillAuthority":
        if not isinstance(payload, Mapping):
            raise ValueError("skill authority must be an object")
        required = {"mode", "allowed_paths", "allowed_operations", "approved_by", "expires_at"}
        if not required <= set(payload):
            raise ValueError("skill authority is missing required fields")
        unexpected = sorted(set(payload) - required - {"policy_ref"})
        if unexpected:
            raise ValueError(f"skill authority has unexpected fields: {unexpected}")
        allowed_paths = payload["allowed_paths"]
        allowed_operations = payload["allowed_operations"]
        if not isinstance(allowed_paths, list) or not all(isinstance(path, str) for path in allowed_paths):
            raise ValueError("allowed_paths must be a list of strings")
        if not isinstance(allowed_operations, list) or not all(isinstance(op, str) for op in allowed_operations):
            raise ValueError("allowed_operations must be a list of strings")
        policy_ref = payload.get("policy_ref")
        if policy_ref is not None and not isinstance(policy_ref, str):
            raise ValueError("policy_ref must be a string")
        return cls(
            mode=_string(payload["mode"], "mode"),
            allowed_paths=tuple(allowed_paths),
            allowed_operations=tuple(allowed_operations),
            approved_by=_string(payload["approved_by"], "approved_by"),
            expires_at=_string(payload["expires_at"], "expires_at"),
            policy_ref=policy_ref,
        )


@dataclass(frozen=True)
class SkillInvocation:
    """A skill identity, optionally bound to a concrete attempt."""

    skill: str
    skill_version: str
    stage: str
    manifest_ref: str | None = None
    manifest_sha256: str | None = None
    invocation_id: str | None = None
    work_item_id: str | None = None
    attempt_id: str | None = None
    authority: SkillAuthority | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.skill, str) or not _NAME.fullmatch(self.skill):
            raise ValueError("skill must be a normalized lowercase kebab-case name")
        _string(self.skill_version, "skill_version")
        if not isinstance(self.stage, str) or not _NAME.fullmatch(self.stage):
            raise ValueError("stage must be a normalized lowercase kebab-case name")
        if (self.manifest_ref is None) != (self.manifest_sha256 is None):
            raise ValueError("manifest_ref and manifest_sha256 must be supplied together")
        if self.manifest_ref is not None:
            _string(self.manifest_ref, "manifest_ref")
            normalize_sha256(self.manifest_sha256, "manifest_sha256")
        identity = (self.invocation_id, self.work_item_id, self.attempt_id)
        if any(value is not None for value in identity) and not all(value is not None for value in identity):
            raise ValueError("invocation_id, work_item_id, and attempt_id must be bound together")
        for value, field in zip(identity, ("invocation_id", "work_item_id", "attempt_id")):
            if value is not None:
                _id(value, field)
        if self.authority is not None and not isinstance(self.authority, SkillAuthority):
            raise ValueError("authority must be a SkillAuthority")

    @property
    def is_bound(self) -> bool:
        return self.invocation_id is not None

    def bind(
        self,
        *,
        work_item_id: str,
        attempt_id: str,
        authority: SkillAuthority | None = None,
    ) -> "SkillInvocation":
        _id(work_item_id, "work_item_id")
        _id(attempt_id, "attempt_id")
        selected_authority = authority or self.authority
        if selected_authority is None:
            raise ValueError("a bound skill invocation requires explicit authority")
        invocation_id = f"invocation:{attempt_id}:{self.stage}:{self.skill}"
        return SkillInvocation(
            skill=self.skill,
            skill_version=self.skill_version,
            stage=self.stage,
            manifest_ref=self.manifest_ref,
            manifest_sha256=self.manifest_sha256,
            invocation_id=invocation_id,
            work_item_id=work_item_id,
            attempt_id=attempt_id,
            authority=selected_authority,
        )

    def to_payload(self) -> dict[str, object]:
        if not self.is_bound or self.authority is None or self.manifest_ref is None or self.manifest_sha256 is None:
            raise ValueError("skill invocation payloads must be bound and include authority and manifest provenance")
        payload: dict[str, object] = {
            "schema_version": SKILL_INVOCATION_SCHEMA_VERSION,
            "skill": self.skill,
            "skill_version": self.skill_version,
            "stage": self.stage,
        }
        for field in ("invocation_id", "work_item_id", "attempt_id"):
            value = getattr(self, field)
            if value is not None:
                payload[field] = value
        if self.authority is not None:
            payload["authority"] = self.authority.to_payload()
        if self.manifest_ref is not None:
            payload["manifest_ref"] = self.manifest_ref
            payload["manifest_sha256"] = self.manifest_sha256
        return payload

    @classmethod
    def from_payload(cls, payload: object) -> "SkillInvocation":
        if not isinstance(payload, Mapping):
            raise ValueError("skill invocation must be an object")
        required = {
            "schema_version", "skill", "skill_version", "stage", "invocation_id", "work_item_id",
            "attempt_id", "authority", "manifest_ref", "manifest_sha256",
        }
        if not required <= set(payload):
            raise ValueError("skill invocation is missing required fields")
        unexpected = sorted(set(payload) - required)
        if unexpected:
            raise ValueError(f"skill invocation has unexpected fields: {unexpected}")
        authority_payload = payload.get("authority")
        authority = SkillAuthority.from_payload(authority_payload) if authority_payload is not None else None
        if authority is None:
            raise ValueError("skill invocation authority must be an object")
        if payload.get("schema_version") != SKILL_INVOCATION_SCHEMA_VERSION:
            raise ValueError("unsupported or missing skill invocation schema_version")
        for field in ("skill", "skill_version", "stage", "invocation_id", "work_item_id", "attempt_id", "manifest_ref", "manifest_sha256"):
            if not isinstance(payload.get(field), str):
                raise ValueError(f"skill invocation {field} must be a string")
        return cls(
            skill=_string(payload["skill"], "skill"),
            skill_version=_string(payload["skill_version"], "skill_version"),
            stage=_string(payload["stage"], "stage"),
            manifest_ref=_string(payload["manifest_ref"], "manifest_ref"),
            manifest_sha256=_string(payload["manifest_sha256"], "manifest_sha256"),
            invocation_id=_id(payload["invocation_id"], "invocation_id"),
            work_item_id=_id(payload["work_item_id"], "work_item_id"),
            attempt_id=_id(payload["attempt_id"], "attempt_id"),
            authority=authority,
        )


@dataclass(frozen=True)
class SkillArtifact:
    """A content-addressed artifact reference."""

    uri: str
    sha256: str
    media_type: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "uri", validate_artifact_uri(self.uri))
        object.__setattr__(self, "sha256", normalize_sha256(self.sha256))
        if self.media_type is not None:
            _string(self.media_type, "media_type")

    @property
    def artifact_id(self) -> str:
        return f"artifact:{self.sha256}"

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {"uri": self.uri, "sha256": self.sha256}
        if self.media_type is not None:
            payload["media_type"] = self.media_type
        return payload

    @classmethod
    def from_payload(cls, payload: object) -> "SkillArtifact":
        if not isinstance(payload, Mapping):
            raise ValueError("skill artifact must be a hashed object")
        allowed = {"uri", "sha256", "media_type"}
        unexpected = sorted(set(payload) - allowed)
        if unexpected:
            raise ValueError(f"skill artifact has unexpected fields: {unexpected}")
        if "uri" not in payload or "sha256" not in payload:
            raise ValueError("skill artifact needs uri and sha256")
        media_type = payload.get("media_type")
        if media_type is not None and not isinstance(media_type, str):
            raise ValueError("skill artifact media_type must be a string")
        return cls(
            uri=_string(payload.get("uri"), "artifact uri"),
            sha256=_string(payload.get("sha256"), "artifact sha256"),
            media_type=media_type,
        )


@dataclass(frozen=True)
class SkillResult:
    """A normalized result emitted by a skill and bound by the controller."""

    schema_version: str
    result_id: str
    invocation_id: str
    attempt_id: str
    case_id: str
    work_item_id: str
    stage: str
    skill: str
    skill_version: str
    status: str
    summary: str
    evidence: tuple[Mapping[str, object], ...]
    findings: tuple[Mapping[str, object], ...]
    artifacts: tuple[SkillArtifact, ...]
    next_actions: tuple[str, ...]
    metadata: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.schema_version != SKILL_RESULT_SCHEMA_VERSION:
            raise ValueError(f"unsupported skill result schema_version: {self.schema_version!r}")
        for value, field in (
            (self.result_id, "result_id"),
            (self.invocation_id, "invocation_id"),
            (self.attempt_id, "attempt_id"),
            (self.case_id, "case_id"),
            (self.work_item_id, "work_item_id"),
            (self.summary, "summary"),
            (self.skill_version, "skill_version"),
        ):
            _string(value, field)
        if not _ID.fullmatch(self.result_id) or not _ID.fullmatch(self.invocation_id) or not _ID.fullmatch(self.attempt_id):
            raise ValueError("result identity contains unsupported characters")
        if not _ID.fullmatch(self.case_id) or not _ID.fullmatch(self.work_item_id):
            raise ValueError("result case_id and work_item_id contain unsupported characters")
        if not _NAME.fullmatch(self.stage):
            raise ValueError("skill result stage must be normalized lowercase kebab-case")
        if not _NAME.fullmatch(self.skill):
            raise ValueError("skill result skill must be normalized lowercase kebab-case")
        if self.status not in SKILL_RESULT_STATUSES:
            raise ValueError(f"unsupported skill result status: {self.status!r}")
        if not self.evidence:
            raise ValueError("skill result evidence must be non-empty")
        for index, item in enumerate(self.evidence):
            if not isinstance(item, Mapping) or set(item) != _EVIDENCE_KEYS:
                raise ValueError(f"evidence[{index}] must contain exactly kind, locator, and note")
            for field in _EVIDENCE_KEYS:
                _string(item[field], f"evidence[{index}].{field}")
        for index, finding in enumerate(self.findings):
            if not isinstance(finding, Mapping) or set(finding) != _FINDING_KEYS:
                raise ValueError(f"finding[{index}] has an invalid shape")
            _string(finding["id"], f"finding[{index}].id")
            if finding["severity"] not in _SEVERITIES:
                raise ValueError(f"finding[{index}].severity is invalid")
            _string(finding["title"], f"finding[{index}].title")
            confidence = finding["confidence"]
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
                raise ValueError(f"finding[{index}].confidence must be between 0 and 1")
            references = finding["evidence"]
            if not isinstance(references, list) or not references or any(
                not isinstance(ref, str) or not ref.strip() for ref in references
            ):
                raise ValueError(f"finding[{index}].evidence must contain locators")
            _string(finding["recommendation"], f"finding[{index}].recommendation")
            if not isinstance(finding["blocking"], bool):
                raise ValueError(f"finding[{index}].blocking must be boolean")
        if any(not isinstance(artifact, SkillArtifact) for artifact in self.artifacts):
            raise ValueError("skill result artifacts must contain SkillArtifact values")
        if any(not isinstance(action, str) or not action.strip() for action in self.next_actions):
            raise ValueError("skill result next_actions must contain non-empty strings")
        if not isinstance(self.metadata, Mapping):
            raise ValueError("skill result metadata must be an object")

    @classmethod
    def from_payload(cls, payload: object) -> "SkillResult":
        if not isinstance(payload, Mapping):
            raise ValueError("skill result must be an object")
        required = {
            "schema_version", "result_id", "invocation_id", "attempt_id", "case_id", "work_item_id", "stage",
            "skill", "skill_version", "status", "summary", "evidence", "findings", "artifacts", "next_actions",
        }
        if not required <= set(payload):
            raise ValueError("skill result is missing required fields")
        allowed = required | {"metadata"}
        unexpected = sorted(set(payload) - allowed)
        if unexpected:
            raise ValueError(f"skill result has unexpected fields: {unexpected}")

        def object_tuple(field: str) -> tuple[Mapping[str, object], ...]:
            value = payload[field]
            if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
                raise ValueError(f"skill result {field} must be a list of objects")
            return tuple(dict(item) for item in value)

        artifacts = payload["artifacts"]
        if not isinstance(artifacts, list):
            raise ValueError("skill result artifacts must be a list")
        next_actions = payload["next_actions"]
        if not isinstance(next_actions, list) or any(not isinstance(action, str) for action in next_actions):
            raise ValueError("skill result next_actions must be a list of strings")
        metadata = payload.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise ValueError("skill result metadata must be an object")
        return cls(
            schema_version=_string(payload["schema_version"], "schema_version"),
            result_id=_id(payload["result_id"], "result_id"),
            invocation_id=_id(payload["invocation_id"], "invocation_id"),
            attempt_id=_id(payload["attempt_id"], "attempt_id"),
            case_id=_string(payload["case_id"], "case_id"),
            work_item_id=_string(payload["work_item_id"], "work_item_id"),
            stage=_string(payload["stage"], "stage"),
            skill=_string(payload["skill"], "skill"),
            skill_version=_string(payload["skill_version"], "skill_version"),
            status=_string(payload["status"], "status"),
            summary=_string(payload["summary"], "summary"),
            evidence=object_tuple("evidence"),
            findings=object_tuple("findings"),
            artifacts=tuple(SkillArtifact.from_payload(item) for item in artifacts),
            next_actions=tuple(next_actions),
            metadata=dict(metadata),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "result_id": self.result_id,
            "invocation_id": self.invocation_id,
            "attempt_id": self.attempt_id,
            "case_id": self.case_id,
            "work_item_id": self.work_item_id,
            "stage": self.stage,
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
    def from_event_payload(cls, payload: object) -> "SkillResult":
        if isinstance(payload, Mapping) and isinstance(payload.get("result"), Mapping):
            return cls.from_payload(payload["result"])
        return cls.from_payload(payload)


@dataclass(frozen=True)
class SkillResultEvent:
    """Transport envelope that makes the source attempt non-ambiguous."""

    event_id: str
    run_id: str
    invocation_id: str
    attempt_id: str
    workspace_id: str
    result: SkillResult

    def __post_init__(self) -> None:
        for value, field in (
            (self.event_id, "event_id"),
            (self.run_id, "run_id"),
            (self.invocation_id, "invocation_id"),
            (self.attempt_id, "attempt_id"),
            (self.workspace_id, "workspace_id"),
        ):
            _id(value, field)
        if self.result.invocation_id != self.invocation_id:
            raise ValueError("result invocation_id does not match event envelope")
        if self.result.attempt_id != self.attempt_id:
            raise ValueError("result attempt_id does not match event envelope")

    @classmethod
    def from_payload(cls, payload: object) -> "SkillResultEvent":
        if not isinstance(payload, Mapping):
            raise ValueError("skill result event must be an object")
        required = {"event_id", "run_id", "invocation_id", "attempt_id", "workspace_id", "result"}
        if not required <= set(payload):
            raise ValueError("skill result event is missing source binding")
        unexpected = sorted(set(payload) - required)
        if unexpected:
            raise ValueError(f"skill result event has unexpected fields: {unexpected}")
        if not isinstance(payload["result"], Mapping):
            raise ValueError("skill result event result must be an object")
        return cls(
            event_id=_id(payload["event_id"], "event_id"),
            run_id=_id(payload["run_id"], "run_id"),
            invocation_id=_id(payload["invocation_id"], "invocation_id"),
            attempt_id=_id(payload["attempt_id"], "attempt_id"),
            workspace_id=_id(payload["workspace_id"], "workspace_id"),
            result=SkillResult.from_payload(payload["result"]),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "invocation_id": self.invocation_id,
            "attempt_id": self.attempt_id,
            "workspace_id": self.workspace_id,
            "result": self.result.to_payload(),
        }


class SkillManifestVerifier(Protocol):
    def verify(self, invocation: SkillInvocation) -> None: ...


class FileSkillManifestVerifier:
    """Verify a skill manifest file against the invocation provenance."""

    def __init__(self, manifest_path: str | Path, *, manifest_ref: str | None = None) -> None:
        self._manifest_path = Path(manifest_path)
        self._manifest_ref = manifest_ref

    def verify(self, invocation: SkillInvocation) -> None:
        if invocation.manifest_ref is None or invocation.manifest_sha256 is None:
            raise ValueError("skill invocation has no manifest provenance")
        if self._manifest_ref is not None and invocation.manifest_ref != self._manifest_ref:
            raise ValueError("skill manifest_ref does not match configured manifest")
        try:
            content = self._manifest_path.read_bytes()
        except OSError as error:
            raise ValueError(f"skill manifest cannot be read: {self._manifest_path}") from error
        actual = f"sha256:{hashlib.sha256(content).hexdigest()}"
        if actual != invocation.manifest_sha256:
            raise ValueError("skill manifest sha256 does not match the installed manifest")


class SkillArtifactVerifier(Protocol):
    def verify(self, artifact: SkillArtifact, workspace: object) -> Mapping[str, object]: ...


class WorkspaceAuthorityVerifier(Protocol):
    def capture(self, workspace: object) -> object: ...

    def verify(
        self,
        workspace: object,
        authority: SkillAuthority,
        baseline: object,
    ) -> Mapping[str, object]: ...


class WorkspaceSkillArtifactVerifier:
    """Verify local artifact existence, workspace scope, and content hash."""

    def verify(self, artifact: SkillArtifact, workspace: object) -> Mapping[str, object]:
        parsed = urlsplit(artifact.uri)
        if parsed.scheme:
            raise ValueError("external artifact URI requires an explicit external verifier")
        root = Path(str(workspace.worktree_path)).resolve()
        candidate = (root / PurePosixPath(artifact.uri)).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as error:
            raise ValueError("artifact URI escapes the assigned workspace") from error
        if not candidate.is_file():
            raise ValueError(f"artifact does not exist inside workspace: {artifact.uri}")
        actual = f"sha256:{hashlib.sha256(candidate.read_bytes()).hexdigest()}"
        if actual != artifact.sha256:
            raise ValueError(f"artifact sha256 mismatch for {artifact.uri}")
        return {"verified_scope": "workspace", "observed_sha256": actual}


@dataclass(frozen=True)
class GitAuthorityBaseline:
    head_revision: str
    dirty_paths: frozenset[str]


class GitWorkspaceAuthorityVerifier:
    """Verify that one stage only changed paths covered by its authority."""

    def capture(self, workspace: object) -> GitAuthorityBaseline:
        root = Path(str(workspace.worktree_path)).resolve()
        if not root.is_dir():
            raise ValueError(f"assigned workspace does not exist: {root}")
        return GitAuthorityBaseline(self._git(root, "rev-parse", "HEAD"), frozenset(self._status_paths(root)))

    def verify(
        self,
        workspace: object,
        authority: SkillAuthority,
        baseline: object,
    ) -> Mapping[str, object]:
        if authority.is_expired():
            raise ValueError("skill authority has expired")
        if not isinstance(baseline, GitAuthorityBaseline):
            raise ValueError("authority verifier received an invalid baseline")
        root = Path(str(workspace.worktree_path)).resolve()
        if not root.is_dir():
            raise ValueError(f"assigned workspace does not exist: {root}")
        head_revision = self._git(root, "rev-parse", "HEAD")
        changed = set(self._git_lines(root, "diff", "--name-only", baseline.head_revision, head_revision))
        changed.update(set(self._status_paths(root)) - set(baseline.dirty_paths))
        changed_paths = tuple(sorted(path for path in changed if path))
        if changed_paths:
            if "write" not in authority.allowed_operations:
                raise ValueError("authority forbids workspace writes")
            unauthorized = tuple(path for path in changed_paths if not authority.allows(path, "write"))
            if unauthorized:
                raise ValueError(f"workspace changes exceed authority paths: {list(unauthorized)}")
        return {"observed_head_revision": head_revision, "changed_paths": list(changed_paths)}

    @staticmethod
    def _git(root: Path, *args: str) -> str:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError) as error:
            raise ValueError(f"workspace Git inspection failed: {root}") from error
        return result.stdout.strip()

    @classmethod
    def _git_lines(cls, root: Path, *args: str) -> tuple[str, ...]:
        return tuple(line for line in cls._git(root, *args).splitlines() if line.strip())

    @classmethod
    def _status_paths(cls, root: Path) -> tuple[str, ...]:
        paths: list[str] = []
        for line in cls._git_lines(root, "status", "--porcelain", "--untracked-files=all"):
            raw = line[3:] if len(line) >= 4 else ""
            if " -> " in raw:
                raw = raw.rsplit(" -> ", 1)[1]
            if raw:
                paths.append(raw.replace("\\", "/"))
        return tuple(paths)


__all__ = [
    "FileSkillManifestVerifier",
    "SKILL_AUTHORITY_MODES",
    "SKILL_AUTHORITY_OPERATIONS",
    "SKILL_INVOCATION_SCHEMA_VERSION",
    "SKILL_PACK_VERSION",
    "SKILL_RESULT_SCHEMA_VERSION",
    "SKILL_RESULT_STATUSES",
    "SkillArtifact",
    "SkillArtifactVerifier",
    "SkillAuthority",
    "SkillInvocation",
    "SkillManifestVerifier",
    "SkillResult",
    "SkillResultEvent",
    "GitAuthorityBaseline",
    "GitWorkspaceAuthorityVerifier",
    "WorkspaceAuthorityVerifier",
    "WorkspaceSkillArtifactVerifier",
    "normalize_sha256",
    "validate_artifact_uri",
]
