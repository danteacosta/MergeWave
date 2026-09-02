# MergeWave skill integration

Status: implemented runtime envelope and result binding

MergeWave can carry a versioned engineering-skill identity through a
`RunSpec` and preserve the skill's result as attempt evidence. This is an
adapter boundary, not a second scheduler.

## Invocation

```python
from mergewave import SkillInvocation

skill = SkillInvocation(
    skill="atdd-plan",
    skill_version="0.1.0",
    stage="plan",
    manifest_ref="urn:agentic-skills:manifest:0.1.0",
)
controller.dispatch_ready(
    {"MW-104": "Implement the work item"},
    skill_invocations={"MW-104": skill},
)
```

The controller fixes this identity to the created `WorkAttempt`. It persists
the same envelope in `work_attempt.started` and passes it to ACP providers in
the `session/start` request. The CLI fallback still receives the generic
`RunSpec`; its command transport remains prompt-oriented.

## Result event

A runtime can emit an `AgentEvent` with kind `skill.result` and a payload
matching `agentic-skills/contracts/skill-result.schema.json`:

```json
{
  "schema_version": "1.0",
  "case_id": "mw-104-plan-1",
  "work_item_id": "MW-104",
  "skill": "atdd-plan",
  "skill_version": "0.1.0",
  "status": "completed",
  "summary": "Acceptance scenarios recorded.",
  "evidence": [],
  "findings": [],
  "artifacts": [
    {"uri": "attempts/MW-104/plan.json", "sha256": "sha256:..."}
  ],
  "next_actions": [],
  "metadata": {}
}
```

The controller rejects a malformed result, a result for another work item, or
a result whose skill/version differs from the assigned invocation. A valid
result is stored as `skill.result.recorded` with:

- the runtime `run_id`;
- the current `attempt_id` and `workspace_id`;
- the normalized result;
- artifact bindings, including stable artifact IDs and optional hashes.

Result events are idempotent. Retrying an item creates a new attempt, so a
result from a superseded attempt cannot be silently attached to its
replacement.

## Authority boundary

Skill output is evidence about planning, implementation, verification, or
review. It does not prove that a pull request is linked, that current-head CI
passed, that reviews are resolved, that scope and ancestry are valid, or that a
human merged into the target base. Those facts remain owned by the delivery
observer and the human gate.

Stage routing across the full `agentic-skills` lifecycle remains a policy layer
above this envelope. The current contract intentionally supports one assigned
skill per work attempt and leaves orchestration policy in the caller.
