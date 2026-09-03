# MergeWave skill integration

Status: implemented strict envelope, authority verification, and lifecycle routing

MergeWave can carry a versioned engineering-skill identity through a
`RunSpec` and preserve the skill's result as attempt evidence. The controller
also verifies manifest provenance, authority scope, and artifact hashes. The
lifecycle adapter is sequential and stage-aware, while delivery authority
remains in the controller.

## Invocation

```python
from mergewave import SkillAuthority, SkillInvocation

skill = SkillInvocation(
    skill="atdd-plan",
    skill_version="0.3.0",
    stage="plan",
    manifest_ref="agentic-skills/.codex/manifest.json",
    manifest_sha256="sha256:<64 lowercase hex characters>",
    authority=SkillAuthority(
        mode="read-only",
        allowed_paths=("src", "tests"),
        allowed_operations=("read", "execute"),
        approved_by="mergewave-policy",
        expires_at="2099-01-01T00:00:00+00:00",
    ),
)
controller.dispatch_ready(
    {"MW-104": "Implement the work item"},
    skill_invocations={"MW-104": skill},
)
```

The controller fixes this identity to the created `WorkAttempt`. It persists
the same envelope in `work_attempt.started`, passes it to ACP providers in the
`session/start` request, and exposes it to CLI providers through JSON/event
and environment-variable transport.

## Result event

A runtime can emit an `AgentEvent` with kind `skill.result` and a payload
matching `agentic-skills/contracts/skill-result-event.schema.json`:

```json
{
  "event_id": "event:skill:1",
  "run_id": "MW-104:main-0",
  "invocation_id": "invocation:attempt:MW-104:main-0:plan:atdd-plan",
  "attempt_id": "attempt:MW-104:main-0",
  "workspace_id": "workspace-MW-104",
  "result": {
    "schema_version": "1.0",
    "result_id": "result:skill:1",
    "invocation_id": "invocation:attempt:MW-104:main-0:plan:atdd-plan",
    "attempt_id": "attempt:MW-104:main-0",
    "case_id": "mw-104-plan-1",
    "work_item_id": "MW-104",
    "stage": "plan",
    "skill": "atdd-plan",
    "skill_version": "0.3.0",
    "status": "completed",
    "summary": "Acceptance scenarios recorded.",
    "evidence": [{"kind": "file", "locator": "docs/plan.md", "note": "Plan artifact."}],
    "findings": [],
    "artifacts": [{"uri": "docs/plan.md", "sha256": "sha256:<64 lowercase hex characters>"}],
    "next_actions": [],
    "metadata": {}
  }
}
```

The controller rejects a malformed result, a result for another work item or
attempt, a stale event from a superseded retry, expired authority, an
unverified manifest, blank evidence, or an artifact outside the assigned
workspace. A valid result is stored as `skill.result.recorded` with:

- the runtime `run_id`;
- the current `attempt_id` and `workspace_id`;
- the normalized result;
- artifact bindings, including content-addressed IDs, hashes, and verified scope.

Result events are idempotent. Retrying an item creates a new attempt, so a
result from a superseded attempt cannot be silently attached to its
replacement.

## Authority boundary

Skill output is evidence about planning, implementation, verification, or
review. It does not prove that a pull request is linked, that current-head CI
passed, that reviews are resolved, that scope and ancestry are valid, or that a
human merged into the target base. Those facts remain owned by the delivery
observer and the human gate.

`LifecycleAgentRuntime` runs the shared nine-stage lifecycle through the same
agent runtime port. It creates a fresh invocation per stage, keeps the same
attempt/workspace, routes `debug` only when a prior stage fails, and emits
`skill.stage_skipped` when a conditional stage is not required. Configure
stage-specific authorities explicitly when a pipeline moves from read-only
analysis to mutation.
