from __future__ import annotations

import os
from typing import Any

from app.agent.agents.base import default_writer_prompt
from app.common.config import settings
from app.agent.schemas import FinalAnswer
from app.agent.state import ChatState
from app.agent.agent_registry import AgentConfig, create_agent_config, register_agent
from app.agent import memory

# 规则分层说明：
# - 代码规则（本文件）：可测试、可确定执行的逻辑（意图判定、工具编排、参数归一化、结果兜底）。
# - Prompt 规则（system.md）：给 LLM 的行为策略与表达规范。
# 系统提示词文件路径
SYSTEM_PROMPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "prompts", "system.md"
)


def smart_intent_resolver(state: ChatState) -> str | None:
    import json

    text = (state.message or "").strip().lower()
    if not text:
        return "unknown"

    # 确认选择信号（优先级高）
    eat_out_tokens = ("出去吃", "外出", "附近餐厅", "找餐厅", "吃饭", "下馆子")
    cook_home_tokens = ("在家做", "做饭", "菜谱", "食谱", "冰箱")
    route_tokens = ("怎么走", "路线", "导航", "去那里", "去那儿", "到", "到这家")

    # 检查是否是确认选择（用户提到之前推荐的菜名）
    # 从历史消息中查找之前的食谱推荐
    recipe_titles = []
    for msg in state.history:
        role = msg.get("role")
        name = msg.get("name")
        if role == "tool" and name == "rag_search_recipes":
            content = msg.get("content", "")
            # content 是 result_preview 的 JSON 字符串
            if isinstance(content, str) and content:
                try:
                    result = json.loads(content)
                    items = result.get("items", []) if isinstance(result, dict) else []
                    for item in items:
                        title = (item.get("title") or "").lower()
                        if title:
                            recipe_titles.append(title)
                except (json.JSONDecodeError, AttributeError):
                    pass

    # 如果用户消息包含之前推荐的菜名，这是确认选择
    for title in recipe_titles:
        if title in text:
            return "confirm_recipe"

    if any(token in text for token in route_tokens):
        return "route"
    if any(token in text for token in cook_home_tokens):
        return "cook_home"
    if any(token in text for token in eat_out_tokens):
        return "eat_out"
    return "chat"


# 需要工具调用的关键词（命中任一则不走 fast path）
_FAST_PATH_TOOL_KEYWORDS = (
    "附近", "餐厅", "饭店", "外卖", "怎么走", "路线", "导航",
    "天气", "菜谱", "食谱", "做法", "冰箱", "食材",
    "推荐", "搜索", "查找", "找一下", "帮我找",
    "在家做", "出去吃", "下馆子", "吃什么",
    "recipe", "restaurant", "weather", "route", "navigate",
)


def smart_fast_path_decider(state: ChatState) -> bool:
    """业务层 fast path 判定：仅处理无需工具的简单闲聊。"""
    text = (state.message or "").strip()
    if not text:
        return False
    if state.scene != "chat":
        return False
    # 有 checkpoint 恢复/重放需求的走完整流程
    if state.resume_from_checkpoint or state.replay_from_checkpoint or state.checkpoint_ref:
        return False
    # 有 context_overrides 的走完整流程
    if state.context_overrides:
        return False
    text_lower = text.lower()
    if any(kw in text_lower for kw in _FAST_PATH_TOOL_KEYWORDS):
        return False
    return True


def smart_fast_path_system_prompt_builder(state: ChatState) -> str | None:
    return (
        "你是 SmartEats 智能助手。"
        "只输出自然语言，不要输出 JSON、代码块、字段名或结构化包装。"
        "用中文回答，语气友好自然。"
    )


def smart_fast_path_writer_prompt_builder(state: ChatState) -> str:
    """为 fast path 构建 Writer prompt，直接基于最近对话历史生成回复。"""
    parts: list[str] = []
    recent = state.history[-10:] if state.history else []
    if recent:
        parts.append("对话历史：")
        for msg in recent:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                parts.append(f"用户: {content}")
            elif role == "assistant":
                parts.append(f"助手: {content}")
        parts.append("")
    parts.append(f"用户最新消息: {state.message}")
    parts.append("\n请直接回复用户，语气友好自然。使用中文回答。")
    return "\n".join(parts)


