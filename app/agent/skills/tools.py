from __future__ import annotations

from app.agent.skills.models import SkillSpec, SkillToolComposerOutput


class SkillToolComposer:
    def __init__(self, *, global_allowlist: list[str] | None = None) -> None:
        self.global_allowlist = list(dict.fromkeys(global_allowlist or []))

    def compose(
        self,
        *,
        base_tools: list[str],
        active_skills: list[SkillSpec],
    ) -> SkillToolComposerOutput:
        allowed: list[str] = []
        denied: dict[str, str] = {}
        sources: dict[str, list[str]] = {}

        for tool_name in base_tools:
            self._append_once(allowed, tool_name)
            sources.setdefault(tool_name, []).append("base")

        global_allow = set(self.global_allowlist)
        for skill in active_skills:
            for tool_name in skill.tools.allow:
                if skill.tools.require_global_allowlist and global_allow and tool_name not in global_allow:
                    denied[tool_name] = "not_in_global_allowlist"
                    continue
                self._append_once(allowed, tool_name)
                sources.setdefault(tool_name, []).append(skill.id)

        return SkillToolComposerOutput(
            allowed_tools=allowed,
            denied_tools=denied,
            tool_sources=sources,
        )

    def _append_once(self, items: list[str], value: str) -> None:
        if value and value not in items:
            items.append(value)

