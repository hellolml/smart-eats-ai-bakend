from __future__ import annotations

from pathlib import Path

from app.agent.skills.loader import load_skills_from_path
from app.agent.skills.models import SkillSpec


class SkillRegistry:
    def __init__(self, skills_path: str | Path | None) -> None:
        self.skills_path = skills_path
        self._skills: list[SkillSpec] | None = None

    def load_all(self) -> list[SkillSpec]:
        if self._skills is None:
            self._skills = load_skills_from_path(self.skills_path)
        return list(self._skills)

    def get_enabled(self) -> list[SkillSpec]:
        return [skill for skill in self.load_all() if skill.enabled]

    def get_by_id(self, skill_id: str) -> SkillSpec | None:
        return next((skill for skill in self.get_enabled() if skill.id == skill_id), None)

