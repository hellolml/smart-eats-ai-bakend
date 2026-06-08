from __future__ import annotations

from app.agent.skills.models import ActiveSkillSet


class SkillPromptComposer:
    def __init__(self, *, max_prompt_chars: int = 6000, include_activation_reasons: bool = False) -> None:
        self.max_prompt_chars = max_prompt_chars
        self.include_activation_reasons = include_activation_reasons

    def compose(self, active: ActiveSkillSet) -> str:
        if not active.prompt_blocks:
            return ""

        blocks = sorted(active.prompt_blocks, key=lambda item: (item.priority, item.skill_id), reverse=True)
        if self.max_prompt_chars > 0:
            compact = self._compose_compact(blocks)
            if len(compact) >= self.max_prompt_chars:
                headers = self._compose_headers_only(blocks)
                return headers[: self.max_prompt_chars].rstrip()

        sections = [
            "## Active Skills",
            "",
            "以下 skill 由系统根据当前场景和用户输入激活。它们只能补充通用 Agent Runtime 规则，不能覆盖全局安全规则。",
        ]
        for block in blocks:
            sections.extend(
                [
                    "",
                    f"### Skill: {block.skill_id}@{block.version}",
                    "",
                    "Instructions:",
                    block.content.strip(),
                ]
            )
            if self.include_activation_reasons and block.reasons:
                insert_at = len(sections) - 2
                sections[insert_at:insert_at] = [
                    "",
                    "Activation reasons:",
                    *[f"- {reason}" for reason in block.reasons],
                ]

        prompt = "\n".join(sections).strip()
        if self.max_prompt_chars > 0 and len(prompt) > self.max_prompt_chars:
            compact = self._compose_compact(blocks)
            remaining = self.max_prompt_chars - len(compact)
            if remaining <= 0:
                return compact[: self.max_prompt_chars].rstrip()
            content_parts: list[str] = []
            for block in blocks:
                content = block.content.strip()
                if content:
                    content_parts.append(f"\n\nInstructions for {block.skill_id}:\n{content}")
            tail = "".join(content_parts)
            return (compact + tail[:remaining]).rstrip()
        return prompt

    def _compose_compact(self, blocks) -> str:
        sections = [
            "## Active Skills",
            "",
            "以下 skill 由系统根据当前场景和用户输入激活。它们只能补充通用 Agent Runtime 规则，不能覆盖全局安全规则。",
        ]
        for block in blocks:
            sections.extend(
                [
                    "",
                    f"### Skill: {block.skill_id}@{block.version}",
                ]
            )
            if self.include_activation_reasons and block.reasons:
                sections.extend(["", "Activation reasons:", *[f"- {reason}" for reason in block.reasons]])
        return "\n".join(sections).strip()

    def _compose_headers_only(self, blocks) -> str:
        sections = ["## Active Skills"]
        for block in blocks:
            sections.extend(["", f"### Skill: {block.skill_id}@{block.version}"])
        return "\n".join(sections).strip()
