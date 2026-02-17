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


def smart_tool_plan_router(state: ChatState) -> list[dict] | None:
    """保留扩展点，但默认不做硬编码路由，让 LLM 自主决定是否调用工具。"""
    _ = state
    return None


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
                        "title": "需要你的具体位置，才能推荐附近餐厅。",
                        "reason": "定位信息不足。",
                    }
                ],
                followups=["告诉我你所在的城市/地标？"],
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
    )

