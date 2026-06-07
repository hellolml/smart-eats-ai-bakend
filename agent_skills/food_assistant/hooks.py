from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.agent.runtime.hooks import BaseSkillHooks
from agent_skills.food_decision.hooks import FoodDecisionHooks
from agent_skills.home_chef.hooks import HomeChefHooks
from agent_skills.restaurant_finder.hooks import RestaurantFinderHooks


EAT_OUT_CUES = (
    "出去吃",
    "外面吃",
    "去哪吃",
    "餐厅",
    "饭店",
    "外卖",
    "附近",
    "找店",
    "店",
    "换一家",
    "下一家",
    "第二家",
    "第三家",
    "近一点",
    "不辣",
)
COOK_HOME_CUES = (
    "做饭",
    "在家做",
    "家里做",
    "家里",
    "菜谱",
    "食谱",
    "冰箱",
    "食材",
    "自己做",
)
DECIDE_FOOD_CUES = (
    "吃点啥",
    "吃什么",
    "今天吃",
    "晚饭",
    "午饭",
    "早餐",
    "夜宵",
    "不知道吃",
)


class FoodAssistantHooks(BaseSkillHooks):
    def __init__(self) -> None:
        self.food_decision = FoodDecisionHooks()
        self.home_chef = HomeChefHooks()
        self.restaurant_finder = RestaurantFinderHooks()

    async def build_context(
        self,
        state: Any,
        context: dict[str, Any],
        runtime: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        extra = await self.restaurant_finder.build_context(state, context, runtime)
        merged = {**context, **extra}
        mode = _infer_food_mode(getattr(state, "message", None), merged)
        if mode:
            extra["food_mode"] = mode
            _ensure_context(state)["food_mode"] = mode
        return extra

    def normalize_tool_args(self, state: Any, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        if tool_name == "food_decision":
            updated = dict(args)
            mode = _current_food_mode(state)
            if mode == "eat_out":
                updated.setdefault("scene", "blindbox")
            elif mode == "cook_home":
                updated.setdefault("scene", "home")
            else:
                updated.setdefault("scene", "food_decision")
            return updated
        return self.restaurant_finder.normalize_tool_args(state, tool_name, args)

    def handle_tool_result(self, state: Any, tool_name: str, result: Any) -> dict[str, Any] | None:
        if tool_name in {"get_fridge_items", "rag_search_recipes"}:
            _set_food_mode(state, "cook_home")
            return self.home_chef.handle_tool_result(state, tool_name, result)

        if tool_name in {"get_ip_location", "geocode_location", "search_restaurants"}:
            _set_food_mode(state, "eat_out")
            handled = self.restaurant_finder.handle_tool_result(state, tool_name, result)
            if handled:
                return handled
            if tool_name == "search_restaurants" and isinstance(result, list) and result:
                _ensure_context(state)["last_restaurants"] = result
                return _restaurant_final(result)
            return None

        if tool_name == "food_decision":
            return self._handle_food_decision(state, result)

        return None

    def best_effort_fallback(self, state: Any) -> dict[str, Any] | None:
        mode = _current_food_mode(state)
        if mode == "eat_out":
            return self.restaurant_finder.best_effort_fallback(state)
        if mode == "cook_home":
            return self.home_chef.best_effort_fallback(state)

        restaurant_final = self.restaurant_finder.best_effort_fallback(state)
        if restaurant_final is not None:
            return restaurant_final
        return self.home_chef.best_effort_fallback(state)

    def short_circuit_final(self, state: Any) -> dict[str, Any] | None:
        if _current_food_mode(state) != "clarify":
            return None
        return _note_final(
            "你想在家做，还是出去吃？",
            "需要先确认吃饭方式",
            ["回复“在家做”我就按冰箱和菜谱推荐。", "回复“出去吃”我就帮你找附近餐厅。"],
        )

    def forced_tool_calls(self, state: Any) -> list[dict[str, Any]] | None:
        if _current_food_mode(state) != "decide_food":
            return None
        return [
            {
                "name": "food_decision",
                "args": {
                    "query": getattr(state, "message", None) or "今天吃点啥",
                    "scene": "food_decision",
                },
                "id": f"call_{uuid4().hex[:12]}_food",
                "type": "tool_call",
            }
        ]

    def filter_allowed_tools(self, state: Any, allowed_tools: list[str]) -> list[str] | None:
        if _current_food_mode(state) != "eat_out":
            return None
        if "search_restaurants" not in allowed_tools:
            return None
        return [tool for tool in allowed_tools if tool != "food_decision"]

    def _handle_food_decision(self, state: Any, result: Any) -> dict[str, Any] | None:
        if not isinstance(result, dict) or result.get("error"):
            return None
        mode = _current_food_mode(state)
        decision = result.get("decision") if isinstance(result.get("decision"), dict) else {}
        decision_type = str(decision.get("type") or "").strip()
        if mode == "eat_out" and decision_type != "restaurant":
            context = _ensure_context(state)
            restaurants = context.get("last_restaurants")
            if isinstance(restaurants, list) and restaurants:
                return _restaurant_final(restaurants)
            context["last_search_error"] = context.get("last_search_error") or "food_decision_non_restaurant"
            return None
        return self.food_decision.handle_tool_result(state, "food_decision", result)


def _infer_food_mode(message: Any, context: dict[str, Any] | None = None) -> str | None:
    text = str(message or "")
    if any(token in text for token in COOK_HOME_CUES):
        return "cook_home"
    if any(token in text for token in EAT_OUT_CUES):
        return "eat_out"
    if any(token in text for token in DECIDE_FOOD_CUES):
        return "decide_food"
    if isinstance(context, dict):
        existing = context.get("food_mode")
        if existing in {"eat_out", "cook_home", "decide_food"}:
            return str(existing)
        if context.get("last_restaurants"):
            return "eat_out"
        if context.get("fridge_items") is not None:
            return "cook_home"
    return "clarify" if text.strip() else None


def _current_food_mode(state: Any) -> str | None:
    context = getattr(state, "context", None)
    if isinstance(context, dict):
        mode = context.get("food_mode")
        if isinstance(mode, str) and mode:
            return mode
    mode = _infer_food_mode(getattr(state, "message", None), context if isinstance(context, dict) else None)
    if mode:
        _set_food_mode(state, mode)
    return mode


def _set_food_mode(state: Any, mode: str) -> None:
    _ensure_context(state)["food_mode"] = mode


def _ensure_context(state: Any) -> dict[str, Any]:
    if getattr(state, "context", None) is None:
        state.context = {}
    return state.context


def _note_final(title: str, reason: str, followups: list[str]) -> dict[str, Any]:
    return {
        "recommendations": [{"type": "note", "title": title, "reason": reason}],
        "followups": followups,
        "warnings": [],
    }


def _restaurant_final(restaurants: list[Any]) -> dict[str, Any]:
    rows = [item for item in restaurants if isinstance(item, dict)]
    recommendations = []
    for item in rows[:3]:
        name = str(item.get("name") or "附近餐厅").strip()
        address = str(item.get("address") or item.get("distance_text") or "").strip()
        rating = item.get("rating")
        price = item.get("price")
        details = []
        if address:
            details.append(address)
        if rating:
            details.append(f"评分 {rating}")
        if price:
            details.append(f"人均 {price}")
        recommendations.append(
            {
                "type": "restaurant",
                "title": name,
                "reason": "；".join(details) or "基于当前位置和关键词搜索到的附近餐厅",
                "raw": item,
            }
        )
    if not recommendations:
        return _note_final(
            "我暂时没有拿到可用餐厅结果。",
            "餐厅搜索结果为空",
            ["换一个商圈或地标再试一次。", "也可以告诉我预算和口味，我再缩小范围。"],
        )
    return {
        "scene": "eat",
        "agent_id": "food_assistant",
        "recommendations": recommendations,
        "followups": ["要不要我按距离、评分或口味再帮你筛一轮？", "选定一家后我可以继续帮你规划路线。"],
        "warnings": [],
    }
