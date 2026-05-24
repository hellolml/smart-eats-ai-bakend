from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.agent.skills.models import ActiveSkillInfo, SkillDiagnostics, SkillRuntimeResult
from app.agent.skills.prompt import SkillPromptComposer
from app.agent.skills.registry import SkillRegistry
from app.agent.skills.resolver import SkillResolver
from app.agent.skills.tools import SkillToolComposer

logger = logging.getLogger("agent.skills.runtime")


class SkillRuntime:
    def __init__(
        self,
        *,
        skills_path: str | Path | None,
        enabled: bool,
        max_active: int,
        max_prompt_chars: int,
        global_allowlist: list[str],
        log_diagnostics: bool = True,
    ) -> None:
        self.enabled = enabled
        self.log_diagnostics = log_diagnostics
        self.max_active = max_active
        self.registry = SkillRegistry(skills_path)
        self.prompt_composer = SkillPromptComposer(max_prompt_chars=max_prompt_chars)
        self.tool_composer = SkillToolComposer(global_allowlist=global_allowlist)

    def resolve(
        self,
        state: Any,
        context: dict[str, Any],
        *,
        base_tools: list[str],
    ) -> SkillRuntimeResult:
        if not self.enabled:
            return SkillRuntimeResult(allowed_tools=list(base_tools), context={})

        skills = self.registry.get_enabled()
        active = SkillResolver(skills, max_active=self.max_active).resolve(state, context)
        tool_output = self.tool_composer.compose(
            base_tools=base_tools,
            active_skills=active.skills,
        )
        prompt_addendum = self.prompt_composer.compose(active)

        active_infos = [
            ActiveSkillInfo(
                id=skill.id,
                version=skill.version,
                reasons=active.activation_reasons.get(skill.id, []),
            )
            for skill in active.skills
        ]
        diagnostics = SkillDiagnostics(
            prompt_chars=len(prompt_addendum),
            denied_tools=tool_output.denied_tools,
            warnings=active.warnings,
            tool_sources=tool_output.tool_sources,
        )
        context_payload = self._build_context_payload(
            active_infos=active_infos,
            allowed_tools=tool_output.allowed_tools,
            diagnostics=diagnostics,
        )

        if active_infos and self.log_diagnostics:
            logger.info(
                "skill_resolved skills=%s reasons=%s",
                [item.id for item in active_infos],
                {item.id: item.reasons for item in active_infos},
            )
            logger.info(
                "skill_prompt_composed chars=%s skills=%s",
                len(prompt_addendum),
                [item.id for item in active_infos],
            )
            logger.info(
                "skill_tools_composed tools=%s",
                tool_output.allowed_tools,
            )

        return SkillRuntimeResult(
            active_skills=active_infos,
            system_prompt_addendum=prompt_addendum,
            allowed_tools=tool_output.allowed_tools,
            context=context_payload,
            diagnostics=diagnostics,
        )

    def _build_context_payload(
        self,
        *,
        active_infos: list[ActiveSkillInfo],
        allowed_tools: list[str],
        diagnostics: SkillDiagnostics,
    ) -> dict[str, Any]:
        return {
            "active_skills": [item.model_dump() for item in active_infos],
            "skill_allowed_tools": list(allowed_tools),
            "skill_diagnostics": diagnostics.model_dump(),
        }
