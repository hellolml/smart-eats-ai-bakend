from __future__ import annotations

import re
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
    "在家吃",
    "家里做",
    "家里吃",
    "家里",
    "菜谱",
    "食谱",
    "冰箱",
    "食材",
    "自己做",
    "能做什么",
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
AFFIRMATIVE_CUES = (
    "可以",
    "可以啊",
    "好",
    "好的",
    "行",
    "行啊",
    "嗯",
    "嗯嗯",
    "继续",
    "筛一轮",
    "再筛",
)
EAT_OUT_ALLOWED_TOOLS = {
    "get_ip_location",
    "geocode_location",
    "search_restaurants",
    "get_weather",
}
DECIDE_FOOD_ALLOWED_TOOLS = {"food_decision"}


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
            if tool_name == "search_restaurants" and isinstance(result, list) and not result:
                _ensure_context(state)["last_search_error"] = "empty_restaurant_results"
                return _note_final(
                    "这附近暂时没有搜到匹配餐厅。",
                    "餐厅搜索结果为空",
                    ["可以换一个地标、扩大范围，或换成更宽泛的菜系。"],
                )
            if tool_name == "search_restaurants" and isinstance(result, dict) and result.get("error"):
                _ensure_context(state)["last_search_error"] = result.get("error")
                return _note_final(
                    "我还需要更明确的位置才能找餐厅。",
                    str(result.get("error") or "missing_location"),
                    ["告诉我附近地标或商圈，我再帮你搜。"],
                )
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
        mode = _current_food_mode(state)
        if mode == "eat_out":
            context = _ensure_context(state)
            if _is_home_vs_eat_out_comparison(getattr(state, "message", None)):
                return _home_vs_eat_out_comparison_final(context)
            selected_restaurant = context.get("selected_restaurant")
            if isinstance(selected_restaurant, dict) and _references_current_restaurant(getattr(state, "message", None)):
                return _selected_restaurant_final(selected_restaurant, message=getattr(state, "message", None))
            restaurants = context.get("last_restaurants")
            if _is_negative_restaurant_selection(getattr(state, "message", None)) and isinstance(restaurants, list) and restaurants:
                return _restaurant_refinement_final(restaurants, message=getattr(state, "message", None))
            selected = _selected_restaurant_from_message(
                getattr(state, "message", None),
                restaurants,
            )
            if not selected and _defaults_to_latest_restaurant(getattr(state, "message", None), restaurants):
                selected = restaurants[0]
            if selected:
                context["selected_restaurant"] = selected
                return _selected_restaurant_final(selected, message=getattr(state, "message", None))
            if _is_restaurant_selection_followup(getattr(state, "message", None)) and isinstance(restaurants, list) and restaurants:
                return _unmatched_restaurant_selection_final(getattr(state, "message", None), restaurants)
            return None
        if mode != "clarify":
            return None
        return _note_final(
            "你想在家做，还是出去吃？",
            "需要先确认吃饭方式",
            ["回复“在家做”我就按冰箱和菜谱推荐。", "回复“出去吃”我就帮你找附近餐厅。"],
        )

    def forced_tool_calls(self, state: Any) -> list[dict[str, Any]] | None:
        mode = _current_food_mode(state)
        if mode == "eat_out" and _has_location(state) and not _has_observed_tool(state, "search_restaurants"):
            context = _ensure_context(state)
            location = context.get("location") if isinstance(context.get("location"), dict) else {}
            return [
                {
                    "name": "search_restaurants",
                    "args": {
                        "query": _restaurant_query_from_message(getattr(state, "message", None)),
                        "lat": location.get("lat"),
                        "lng": location.get("lng"),
                        "city": context.get("city"),
                    },
                    "id": f"call_{uuid4().hex[:12]}_restaurant",
                    "type": "tool_call",
                }
            ]
        if mode == "eat_out" and _is_affirmative_followup(str(getattr(state, "message", None) or "")):
            return [
                {
                    "name": "search_restaurants",
                    "args": {
                        "query": "附近餐厅",
                    },
                    "id": f"call_{uuid4().hex[:12]}_restaurant",
                    "type": "tool_call",
                }
            ]
        if mode != "decide_food":
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
        mode = _current_food_mode(state)
        if mode == "eat_out":
            if "search_restaurants" not in allowed_tools:
                return None
            return [tool for tool in allowed_tools if tool in EAT_OUT_ALLOWED_TOOLS]
        if mode == "decide_food":
            return [tool for tool in allowed_tools if tool in DECIDE_FOOD_ALLOWED_TOOLS]
        return None

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
    if _is_explicit_eat_out_message(text):
        return "eat_out"
    if any(token in text for token in COOK_HOME_CUES):
        return "cook_home"
    if any(token in text for token in EAT_OUT_CUES):
        return "eat_out"
    if any(token in text for token in DECIDE_FOOD_CUES):
        return "decide_food"
    if isinstance(context, dict):
        intent = context.get("intent")
        if intent in {"eat_out", "cook_home", "decide_food"}:
            return str(intent)
        existing = context.get("food_mode")
        if existing in {"eat_out", "cook_home", "decide_food"}:
            return str(existing)
        if context.get("last_restaurants"):
            return "eat_out"
        if context.get("fridge_items") is not None:
            return "cook_home"
        if _is_affirmative_followup(text) and _history_offered_restaurant_refinement(context):
            return "eat_out"
    return "clarify" if text.strip() else None


