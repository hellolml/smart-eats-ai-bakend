from __future__ import annotations

from typing import Any

from app.agent.runtime.hooks import BaseSkillHooks


class HomeChefHooks(BaseSkillHooks):
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
            if not isinstance(item, dict) or item.get("tool") != "rag_search_recipes":
                continue
            result = item.get("result")
            if not isinstance(result, dict):
                continue
            recipes = result.get("items")
            if not isinstance(recipes, list) or not recipes:
                continue
            recommendations: list[dict[str, Any]] = []
            for recipe in recipes[:3]:
                if not isinstance(recipe, dict):
                    continue
                title = str(recipe.get("title") or "").strip()
                if not title:
                    continue
                snippet = str(recipe.get("snippet") or "").strip()
                recommendations.append(
                    {
                        "type": "recipe",
                        "title": title,
                        "reason": snippet[:80] if snippet else "基于知识库检索",
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
