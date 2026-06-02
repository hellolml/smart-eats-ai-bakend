from __future__ import annotations

from typing import Any

from app.agent.multi_agent.base import AgentTurnContext, PreparedAgentTurn
from app.domain.preferences.markdown_profile import (
    build_preference_context,
    ensure_user_preference_file,
    update_user_preference_profile,
)


class FoodDecisionAgent:
    agent_id = "food_decision"
    plan_type = None
    scenes = {"eat", "food_decision", "home_chef"}

    def matches(self, payload: dict[str, Any]) -> bool:
        agent_id = str(payload.get("agent_id") or "").strip()
        scene = str(payload.get("scene") or "").strip()
        intent = _intent_from_payload(payload)
        return agent_id in {self.agent_id, "eat"} or scene in self.scenes or intent in {"eat_out", "cook_home"}

    async def prepare_turn(self, context: AgentTurnContext) -> PreparedAgentTurn:
        payload = dict(context.payload)
        payload["scene"] = "eat" if payload.get("scene") in (None, "", "chat") else payload.get("scene")
        payload["agent_id"] = self.agent_id

        await update_user_preference_profile(
            context.user_id,
            user_text=str(payload.get("message") or ""),
            source="food_agent_user_message",
        )
        profile = await ensure_user_preference_file(context.user_id)
        preference_context = build_preference_context(profile)

        context_overrides = _context_overrides(payload)
        intent = _intent_from_payload(payload) or context_overrides.get("intent") or "eat_out"
        context_overrides["intent"] = intent
        context_overrides["agent_id"] = self.agent_id
        context_overrides["user_preference_md"] = preference_context
        context_overrides["food_profile"] = preference_context.get("profile") or {}
        context_overrides["forced_skill_ids"] = _merge_forced_skill_ids(
            context_overrides.get("forced_skill_ids"),
            ["home_chef"] if intent == "cook_home" else ["food_decision", "restaurant_finder"],
        )
        payload["client_context_overrides"] = context_overrides

        return PreparedAgentTurn(
            payload=payload,
            agent_id=self.agent_id,
            plan_type=None,
            context_overrides=context_overrides,
        )


def _intent_from_payload(payload: dict[str, Any]) -> str | None:
    overrides = payload.get("client_context_overrides")
    if isinstance(overrides, dict) and isinstance(overrides.get("intent"), str):
        return overrides["intent"]
    text = str(payload.get("message") or "")
    if any(token in text for token in ("在家做", "自己做", "菜谱", "食材", "冰箱")):
        return "cook_home"
    if any(token in text for token in ("吃", "饭", "餐", "美食", "外卖", "餐厅")):
        return "eat_out"
    return None


def _context_overrides(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("client_context_overrides")
    return dict(value) if isinstance(value, dict) else {}


def _merge_forced_skill_ids(existing: Any, required: list[str]) -> list[str]:
    values: list[str] = []
    if isinstance(existing, str):
        values.append(existing)
    elif isinstance(existing, list):
        values.extend(item for item in existing if isinstance(item, str))
    for item in required:
        if item not in values:
            values.append(item)
    return values