def _normalize_coord(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number != 0 else None


def _extract_location_from_context(context: dict[str, Any] | None) -> tuple[float | None, float | None]:
    if not isinstance(context, dict):
        return None, None

    env = context.get("environment") if isinstance(context.get("environment"), dict) else {}
    env_location = env.get("location") if isinstance(env.get("location"), dict) else {}
    top_location = context.get("location") if isinstance(context.get("location"), dict) else {}

    lat = _normalize_coord(env_location.get("lat"))
    lng = _normalize_coord(env_location.get("lng"))
    if lat is None or lng is None:
        lat = lat if lat is not None else _normalize_coord(top_location.get("lat"))
        lng = lng if lng is not None else _normalize_coord(top_location.get("lng"))
    return lat, lng


def _extract_location_from_observations(observations: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    for item in reversed(observations):
        if not isinstance(item, dict):
            continue
        tool_name = item.get("tool")
        result = item.get("result")
        if tool_name not in {"get_ip_location", "geocode_location"}:
            continue
        if not isinstance(result, dict):
            continue
        lat = _normalize_coord(result.get("lat"))
        lng = _normalize_coord(result.get("lng"))
        if lat is not None and lng is not None:
            return lat, lng
    return None, None


def _normalize_restaurant_query(message: str | None) -> str:
    text = (message or "").strip()
    if not text:
        return "美食"
    generic_phrases = {
        "出去吃",
        "附近吃什么",
        "附近有啥吃的",
        "吃什么",
        "找吃的",
        "下馆子",
        "附近餐厅",
    }
    if text in generic_phrases:
        return "美食"
    return text


def smart_tool_plan_router(state: ChatState) -> list[dict[str, Any]] | None:
    """业务层规则路由：高频吃饭链路优先走确定性工具编排。"""
    if state.intent != "eat_out":
        return None

    lat, lng = _extract_location_from_context(state.context)
    if lat is None or lng is None:
        lat, lng = _extract_location_from_observations(state.observations)

    if lat is not None and lng is not None:
        return [
            {
                "name": "search_restaurants",
                "args": {
                    "query": _normalize_restaurant_query(state.message),
                    "lat": lat,
                    "lng": lng,
                },
            }
        ]

    return [{"name": "get_ip_location", "args": {}}]


def smart_context_extender(state: ChatState) -> dict:
    """扩展 LLM 上下文，注入业务相关字段。"""
    extra = {}
    if state.intent:
        extra["intent"] = state.intent
    if state.location_source:
        extra["location_source"] = state.location_source
    if state.recovery_path:
        extra["recovery_path"] = list(state.recovery_path)
    if state.tool_plan:
        extra["tool_plan"] = list(state.tool_plan)
    return extra


def smart_tool_args_normalizer(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    if tool_name == "geocode_location" and "query" not in args and "location" in args:
        updated = dict(args)
        updated["query"] = updated.pop("location")
        return updated
    if tool_name == "search_restaurants":
        updated = dict(args)
        for key in ("lat", "lng"):
            value = updated.get(key)
            if isinstance(value, (int, float)) and float(value) == 0.0:
                updated.pop(key, None)
        query = updated.get("query")
        if isinstance(query, str) and not query.strip():
            updated.pop("query", None)
        return updated
    return args


def smart_serial_execution_decider(calls: list[dict[str, Any]]) -> bool:
    has_location_tool = False
    has_search_without_coords = False
    for call in calls:
        tool_name = call.get("name")
        args = call.get("args", {})
        if tool_name in {"get_ip_location", "geocode_location"}:
            has_location_tool = True
        if tool_name == "search_restaurants" and isinstance(args, dict):
            lat = args.get("lat")
            lng = args.get("lng")
            lat_ok = isinstance(lat, (int, float)) and float(lat) != 0.0
            lng_ok = isinstance(lng, (int, float)) and float(lng) != 0.0
            if not (lat_ok and lng_ok):
                has_search_without_coords = True
    return has_location_tool and has_search_without_coords


def smart_tool_result_previewer(tool_name: str, result: object) -> dict[str, Any] | None:
    if tool_name == "plan_route" and isinstance(result, dict):
        return {
            "distance_m": result.get("distance_m"),
            "duration_s": result.get("duration_s"),
            "origin": result.get("origin"),
            "destination": result.get("destination"),
            "mode": result.get("mode"),
            "fallback_from": result.get("fallback_from"),
            "error": result.get("error"),
        }
    return None


async def smart_final_action_hook(state: ChatState, _final_json: dict[str, Any], db: Any) -> None:
    user = (state.message or "").strip()
    if user.startswith("记住"):
        await memory.store_memory(db, state.user_id, user)


def smart_system_prompt(payload: dict) -> str:
    """从 system.md 加载 Prompt 规则，并注入运行时上下文。"""
    try:
        with open(SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as f:
            base_prompt = f.read()
    except FileNotFoundError:
        base_prompt = "你是 SmartEats 智能助手，帮助用户解决「吃什么」的问题。"
    
    return (
        f"{base_prompt}\n\n"
        "## Runtime Context（系统注入，非用户输入）\n"
        f"- output_language: {settings.DEFAULT_LANGUAGE}\n"
        f"- context: {payload}"
    )


def _tool_result_handler(state: ChatState, tool_name: str, result: object) -> dict | None:
    """处理工具返回结果，更新状态或生成最终回复。"""
    if isinstance(result, dict):
        loc_source = result.get("location_source")
        if isinstance(loc_source, str) and loc_source:
            state.location_source = loc_source
    if isinstance(state.context, dict):
        ctx_loc_source = state.context.get("location_source")
        if isinstance(ctx_loc_source, str) and ctx_loc_source:
            state.location_source = ctx_loc_source
    
    # get_fridge_items: 仅缓存食材，让 LLM 结合上下文自主组织回复
    if tool_name == "get_fridge_items" and isinstance(result, dict):
        items = result.get("items") if isinstance(result.get("items"), list) else []
        if state.context is None:
            state.context = {}
        state.context["fridge_items"] = items
        return None
    if tool_name == "search_restaurants":
        if isinstance(result, dict) and result.get("error") == "missing_location":
            return FinalAnswer(
                recommendations=[
                    {
                        "type": "note",
                        "title": "先告诉我你想吃什么，我先按口味帮你筛一批。",
                        "reason": "定位暂时不可用时，可先按口味/预算推荐，随后再结合定位优化距离。",
                    }
                ],
                followups=["你想吃什么口味？", "预算大概多少？", "如果允许定位，我可以按最近距离再排序。"],
                warnings=[],
            ).model_dump()
        return None
    if tool_name in {"get_ip_location", "geocode_location"} and isinstance(result, dict):
        if result.get("error"):
            return FinalAnswer(
                recommendations=[
                    {
                        "type": "note",
                        "title": "我刚尝试了自动定位，但没拿到有效位置。",
                        "reason": "定位信息不足。",
                    }
                ],
                followups=[
                    "告诉我你所在的城市/地标？",
                    "或在浏览器允许定位，我就能按距离推荐附近餐厅。",
                ],
                warnings=[],
            ).model_dump()
        if state.context is None:
            state.context = {}
        location = {"lat": result.get("lat"), "lng": result.get("lng")}
        state.context["location"] = location
        if result.get("city"):
            state.context["city"] = result.get("city")
        return None
    if tool_name == "search_recipes" and isinstance(result, list):
        return None
    if tool_name == "rag_search_recipes" and isinstance(result, dict):
        items = result.get("items") if isinstance(result.get("items"), list) else []
        error = result.get("error")
        if error and not items:
            # RAG 出错且无结果 → 返回 None 让 LLM 兜底生成菜谱
            return None
        if not items:
            # 没有匹配结果 → 返回 None 让 LLM 直接生成菜谱
            return None
        return None
    if tool_name == "plan_route" and isinstance(result, dict):
        error = result.get("error")
        if error == "missing_origin":
            return FinalAnswer(
                recommendations=[
                    {
                        "type": "note",
                        "title": "还需要你的出发位置，才能规划路线。",
                        "reason": "起点信息缺失。",
                    }
                ],
                followups=["告诉我你的出发地/地标？", "你现在在哪个城市或位置？"],
                warnings=[],
            ).model_dump()
        if error == "missing_destination":
            return FinalAnswer(
                recommendations=[
                    {
                        "type": "note",
                        "title": "还需要你的目的地，才能规划路线。",
                        "reason": "终点信息缺失。",
                    }
                ],
                followups=["想去哪儿？给我目的地名称。"],
                warnings=[],
            ).model_dump()
        if error:
            return FinalAnswer(
                recommendations=[
                    {"type": "note", "title": "路线规划失败", "reason": "暂时无法获取路线信息。"}
                ],
                followups=["换个出发地或目的地试试？"],
                warnings=[],
            ).model_dump()

        # 路线成功时仅保留工具观察结果，让 LLM 自主组织最终表达（半放权）
        return None
    return None


@register_agent
def _smart_eats_agent() -> AgentConfig:
    return create_agent_config(
        name="smart_eats",
        scene="chat",
        tool_names=[
            "get_weather",
            "get_fridge_items",
            "search_recipes",
            "rag_search_recipes",
            "search_restaurants",
            "plan_route",
            "get_ip_location",
            "geocode_location",
            "get_user_info",
        ],
        max_steps=4,
        system_prompt_builder=smart_system_prompt,
        writer_prompt_builder=default_writer_prompt,
        tool_result_handler=_tool_result_handler,
        intent_resolver=smart_intent_resolver,
        context_extender=smart_context_extender,
        tool_plan_router=smart_tool_plan_router,
        tool_args_normalizer=smart_tool_args_normalizer,
        serial_execution_decider=smart_serial_execution_decider,
        tool_result_previewer=smart_tool_result_previewer,
        final_action_hook=smart_final_action_hook,
        fast_path_decider=smart_fast_path_decider,
        fast_path_system_prompt_builder=smart_fast_path_system_prompt_builder,
        fast_path_writer_prompt_builder=smart_fast_path_writer_prompt_builder,
    )