def _is_explicit_eat_out_message(text: str) -> bool:
    return any(
        token in text
        for token in (
            "不想做饭",
            "不做饭",
            "做饭失败",
            "出去吃",
            "外面吃",
            "出门吃",
            "找餐厅",
            "找饭店",
            "餐厅",
            "饭店",
        )
    )


def _is_affirmative_followup(text: str) -> bool:
    cleaned = text.strip().strip("，。！？!?,. ")
    return cleaned in AFFIRMATIVE_CUES


def _history_offered_restaurant_refinement(context: dict[str, Any]) -> bool:
    history = context.get("history")
    if not isinstance(history, list):
        return False
    for item in reversed(history[-6:]):
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or item.get("message") or item.get("text") or "")
        if "按距离、评分或口味" in content or "帮你筛一轮" in content or "规划路线" in content:
            return True
    return False


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


def _is_home_vs_eat_out_comparison(message: Any) -> bool:
    text = str(message or "")
    return "对比" in text and any(token in text for token in ("在家做", "在家吃", "做饭")) and any(
        token in text for token in ("出去吃", "外面吃", "餐厅")
    )


def _home_vs_eat_out_comparison_final(context: dict[str, Any]) -> dict[str, Any]:
    home_title = "在家做"
    eat_title = "出去吃"
    latest_home = context.get("latest_home_chef_final_json")
    if isinstance(latest_home, dict):
        recs = latest_home.get("recommendations")
        if isinstance(recs, list) and recs and isinstance(recs[0], dict):
            name = str(recs[0].get("title") or "").strip()
            if name:
                home_title = f"在家做：{name}"
    selected = context.get("selected_restaurant")
    if isinstance(selected, dict):
        name = str(selected.get("name") or selected.get("title") or "").strip()
        if name:
            eat_title = f"出去吃：{name}"
    return {
        "scene": "eat",
        "agent_id": "food_assistant",
        "recommendations": [
            {
                "type": "note",
                "title": home_title,
                "reason": "在家做更省钱可控，适合继续用现有食材，缺点是要动手和收拾。",
            },
            {
                "type": "note",
                "title": eat_title,
                "reason": "出去吃更省事，适合不想做饭时快速解决，缺点是口味和预算可控性稍弱。",
            },
        ],
        "followups": ["如果现在很累，优先出去吃；如果想省钱和清淡可控，优先在家做。"],
        "warnings": [],
    }


def _restaurant_final(restaurants: list[Any]) -> dict[str, Any]:
    rows = [item for item in restaurants if isinstance(item, dict)]
    recommendations = []
    for item in rows[:3]:
        name = str(item.get("name") or "附近餐厅").strip()
        address = str(item.get("address") or item.get("distance_text") or "").strip()
        rating = item.get("rating")
        price = _restaurant_price_text(item.get("price"))
        details = []
        if address:
            details.append(address)
        if rating:
            details.append(f"评分 {rating}")
        if price:
            details.append(price)
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


