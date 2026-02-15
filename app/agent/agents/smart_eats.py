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
    route_tokens = ("怎么走", "路线", "导航", "去那里", "到")
    
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
    if state.intent != "eat_out":
        return None
    message = (state.message or "").strip()
    generic = {"出去吃", "外出用餐", "找餐厅", "附近餐厅", "吃饭", "吃点什么"}
    if message not in generic:
        return None
    # 原子化：先获取位置，而不是直接调用 search_restaurants
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
    
    # get_fridge_items: 缓存食材到 context
    # 冰箱有食材时继续让 Agent 调用 rag_search_recipes
    # 冰箱为空时直接返回提示，引导用户说出想做的菜
    if tool_name == "get_fridge_items" and isinstance(result, dict):
        items = result.get("items") if isinstance(result.get("items"), list) else []
        if state.context is None:
            state.context = {}
        state.context["fridge_items"] = items
        if not items:
            # 冰箱为空：提醒用户并引导输入想做的菜
            return FinalAnswer(
                recommendations=[
                    {
                        "type": "note",
                        "title": "你的冰箱里暂时没有食材记录哦～",
                        "reason": "你可以去「冰箱」页面添加食材，我就能根据现有食材推荐菜谱啦！"
                                  "或者直接告诉我你想做什么菜，我来帮你生成菜谱 🍳",
                    }
                ],
                followups=["想做什么菜？告诉我菜名我来出菜谱", "去冰箱页面添加食材", "随便推荐几道家常菜吧"],
                warnings=[],
            ).model_dump()
        # 有食材，继续让 Agent 决定下一步（调用 rag_search_recipes）
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
        if isinstance(result, list) and not result:
            return FinalAnswer(
                recommendations=[
                    {
                        "type": "note",
                        "title": "附近没有找到合适的餐厅。",
                        "reason": "可以换个口味或更具体的关键字。",
                    }
                ],
                followups=["想吃什么菜系？", "要不要换个更大的范围？"],
                warnings=[],
            ).model_dump()
        if isinstance(result, list):
            cards = []
            unlocated = state.location_source in {None, "text_search_without_location"}
            for item in result:
                cards.append(
                    {
                        "type": "restaurant",
                        "title": item.get("name") or item.get("title"),
                        "reason": "按热度推荐（未定位）" if unlocated else "附近餐厅推荐",
                        "rating": item.get("rating"),
                        "price": item.get("price"),
                        "tags": item.get("tags") or [],
                        "geo": item.get("geo"),
                    }
                )
            return FinalAnswer(
                recommendations=cards,
                followups=(
                    ["想换一种口味吗？", "要不要更便宜一点？", "允许定位后我可以按距离重新排序。"]
                    if unlocated
                    else ["想换一种口味吗？", "要不要更便宜一点？"]
                ),
                warnings=[],
            ).model_dump()
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
        fridge_items = []
        if state.context:
            fridge_items = state.context.get("fridge_items") or []
        fridge_names = [
            str(item.get("name") or "")
            for item in fridge_items
            if isinstance(item, dict) and item.get("name")
        ]
        fridge_names_lower = [name.lower() for name in fridge_names]

        def _score(item: dict) -> int:
            title = (item.get("title") or "").lower()
            return sum(1 for name in fridge_names_lower if name in title)

        ranked = sorted(result, key=_score, reverse=True) if fridge_names else result
        cards = []
        for item in ranked[:3]:
            title_lower = (item.get("title") or "").lower()
            matched = [
                name
                for name, name_lower in zip(fridge_names, fridge_names_lower)
                if name_lower in title_lower
            ]
            reason = "根据冰箱食材推荐" if matched else "适合在家做"
            if matched:
                reason = f"匹配食材：{', '.join(matched[:3])}"
            cards.append(
                {
                    "type": "recipe",
                    "title": item.get("title"),
                    "reason": reason,
                    "calories": item.get("calories"),
                    "time": item.get("time") or item.get("cook_time_min"),
                    "tags": item.get("tags") or [],
                    "image_url": item.get("image_url"),
                }
            )
        return FinalAnswer(
            recommendations=cards,
            followups=["要不要更快手的？", "能接受辣吗？"],
            warnings=[],
        ).model_dump()
    if tool_name == "rag_search_recipes" and isinstance(result, dict):
        items = result.get("items") if isinstance(result.get("items"), list) else []
        error = result.get("error")
        if error and not items:
            # RAG 出错且无结果 → 返回 None 让 LLM 兜底生成菜谱
            return None
        if not items:
            # 没有匹配结果 → 返回 None 让 LLM 直接生成菜谱
            return None
        
        # 判断是否为"指定菜名"场景：
        # 1. intent == confirm_recipe（确认选择）
        # 2. 只返回 1 个结果（top_k=1，用户指定了具体菜名）
        is_specific_dish = state.intent == "confirm_recipe" or len(items) == 1
        
        if is_specific_dish:
            # 用户指定了具体菜名，需要返回详细做法
            item = items[0]
            title = item.get("title") or ""
            metadata = item.get("metadata") or {}
            ingredients = metadata.get("ingredients") or []
            steps = metadata.get("steps") or []
            
            if steps:
                # 有步骤数据，直接使用
                if isinstance(steps, list):
                    reason = "做法：\n" + "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps))
                else:
                    reason = f"做法：{steps}"
                
                return FinalAnswer(
                    recommendations=[
                        {
                            "type": "recipe",
                            "title": title,
                            "reason": reason,
                            "calories": metadata.get("calories"),
                            "time": metadata.get("time") or metadata.get("cook_time_min"),
                            "tags": metadata.get("tags") or [],
                            "image_url": metadata.get("image_url"),
                            "ingredients": ingredients,
                        }
                    ],
                    followups=["需要更详细的步骤吗？", "想知道注意事项吗？"],
                    warnings=[],
                ).model_dump()
            else:
                # 无步骤数据，返回 None 让 LLM 根据菜名 + 食材生成完整菜谱
                return None
        
        # 多结果推荐模式：获取冰箱食材用于匹配排序
        fridge_items = []
        if state.context:
            fridge_items = state.context.get("fridge_items") or []
        fridge_names = [
            str(item.get("name") or "")
            for item in fridge_items
            if isinstance(item, dict) and item.get("name")
        ]
        fridge_names_lower = [name.lower() for name in fridge_names]

        cards = []
        for item in items[:5]:
            title = item.get("title") or ""
            snippet = item.get("snippet") or ""
            metadata = item.get("metadata") or {}
            title_lower = title.lower()
            # 匹配冰箱食材
            matched = [
                name
                for name, name_lower in zip(fridge_names, fridge_names_lower)
                if name_lower in title_lower or name_lower in snippet.lower()
            ]
            if matched:
                reason = f"匹配食材：{', '.join(matched[:3])}"
            else:
                reason = metadata.get("description") or "适合在家做"
            cards.append(
                {
                    "type": "recipe",
                    "title": title,
                    "reason": reason,
                    "calories": metadata.get("calories"),
                    "time": metadata.get("time") or metadata.get("cook_time_min"),
                    "tags": metadata.get("tags") or [],
                    "image_url": metadata.get("image_url"),
                }
            )
        return FinalAnswer(
            recommendations=cards,
            followups=["要不要更快手的？", "能接受辣吗？"],
            warnings=[],
        ).model_dump()
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

        distance = result.get("distance_m")
        duration = result.get("duration_s")
        steps = result.get("steps")
        mode = str(result.get("mode") or "").strip().lower()
        distance_km = None
        distance_m = None
        duration_min = None
        fallback_from = result.get("fallback_from")
        try:
            if distance is not None:
                distance_m = float(distance)
                distance_km = distance_m / 1000
        except (TypeError, ValueError):
            distance_m = None
            distance_km = None
        try:
            if duration is not None:
                duration_min = float(duration) / 60
        except (TypeError, ValueError):
            duration_min = None
        summary = "路线规划完成"
        mode_label = _route_mode_label(mode)
        distance_label = None
        if distance_m is not None:
            if distance_m < 1000:
                distance_label = f"{distance_m:.0f}米"
            else:
                distance_label = f"{distance_km:.1f}公里"
        if distance_label is not None and duration_min is not None:
            summary = f"{mode_label}预计{distance_label}，约{duration_min:.0f}分钟"
        elif distance_label is not None:
            summary = f"{mode_label}预计{distance_label}"
        elif duration_min is not None:
            summary = f"{mode_label}预计约{duration_min:.0f}分钟"
        if fallback_from:
            summary = f"{summary}（距离过远，已切换为驾车路线）"
        detail_lines = []
        if isinstance(steps, list):
            for idx, step in enumerate(steps, start=1):
                if not isinstance(step, str):
                    continue
                detail_lines.append(f"{idx}. {step}")
                if len(detail_lines) >= 8:
                    break
        reason = summary
        if detail_lines:
            reason = f"{summary}\n" + "\n".join(detail_lines)
        return FinalAnswer(
            recommendations=[
                {
                    "type": "note",
                    "title": "路线建议",
                    "reason": reason,
                }
            ],
            followups=["需要换一种出行方式吗？", "是否需要查看途经餐厅？"],
            warnings=[],
        ).model_dump()
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


def _route_mode_label(mode: str) -> str:
    if mode in {"walking", "walk"}:
        return "步行"
    if mode in {"bicycling", "cycling", "bike"}:
        return "骑行"
    if mode in {"transit", "bus", "public"}:
        return "公交"
    if mode in {"driving", "drive", "car"}:
        return "驾车"
    return "路线"
