"""Deterministic lifecycle routing and a sequential skill-pipeline adapter."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
import hashlib
import json
import re
from typing import cast

from .runtime import AgentEvent, AgentRuntime, RunHandle, RunSpec, RuntimeCapabilities
from .skills import SkillAuthority, SkillInvocation, SkillResult, SkillResultEvent


_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_RUN_CONDITIONS = frozenset(
    {"always", "after_previous", "on_failure", "after_implementation", "after_verify", "after_review", "after_release"}
)


@dataclass(frozen=True)
class LifecycleStage:
    id: str
    skills: tuple[str, ...]
    mode: str
    run_if: str
    required_output_fields: tuple[str, ...] = ("summary", "evidence", "findings", "next_actions")

    def __post_init__(self) -> None:
        if not _NAME.fullmatch(self.id):
            raise ValueError(f"invalid lifecycle stage id: {self.id!r}")
        if not self.skills or any(not _NAME.fullmatch(skill) for skill in self.skills):
            raise ValueError(f"lifecycle stage {self.id!r} needs normalized skills")
        if self.run_if not in _RUN_CONDITIONS:
            raise ValueError(f"invalid lifecycle run_if: {self.run_if!r}")
        if not self.required_output_fields:
            raise ValueError(f"lifecycle stage {self.id!r} needs output fields")

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "LifecycleStage":
        skills = payload.get("skills")
        fields = payload.get("required_output_fields")
        if not isinstance(skills, list) or not all(isinstance(skill, str) for skill in skills):
            raise ValueError("lifecycle stage skills must be a list of strings")
        if not isinstance(fields, list) or not all(isinstance(field, str) for field in fields):
            raise ValueError("lifecycle stage required_output_fields must be a list of strings")
        return cls(
            id=str(payload.get("id", "")),
            skills=tuple(skills),
            mode=str(payload.get("mode", "")),
            run_if=str(payload.get("run_if", "")),
            required_output_fields=tuple(fields),
        )


DEFAULT_LIFECYCLE_STAGES = (
    LifecycleStage("intake", ("intake", "work-prep"), "read-only", "always"),
    LifecycleStage("explore", ("codebase-explore",), "read-only", "after_previous"),
    LifecycleStage("plan", ("atdd-plan", "testing-philosophy"), "read-only", "after_previous"),
    LifecycleStage("implement-test", ("tdd-writer",), "mutating-with-authority", "after_previous"),
    LifecycleStage("debug", ("debug-hunt",), "read-only", "on_failure"),
    LifecycleStage("verify", ("bug-bash", "smoke-test"), "read-only-with-disposable-data", "after_implementation"),
    LifecycleStage(
        "review",
        ("implementation-review", "security-audit", "ux-audit", "design-review", "interaction-polish"),
        "read-only",
        "after_verify",
    ),
    LifecycleStage("release", ("release-check", "staleness-audit"), "read-only", "after_review"),
    LifecycleStage("wrap-up", ("wrap-up",), "read-only", "after_release"),
)


@dataclass(frozen=True)
class StageDecision:
    action: str
    stage: LifecycleStage
    skill: str
    reason: str


class LifecycleRouter:
    """Choose the next stage from append-only results and explicit skips."""

    def __init__(
        self,
        stages: Sequence[LifecycleStage] = DEFAULT_LIFECYCLE_STAGES,
        *,
        selected_skills: Mapping[str, str] | None = None,
    ) -> None:
        self._stages = tuple(stages)
        if not self._stages or len({stage.id for stage in self._stages}) != len(self._stages):
            raise ValueError("lifecycle stage ids must be unique")
        self._selected_skills = dict(selected_skills or {})
        for stage_id, skill in self._selected_skills.items():
            stage = next((item for item in self._stages if item.id == stage_id), None)
            if stage is None or skill not in stage.skills:
                raise ValueError(f"selected skill {skill!r} is not available in stage {stage_id!r}")

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, object],
        *,
        selected_skills: Mapping[str, str] | None = None,
    ) -> "LifecycleRouter":
        stages = payload.get("stages")
        if not isinstance(stages, list) or any(not isinstance(stage, Mapping) for stage in stages):
            raise ValueError("lifecycle payload needs a list of stage objects")
        return cls(
            tuple(LifecycleStage.from_payload(stage) for stage in stages),
            selected_skills=selected_skills,
        )

    @property
    def stages(self) -> tuple[LifecycleStage, ...]:
        return self._stages

    def next(
        self,
        results: Sequence[SkillResult],
        *,
        skipped_stages: Iterable[str] = (),
        failure: bool = False,
    ) -> StageDecision | None:
        completed = {result.stage for result in results if result.status == "completed"}
        reported_skips = {result.stage for result in results if result.status == "skipped"}
        known = completed | reported_skips | set(skipped_stages)
        result_by_stage = {result.stage: result for result in results}
        for index, stage in enumerate(self._stages):
            if stage.id in known:
                continue
            selected_skill = self._selected_skills.get(stage.id, stage.skills[0])
            existing_result = result_by_stage.get(stage.id)
            if existing_result is not None and existing_result.status != "completed":
                if failure and stage.run_if != "on_failure":
                    continue
                return StageDecision("blocked", stage, selected_skill, "stage result is not completed")
            if stage.run_if == "on_failure":
                if failure:
                    return StageDecision("run", stage, selected_skill, "failure evidence requires diagnostic stage")
                return StageDecision("skip", stage, selected_skill, "no failure evidence requires this stage")
            if stage.run_if == "after_previous" and index > 0 and self._stages[index - 1].id not in known:
                return StageDecision("blocked", stage, selected_skill, "previous lifecycle stage is incomplete")
            required = {
                "after_implementation": "implement-test",
                "after_verify": "verify",
                "after_review": "review",
                "after_release": "release",
            }.get(stage.run_if)
            if required is not None and required not in known:
                return StageDecision("blocked", stage, selected_skill, f"required stage {required!r} is incomplete")
            return StageDecision("run", stage, selected_skill, "stage is ready")
        return None

    def invocation_for(
        self,
        decision: StageDecision,
        *,
        template: SkillInvocation,
        work_item_id: str,
        attempt_id: str,
        authority: SkillAuthority | None = None,
    ) -> SkillInvocation:
        selected_authority = authority or template.authority
        if selected_authority is None:
            raise ValueError(f"lifecycle stage {decision.stage.id!r} requires explicit authority")
        if selected_authority.mode != decision.stage.mode:
            raise ValueError(
                f"authority mode {selected_authority.mode!r} does not match stage {decision.stage.id!r} "
                f"mode {decision.stage.mode!r}"
            )
        invocation = SkillInvocation(
            skill=decision.skill,
            skill_version=template.skill_version,
            stage=decision.stage.id,
            manifest_ref=template.manifest_ref,
            manifest_sha256=template.manifest_sha256,
            authority=selected_authority,
        )
        return invocation.bind(work_item_id=work_item_id, attempt_id=attempt_id)


@dataclass
class _LifecycleSession:
    spec: RunSpec
    router: LifecycleRouter
    results: list[SkillResult]
    skipped_stages: set[str]
    current_invocation: SkillInvocation
    current_inner_handle: RunHandle
    current_decision: StageDecision
    current_started: bool = False
    finished: bool = False


PromptBuilder = Callable[[RunSpec, LifecycleStage, Sequence[SkillResult]], str]


class LifecycleAgentRuntime:
    """Run the lifecycle stages sequentially through one AgentRuntime port.

    The wrapper preserves one outer MergeWave run and workspace while each
    stage receives a fresh, source-bound invocation.  It is intentionally an
    adapter: scheduler and delivery authority remain in MergeWave's controller.
    """

    def __init__(
        self,
        delegate: AgentRuntime,
        *,
        stages: Sequence[LifecycleStage] = DEFAULT_LIFECYCLE_STAGES,
        selected_skills: Mapping[str, str] | None = None,
        prompt_builder: PromptBuilder | None = None,
        stage_authorities: Mapping[str, SkillAuthority] | None = None,
    ) -> None:
        self._delegate = delegate
        self._stages = tuple(stages)
        self._selected_skills = dict(selected_skills or {})
        self._prompt_builder = prompt_builder or self._default_prompt
        self._stage_authorities = dict(stage_authorities or {})

    def start(self, spec: RunSpec) -> RunHandle:
        if spec.skill is None:
            raise ValueError("LifecycleAgentRuntime requires an initial skill invocation template")
        router = LifecycleRouter(self._stages, selected_skills=self._selected_skills)
        decision = router.next(())
        if decision is None or decision.action != "run":
            raise ValueError("lifecycle has no dispatchable initial stage")
        if decision.stage.id != spec.skill.stage:
            raise ValueError(f"initial invocation stage must be {decision.stage.id!r}")
        invocation = spec.skill
        authority = self._stage_authorities.get(decision.stage.id, invocation.authority)
        if authority is None or authority.mode != decision.stage.mode:
            raise ValueError(
                f"initial lifecycle stage {decision.stage.id!r} requires authority mode {decision.stage.mode!r}"
            )
        if not invocation.is_bound:
            invocation = invocation.bind(
                work_item_id=spec.work_item_id,
                attempt_id=f"attempt:{spec.run_id}",
                authority=authority,
            )
        if invocation.skill != decision.skill:
            raise ValueError(f"initial invocation skill must be {decision.skill!r}")
        if invocation.authority != authority:
            invocation = SkillInvocation(
                skill=invocation.skill,
                skill_version=invocation.skill_version,
                stage=invocation.stage,
                manifest_ref=invocation.manifest_ref,
                manifest_sha256=invocation.manifest_sha256,
                invocation_id=invocation.invocation_id,
                work_item_id=invocation.work_item_id,
                attempt_id=invocation.attempt_id,
                authority=authority,
            )
        inner = self._start_inner(spec, invocation, decision, ())
        session = _LifecycleSession(spec, router, [], set(), invocation, inner, decision)
        return RunHandle(spec.run_id, session)

    def stream(self, handle: RunHandle) -> Iterable[AgentEvent]:
        session = cast(_LifecycleSession, handle.runtime_ref)
        while not session.finished:
            if not session.current_started:
                session.current_started = True
                yield AgentEvent(
                    "skill.stage_started",
                    {
                        "run_id": handle.run_id,
                        "invocation": session.current_invocation.to_payload(),
                        "attempt_id": session.current_invocation.attempt_id,
                        "workspace_id": self._workspace_id(session.spec),
                    },
                )
            stage_result: SkillResult | None = None
            stage_exited = False
            try:
                for event in self._delegate.stream(session.current_inner_handle):
                    if event.kind == "skill.result":
                        try:
                            stage_result = self._normalize_result(event.payload, handle.run_id, session)
                        except ValueError as error:
                            yield AgentEvent("skill.result.rejected", {"error": str(error), "stage": session.current_decision.stage.id})
                            yield AgentEvent("runtime.exited", {"returncode": 1, "error": str(error)})
                            session.finished = True
                            return
                        yield AgentEvent(
                            "skill.result",
                            SkillResultEvent(
                                event_id=f"event:{handle.run_id}:{stage_result.result_id}",
                                run_id=handle.run_id,
                                invocation_id=session.current_invocation.invocation_id or "",
                                attempt_id=session.current_invocation.attempt_id or "",
                                workspace_id=self._workspace_id(session.spec),
                                result=stage_result,
                            ).to_payload(),
                        )
                        continue
                    if event.kind == "runtime.exited":
                        stage_exited = True
                        return_events = self._advance_after_stage(
                            handle.run_id,
                            session,
                            stage_result,
                            event,
                        )
                        for return_event in return_events:
                            yield return_event
                            if return_event.kind == "runtime.exited":
                                session.finished = True
                                return
                        if not session.finished and session.current_decision.action == "run":
                            break
                        continue
                    yield event
                if not stage_exited and not session.finished:
                    return_events = self._advance_after_stage(
                        handle.run_id,
                        session,
                        stage_result,
                        AgentEvent("runtime.exited", {"returncode": 0}),
                    )
                    for return_event in return_events:
                        yield return_event
                        if return_event.kind == "runtime.exited":
                            session.finished = True
                            return
            except Exception as error:
                yield AgentEvent("runtime.exited", {"returncode": 1, "error": str(error)})
                session.finished = True
                return

    def continue_run(self, handle: RunHandle, input: str) -> None:
        session = cast(_LifecycleSession, handle.runtime_ref)
        self._delegate.continue_run(session.current_inner_handle, input)

    def cancel(self, handle: RunHandle) -> AgentEvent:
        session = cast(_LifecycleSession, handle.runtime_ref)
        return self._delegate.cancel(session.current_inner_handle)

    def capabilities(self) -> RuntimeCapabilities:
        capabilities = self._delegate.capabilities()
        return replace(capabilities, supports_reattach=False)

    def snapshot(self, handle: RunHandle) -> Mapping[str, object]:
        session = cast(_LifecycleSession, handle.runtime_ref)
        return {
            "lifecycle": True,
            "stage": session.current_decision.stage.id,
            "invocation": session.current_invocation.to_payload(),
            "results": [result.to_payload() for result in session.results],
            "skipped_stages": sorted(session.skipped_stages),
        }

    def reattach(self, run_id: str, snapshot: Mapping[str, object]) -> RunHandle:
        raise RuntimeError("LifecycleAgentRuntime does not support reattach; restart the lifecycle explicitly")

    def _start_inner(
        self,
        spec: RunSpec,
        invocation: SkillInvocation,
        decision: StageDecision,
        prior_results: Sequence[SkillResult],
    ) -> RunHandle:
        prompt = self._prompt_builder(spec, decision.stage, prior_results)
        inner_spec = replace(
            spec,
            run_id=f"{spec.run_id}:stage:{decision.stage.id}:{len(prior_results) + 1}",
            prompt=prompt,
            skill=invocation,
        )
        return self._delegate.start(inner_spec)

    def _normalize_result(
        self,
        payload: Mapping[str, object],
        outer_run_id: str,
        session: _LifecycleSession,
    ) -> SkillResult:
        result = SkillResult.from_event_payload(payload)
        invocation = session.current_invocation
        if result.invocation_id != invocation.invocation_id:
            raise ValueError("skill result invocation_id does not match the active stage")
        if result.attempt_id != invocation.attempt_id:
            raise ValueError("skill result attempt_id does not match the active attempt")
        if result.work_item_id != session.spec.work_item_id:
            raise ValueError("skill result work_item_id does not match the active work item")
        if result.stage != session.current_decision.stage.id or result.skill != invocation.skill:
            raise ValueError("skill result stage or skill does not match the active stage")
        return result

    def _advance_after_stage(
        self,
        outer_run_id: str,
        session: _LifecycleSession,
        result: SkillResult | None,
        exit_event: AgentEvent,
    ) -> tuple[AgentEvent, ...]:
        returncode = exit_event.payload.get("returncode", 0)
        if returncode not in {0, None}:
            return (AgentEvent("runtime.exited", {**exit_event.payload, "returncode": returncode}),)
        if result is None:
            return (
                AgentEvent(
                    "runtime.exited",
                    {"returncode": 1, "error": f"stage {session.current_decision.stage.id} exited without skill.result"},
                ),
            )
        session.results.append(result)
        failure = result.status in {"blocked", "failed", "needs_input"}
        decision = session.router.next(session.results, skipped_stages=session.skipped_stages, failure=failure)
        output_events = [
            AgentEvent(
                "skill.stage_completed",
                {"run_id": outer_run_id, "stage": result.stage, "status": result.status},
            )
        ]
        while decision is not None and decision.action == "skip":
            session.skipped_stages.add(decision.stage.id)
            output_events.append(
                AgentEvent(
                    "skill.stage_skipped",
                    {"run_id": outer_run_id, "stage": decision.stage.id, "reason": decision.reason},
                )
            )
            decision = session.router.next(session.results, skipped_stages=session.skipped_stages)
        if decision is None:
            session.finished = True
            output_events.append(AgentEvent("runtime.exited", {"returncode": 0}))
            return tuple(output_events)
        if decision.action != "run":
            output_events.append(AgentEvent("runtime.exited", {"returncode": 1, "error": decision.reason}))
            return tuple(output_events)
        template = session.current_invocation
        next_invocation = session.router.invocation_for(
            decision,
            template=template,
            work_item_id=session.spec.work_item_id,
            attempt_id=template.attempt_id or "",
            authority=self._stage_authorities.get(decision.stage.id),
        )
        session.current_decision = decision
        session.current_invocation = next_invocation
        session.current_inner_handle = self._start_inner(session.spec, next_invocation, decision, session.results)
        session.current_started = False
        return tuple(output_events)

    @staticmethod
    def _default_prompt(spec: RunSpec, stage: LifecycleStage, prior_results: Sequence[SkillResult]) -> str:
        return json.dumps(
            {
                "stage": stage.id,
                "skill": stage.skills[0],
                "work_item_id": spec.work_item_id,
                "original_prompt": spec.prompt,
                "prior_results": [
                    {"stage": result.stage, "status": result.status, "summary": result.summary}
                    for result in prior_results
                ],
            },
            sort_keys=True,
        )

    @staticmethod
    def _workspace_id(spec: RunSpec) -> str:
        if spec.workspace is not None:
            return spec.workspace.workspace_id
        digest = hashlib.sha256(spec.workspace_path.encode("utf-8")).hexdigest()[:16]
        return f"workspace:{digest}"


__all__ = [
    "DEFAULT_LIFECYCLE_STAGES",
    "LifecycleAgentRuntime",
    "LifecycleRouter",
    "LifecycleStage",
    "StageDecision",
]