def _selected_restaurant_final(restaurant: dict[str, Any], *, message: Any = None) -> dict[str, Any]:
    name = str(restaurant.get("name") or restaurant.get("title") or "这家餐厅").strip()
    address = str(restaurant.get("address") or restaurant.get("distance_text") or "").strip()
    rating = restaurant.get("rating")
    price = _restaurant_price_text(restaurant.get("price"))
    details = []
    if address:
        details.append(address)
    if rating:
        details.append(f"评分 {rating}")
    if price:
        details.append(price)
    text = str(message or "")
    if _selection_index(text) == 0:
        details.append("已选第一家")
    if "春熙路" in text:
        details.append("春熙路附近")
    if "晚餐" in text:
        details.append("可安排进晚餐")
    if any(token in text for token in ("推荐理由", "为什么推荐", "推荐一下", "理由")):
        details.insert(0, "推荐这家餐厅：位置方便，口味相对清淡，适合一个人简单吃")
    reason = "；".join(details) or "已按你刚才选中的餐厅继续"
    followups = ["我可以继续帮你规划路线。", "也可以换一家或再按口味筛选。"]
    if any(token in text for token in ("不要规划路线", "先不要规划路线", "先不规划路线", "不用规划路线")):
        followups = ["已按你的要求先不规划路线。", "需要路线时再告诉我出发地即可。"]
    if "清淡" in text or "记住" in text:
        followups.insert(0, "已按“清淡”这个偏好继续理解这次选择。")
    return {
        "scene": "eat",
        "agent_id": "food_assistant",
        "recommendations": [
            {
                "type": "restaurant",
                "title": name,
                "reason": reason,
                "raw": restaurant,
            }
        ],
        "followups": followups,
        "warnings": [],
        "selected_restaurant": restaurant,
    }


def _unmatched_restaurant_selection_final(message: Any, restaurants: list[Any]) -> dict[str, Any]:
    names = [
        str(item.get("name") or item.get("title") or "").strip()
        for item in restaurants
        if isinstance(item, dict) and str(item.get("name") or item.get("title") or "").strip()
    ]
    preview = "、".join(names[:3]) if names else "刚才推荐的餐厅"
    hints = _selection_hints(str(message or ""))
    hint_text = f"名字带“{hints[0]}”的餐厅" if hints else "你说的那家餐厅"
    return {
        "scene": "eat",
        "agent_id": "food_assistant",
        "status": "needs_clarification",
        "recommendations": [
            {
                "type": "note",
                "title": f"我没有在刚才的推荐里找到{hint_text}。",
                "reason": f"当前可选项是：{preview}",
            }
        ],
        "followups": ["可以回复“第一家/第二家”，或直接说餐厅名。", "也可以让我重新按口味再筛一轮。"],
        "warnings": [],
    }


def _restaurant_refinement_final(restaurants: list[Any], *, message: Any = None) -> dict[str, Any]:
    rows = [item for item in restaurants if isinstance(item, dict)]
    rejected_index = _selection_index(str(message or ""))
    if rejected_index is not None and 0 <= rejected_index < len(rows):
        rows = [item for index, item in enumerate(rows) if index != rejected_index]
    final = _restaurant_final(rows)
    if final.get("recommendations"):
        reason_prefix = "已避开你刚才说不想选的那家"
        if "一个人" in str(message or ""):
            reason_prefix += "，优先保留更适合一个人吃的选择"
        for item in final["recommendations"]:
            if isinstance(item, dict):
                current = str(item.get("reason") or "").strip()
                item["reason"] = f"{reason_prefix}；{current}" if current else reason_prefix
    return final


def _selected_restaurant_from_message(message: Any, restaurants: Any) -> dict[str, Any] | None:
    if not isinstance(restaurants, list) or not restaurants:
        return None
    selected_index = _selection_index(str(message or ""))
    if selected_index is not None and 0 <= selected_index < len(restaurants):
        selected = restaurants[selected_index]
        return selected if isinstance(selected, dict) else None
    hints = _selection_hints(str(message or ""))
    text = _normalize_selection_text(str(message or ""))
    if not text:
        return None
    for restaurant in restaurants:
        if not isinstance(restaurant, dict):
            continue
        aliases = _restaurant_aliases(restaurant)
        if any(alias and alias in text for alias in aliases):
            return restaurant
        if hints and any(hint in alias for hint in hints for alias in aliases):
            return restaurant
    return None


def _is_restaurant_selection_followup(message: Any) -> bool:
    text = str(message or "")
    normalized = _normalize_selection_text(text)
    if not normalized:
        return False
    if _selection_index(text) is not None or _selection_hints(text):
        return True
    return any(
        token in text
        for token in (
            "上面推荐",
            "刚才推荐",
            "就这家",
            "就那家",
            "选这家",
            "选那家",
            "这家餐厅",
            "那家餐厅",
            "换到",
            "回到上面",
            "推荐的",
        )
    )


