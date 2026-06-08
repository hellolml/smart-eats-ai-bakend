from __future__ import annotations

from typing import Any

from app.agent.runtime.hooks import BaseSkillHooks


HOME_CHEF_ALLOWED_TOOLS = {
    "get_fridge_items",
    "rag_search_recipes",
    "search_recipes",
}


class HomeChefHooks(BaseSkillHooks):
    def short_circuit_final(self, state: Any) -> dict[str, Any] | None:
        message = str(getattr(state, "message", "") or "")
        if _is_leftover_rice_safety_question(message):
            return _leftover_rice_safety_final()
        return None

    def filter_allowed_tools(self, state: Any, allowed_tools: list[str]) -> list[str] | None:
        context = getattr(state, "context", None)
        intent = context.get("intent") if isinstance(context, dict) else None
        food_mode = context.get("food_mode") if isinstance(context, dict) else None
        if getattr(state, "scene", None) != "home_chef" and intent != "cook_home" and food_mode != "cook_home":
            return None
        return [tool for tool in allowed_tools if tool in HOME_CHEF_ALLOWED_TOOLS]

    def handle_tool_result(self, state: Any, tool_name: str, result: Any) -> dict[str, Any] | None:
        if tool_name == "get_fridge_items":
            return self._handle_get_fridge_items(state, result)
        if tool_name == "rag_search_recipes":
            return self._handle_rag_search_recipes(state, result)
        return None

    def best_effort_fallback(self, state: Any) -> dict[str, Any] | None:
        if isinstance(state.context, dict) and state.context.get("fridge_items") == []:
            return _note_final(
                "冰箱空啦，我先给你几道简单快手菜思路。",
                "状态：冰箱为空",
                ["要不要我按 10 分钟内完成给你 3 道菜？", "或者你想改成附近餐厅推荐也可以。"],
            )

        for item in reversed(getattr(state, "observations", []) or []):
            if not isinstance(item, dict) or item.get("tool") not in {"rag_search_recipes", "search_recipes"}:
                continue
            result = item.get("result")
            if isinstance(result, dict):
                recipes = result.get("items")
            elif isinstance(result, list):
                recipes = result
            else:
                recipes = None
            if not isinstance(recipes, list) or not recipes:
                continue
            recommendations: list[dict[str, Any]] = []
            for recipe in recipes[:3]:
                if not isinstance(recipe, dict):
                    continue
                title = str(recipe.get("title") or "").strip()
                if not title:
                    continue
                snippet = str(recipe.get("snippet") or recipe.get("reason") or "").strip()
                time = recipe.get("cook_time_min") or recipe.get("time")
                tags = recipe.get("tags")
                recommendations.append(
                    {
                        "type": "recipe",
                        "title": title,
                        "reason": snippet[:80] if snippet else "基于菜谱搜索和当前食材匹配",
                        **({"time": time} if time not in (None, "", [], {}) else {}),
                        **({"tags": tags} if isinstance(tags, list) and tags else {}),
                    }
                )
            if recommendations:
                return {
                    "recommendations": recommendations,
                    "followups": [
                        "你想学哪一道？告诉我菜名，我直接给你详细步骤。",
                        "如果你有忌口或时间限制，我可以继续帮你筛。",
                    ],
                    "warnings": [],
                }
        return None

    def _handle_get_fridge_items(self, state: Any, result: Any) -> dict[str, Any] | None:
        if not isinstance(result, dict):
            return None
        context = _ensure_context(state)
        items = result.get("items") if isinstance(result.get("items"), list) else []
        context["fridge_items"] = items
        overrides = _ensure_context_overrides(state)
        if not items:
            overrides["fridge_empty"] = True
            # ── eval: emit recovery SSE event for empty fridge ──
            _emit_recovery_event(state, "fridge_empty", "get_fridge_items")
        else:
            overrides.pop("fridge_empty", None)
        overrides.pop("system_directive", None)
        _prune_empty_context_overrides(state)
        return None

    def _handle_rag_search_recipes(self, state: Any, result: Any) -> dict[str, Any] | None:
        if not isinstance(result, dict):
            return None
        items = result.get("items") if isinstance(result.get("items"), list) else []
        overrides = _ensure_context_overrides(state)
        if items:
            overrides["rag_recipe_hits"] = items[:3]
        else:
            overrides.pop("rag_recipe_hits", None)
        overrides.pop("system_directive", None)
        _prune_empty_context_overrides(state)
        return None


def _ensure_context(state: Any) -> dict[str, Any]:
    if getattr(state, "context", None) is None:
        state.context = {}
    return state.context


def _ensure_context_overrides(state: Any) -> dict[str, Any]:
    if getattr(state, "context_overrides", None) is None:
        state.context_overrides = {}
    return state.context_overrides


def _prune_empty_context_overrides(state: Any) -> None:
    if isinstance(getattr(state, "context_overrides", None), dict) and not state.context_overrides:
        state.context_overrides = None


def _note_final(title: str, reason: str, followups: list[str]) -> dict[str, Any]:
    return {
        "recommendations": [{"type": "note", "title": title, "reason": reason}],
        "followups": followups,
        "warnings": [],
    }


def _is_leftover_rice_safety_question(message: str) -> bool:
    return any(token in message for token in ("剩饭", "剩米饭", "隔夜饭", "昨天剩")) and any(
        token in message for token in ("安全", "提醒", "能吃", "加热", "冷藏", "隔夜")
    )


def _leftover_rice_safety_final() -> dict[str, Any]:
    return {
        "scene": "home_chef",
        "agent_id": "home_chef",
        "recommendations": [
            {
                "type": "note",
                "title": "隔夜剩饭可以用，但先确认保存条件。",
                "reason": "米饭煮熟后 2 小时内冷藏、冷藏不超过 24 小时、没有酸味或发黏，才建议继续做炒饭。",
            }
        ],
        "followups": [
            "加热时要彻底炒透，中心温度尽量到 75℃ 以上。",
            "如果昨晚室温放了一夜，建议直接丢弃，不要为了省一点米饭冒风险。",
        ],
        "warnings": [
            "剩饭常见风险是蜡样芽孢杆菌，室温放太久会增加食物中毒风险。",
            "冷藏后的隔夜米饭只适合再加热一次，不建议反复加热。",
        ],
    }


def _emit_recovery_event(state: Any, trigger: str, tool_name: str) -> None:
    """Emit a recovery SSE event for evaluation trace collection."""
    events = getattr(state, "events", None)
    if isinstance(events, list):
        events.append(
            {
                "event": "recovery",
                "data": {
                    "path": "best_effort_fallback",
                    "trigger": trigger,
                    "tool_name": tool_name,
                    "message": f"Home chef encountered: {trigger}",
                },
            }
        )
