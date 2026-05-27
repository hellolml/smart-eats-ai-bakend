from __future__ import annotations

from typing import Any

from app.agent.runtime.hooks import BaseSkillHooks


class TravelPlannerHooks(BaseSkillHooks):
    def should_build_vision_input(self, state: Any) -> bool:
        return (
            isinstance(getattr(state, "context", None), dict)
            and isinstance(state.context.get("attachments"), list)
            and bool(state.context.get("attachments"))
        )