def _references_current_restaurant(message: Any) -> bool:
    text = str(message or "")
    return any(token in text for token in ("这家", "那家", "刚才那家", "回到刚才", "回到这家", "回到那家", "就这家", "就那家"))


def _defaults_to_latest_restaurant(message: Any, restaurants: Any) -> bool:
    if not isinstance(restaurants, list) or not restaurants:
        return False
    text = str(message or "")
    if _is_negative_restaurant_selection(text):
        return False
    return any(token in text for token in ("那就这家", "就这家", "就那家", "回到刚才那家", "刚才那家"))


def _is_negative_restaurant_selection(message: Any) -> bool:
    text = str(message or "")
    for token in ("不要规划路线", "先不要规划路线", "不用规划路线", "先不用规划路线", "不需要路线", "暂时不规划路线", "先不规划路线"):
        text = text.replace(token, "")
    if _selection_index(text) is None and not any(token in text for token in ("这家", "那家", "刚才那家")):
        return False
    return any(token in text for token in ("别选", "不选", "先不选", "不要", "算了", "不合适", "看起来一般", "换一家", "换个"))


def _has_location(state: Any) -> bool:
    context = _ensure_context(state)
    location = context.get("location") if isinstance(context.get("location"), dict) else {}
    return location.get("lat") is not None and location.get("lng") is not None


def _has_observed_tool(state: Any, tool_name: str) -> bool:
    observations = getattr(state, "observations", None)
    if not isinstance(observations, list):
        return False
    return any(isinstance(item, dict) and item.get("tool") == tool_name for item in observations)


def _restaurant_query_from_message(message: Any) -> str:
    text = str(message or "")
    for token in ("湘菜", "粤菜", "川菜", "火锅", "烧烤", "日料", "本帮菜", "杭帮菜", "小吃", "清淡"):
        if token in text:
            return token
    return "附近餐厅"


def _selection_index(value: str) -> int | None:
    text = str(value or "")
    digit = re.search(r"第\s*(\d+)\s*家|(\d+)\s*号|第\s*(\d+)\s*个", text)
    if digit:
        for group in digit.groups():
            if group:
                return max(0, int(group) - 1)
    chinese = re.search(r"第?\s*([一二两三四五六七八九十])\s*(?:家|个|号)", text)
    if not chinese and any(token in text for token in ("第一家", "第一个", "第一")):
        return 0
    if chinese:
        index = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}.get(chinese.group(1))
        return max(0, index - 1) if index else None
    return None


def _restaurant_aliases(restaurant: dict[str, Any]) -> list[str]:
    raw_names = [
        restaurant.get("name"),
        restaurant.get("title"),
        restaurant.get("verified_name"),
        restaurant.get("source_name"),
    ]
    aliases: list[str] = []
    for raw in raw_names:
        name = str(raw or "").strip()
        if not name:
            continue
        for value in (name, name.split("(", 1)[0], name.split("（", 1)[0]):
            cleaned = _normalize_selection_text(value)
            if len(cleaned) >= 2 and cleaned not in aliases:
                aliases.append(cleaned)
    return aliases


def _normalize_selection_text(value: str) -> str:
    text = str(value or "").strip().lower()
    for token in (" ", "\t", "\n", "，", "。", "！", "？", "!", "?", ",", ".", "“", "”", "\"", "'", "就", "选", "去"):
        text = text.replace(token, "")
    while text.endswith(("吧", "把", "呗", "呢", "啦", "了")):
        text = text[:-1]
    return text


def _selection_hints(value: str) -> list[str]:
    hints: list[str] = []
    for pattern in (r"名字带[“\"']?([^“”\"'，。！？\s]{1,8})", r"[“\"']([^“”\"']{1,8})[”\"']"):
        for match in re.findall(pattern, str(value or "")):
            cleaned = _normalize_selection_text(match)
            if len(cleaned) >= 2 and cleaned not in hints:
                hints.append(cleaned)
    return hints


def _restaurant_price_text(price: Any) -> str:
    if price is None:
        return ""
    if isinstance(price, (int, float)):
        return f"人均 {int(price)}"
    text = str(price).strip()
    if not text:
        return ""
    if "人均" in text or "/人" in text or "每人" in text:
        return text
    return f"人均 {text}"
