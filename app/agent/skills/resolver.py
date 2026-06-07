from __future__ import annotations

from typing import Any

from app.agent.skills.models import ActiveSkillSet, SkillPromptBlock, SkillSpec
from app.agent.skills.loader import load_skill_body


class SkillResolver:
    def __init__(self, skills: list[SkillSpec], *, max_active: int = 3) -> None:
        self.skills = skills
        self.max_active = max_active

    def resolve(self, state: Any, context: dict[str, Any] | None = None) -> ActiveSkillSet:
        context = context or {}
        message = str(getattr(state, "message", "") or context.get("user_message") or "")
        scene = str(getattr(state, "scene", "") or context.get("ui_scene") or "")
        intent = str(getattr(state, "intent", "") or context.get("intent") or "")
        forced_skill_ids = self._forced_skill_ids(state, context)

        scored: list[tuple[int, SkillSpec, list[str]]] = []
        for skill in self.skills:
            if not skill.enabled:
                continue
            score, reasons = self._score_skill(
                skill,
                message=message,
                scene=scene,
                intent=intent,
                forced_skill_ids=forced_skill_ids,
            )
            if score >= skill.activation.min_score:
                scored.append((score, skill, reasons))

        scored.sort(
            key=lambda item: (
                item[1].id in forced_skill_ids,
                item[1].priority,
                item[0],
                item[1].id,
            ),
            reverse=True,
        )
        if self.max_active > 0:
            scored = scored[: self.max_active]

        skills = [skill for _score, skill, _reasons in scored]
        reasons_by_skill = {skill.id: reasons for _score, skill, reasons in scored}
        prompt_blocks = [
            SkillPromptBlock(
                skill_id=skill.id,
                version=skill.version,
                priority=skill.priority,
                content=skill.instructions.content or load_skill_body(skill),
                reasons=reasons_by_skill.get(skill.id, []),
            )
            for skill in skills
            if skill.instructions.content or skill.skill_file
        ]
        return ActiveSkillSet(
            skills=skills,
            activation_reasons=reasons_by_skill,
            prompt_blocks=prompt_blocks,
        )

    def _score_skill(
        self,
        skill: SkillSpec,
        *,
        message: str,
        scene: str,
        intent: str,
        forced_skill_ids: set[str],
    ) -> tuple[int, list[str]]:
        score = 0
        reasons: list[str] = []

        if skill.id in forced_skill_ids:
            score += 100
            reasons.append("forced")
        if scene and scene in skill.activation.scenes:
            score += 3
            reasons.append(f"scene:{scene}")
        if intent and intent in skill.activation.intents:
            score += 3
            reasons.append(f"intent:{intent}")
        for keyword in skill.activation.keywords:
            if keyword and keyword in message:
                score += 1
                reasons.append(f"keyword:{keyword}")

        return score, reasons

    def _forced_skill_ids(self, state: Any, context: dict[str, Any]) -> set[str]:
        candidates: list[Any] = []
        context_overrides = getattr(state, "context_overrides", None)
        if isinstance(context_overrides, dict):
            candidates.append(context_overrides.get("skill_ids"))
            candidates.append(context_overrides.get("forced_skill_ids"))
        candidates.append(context.get("skill_ids"))
        candidates.append(context.get("forced_skill_ids"))

        skill_ids: set[str] = set()
        for value in candidates:
            if isinstance(value, str):
                skill_ids.add(value)
            elif isinstance(value, list):
                skill_ids.update(item for item in value if isinstance(item, str))
        return skill_ids
