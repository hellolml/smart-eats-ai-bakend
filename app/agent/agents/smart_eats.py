from __future__ import annotations

import os
import re
import logging
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

logger = logging.getLogger("agent.intent")


def _set_task_stage(state: ChatState, next_stage: str, *, cause: str) -> None:
    prev = state.task_stage or "unknown"
    if prev != next_stage:
        logger.info(
            "stage_transition session_id=%s from=%s to=%s cause=%s",
            state.session_id,
            prev,
            next_stage,
            cause,
        )
    state.task_stage = next_stage


def _looks_like_location_update(text: str) -> bool:
    tokens = (
        "我在", "在这", "在这里", "在那", "在那边", "定位", "位置", "地址", "附近", "广场", "路", "街", "号", "小区", "商场", "地铁", "站",
    )
    if any(token in text for token in tokens):
        return True
    return bool(re.search(r"[省市区县镇乡村路街道号弄巷]", text))


def _looks_like_explicit_address(text: str) -> bool:
    tokens = (
        "我在", "在这", "在这里", "在那", "在那边", "地址", "广场", "路", "街", "号", "小区", "商场", "地铁", "站", "省", "市", "区", "县", "镇", "乡", "村",
    )
    if any(token in text for token in tokens):
        return True
    return bool(re.search(r"[省市区县镇乡村路街道号弄巷]", text))


def _looks_like_food_preference(text: str) -> bool:
    food_tokens = (
        "火锅", "烧烤", "麻辣烫", "米粉", "面馆", "烤肉", "串串", "小龙虾", "川菜", "粤菜", "湘菜", "日料", "韩餐", "快餐",
    )
    return any(token in text for token in food_tokens)


def _extract_food_preference(text: str) -> str | None:
    food_tokens = (
        "火锅", "烧烤", "麻辣烫", "米粉", "面馆", "烤肉", "串串", "小龙虾", "川菜", "粤菜", "湘菜", "日料", "韩餐", "快餐",
    )
    for token in food_tokens:
        if token in text:
            return token
    return None


def _looks_like_eat_out_request(text: str) -> bool:
    tokens = (
        "美食", "吃什么", "吃点", "好吃", "餐厅", "饭店", "馆子", "找吃的", "找店", "下馆子", "附近吃", "口味",
    )
    return any(token in text for token in tokens) or _looks_like_food_preference(text)


def _extract_geocode_query(text: str) -> str:
    candidate = (text or "").strip()
    if not candidate:
        return candidate

    candidate = re.split(r"[，。！？!?；;]", candidate, maxsplit=1)[0].strip()

    for prefix in ("我在", "在", "我想去", "想去", "帮我找", "帮我搜", "帮我查", "帮我看看", "搜一下", "搜索", "查一下", "查找", "找一下", "找找"):
        if candidate.startswith(prefix) and len(candidate) > len(prefix):
            candidate = candidate[len(prefix):].strip()
            break

    for suffix in ("附近吃什么", "附近有啥吃的", "附近有什么吃的", "附近美食", "美食", "好吃的", "餐厅", "饭店", "吃什么", "找吃的", "下馆子", "推荐"):
        if candidate.endswith(suffix) and len(candidate) > len(suffix):
            candidate = candidate[: -len(suffix)].strip(" ，。,.!?？！")
            break

    if candidate.endswith("附近") and len(candidate) > 2:
        candidate = candidate[:-2].strip()

    return candidate or (text or "").strip()


def _recent_eat_out_context(history: list[dict[str, Any]]) -> bool:
    for msg in reversed(history[-8:]):
        role = (msg.get("role") or "").lower()
        content = str(msg.get("content") or "")
        if role == "user" and any(t in content for t in ("出去吃", "附近吃", "找餐厅", "下馆子", "吃饭", "美食")):
            return True
        if role == "assistant" and any(t in content for t in ("附近", "餐厅", "位置", "地标", "城市", "美食")):
            return True
    return False


def smart_intent_resolver(state: ChatState) -> str | None:
    import json

    text = (state.message or "").strip().lower()
    if not text:
        intent = "unknown"
        logger.info("intent_reason session_id=%s intent=%s reason=empty_message", state.session_id, intent)
        return intent

    # 模型优先判意图：规则层只处理“高置信显式意图”，其余交给 planner。
    eat_out_tokens = ("出去吃", "外出", "附近餐厅", "找餐厅", "下馆子", "美食")
    cook_home_tokens = ("在家做", "做饭", "菜谱", "食谱", "冰箱")
    route_tokens = ("怎么走", "路线", "导航", "去那里", "去那儿", "到这家", "带我去", "怎么去")
    greeting_tokens = ("你好", "hi", "hello", "嗨", "在吗")

    # 1) 高置信规则：路线/在家做/出去吃显式表达
    if any(token in text for token in route_tokens):
        intent = "route"
        logger.info("intent_reason session_id=%s intent=%s reason=route_tokens text=%s", state.session_id, intent, text)
        return intent
    if any(token in text for token in cook_home_tokens):
        intent = "cook_home"
        logger.info("intent_reason session_id=%s intent=%s reason=cook_home_tokens text=%s", state.session_id, intent, text)
        return intent
    if any(token in text for token in eat_out_tokens):
        intent = "eat_out"
        logger.info("intent_reason session_id=%s intent=%s reason=eat_out_tokens text=%s", state.session_id, intent, text)
        return intent

    # 2) 地址表达或“去哪吃”表达，直接归到外出就餐
    if _looks_like_explicit_address(text):
        intent = "eat_out"
        logger.info("intent_reason session_id=%s intent=%s reason=explicit_address text=%s", state.session_id, intent, text)
        return intent
    if _looks_like_eat_out_request(text):
        intent = "eat_out"
        logger.info("intent_reason session_id=%s intent=%s reason=eat_out_request text=%s", state.session_id, intent, text)
        return intent

    # 3) 语境兜底：上一轮在外出就餐，当前是地址更新或菜系偏好
    if _looks_like_location_update(text) and _recent_eat_out_context(state.history):
        intent = "eat_out"
        logger.info("intent_reason session_id=%s intent=%s reason=location_update_in_eat_out_context text=%s", state.session_id, intent, text)
        return intent
    if _looks_like_food_preference(text) and _recent_eat_out_context(state.history):
        intent = "eat_out"
        logger.info("intent_reason session_id=%s intent=%s reason=food_preference_in_eat_out_context text=%s", state.session_id, intent, text)
        return intent

    # 4) 检查是否是确认选择（用户提到之前推荐的菜名）
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
            intent = "confirm_recipe"
            logger.info("intent_reason session_id=%s intent=%s reason=recipe_title_confirm matched=%s", state.session_id, intent, title)
            return intent

    if any(token == text for token in greeting_tokens):
        intent = "chat"
        logger.info("intent_reason session_id=%s intent=%s reason=greeting text=%s", state.session_id, intent, text)
        return intent

    # 其余交给 planner 自主判意图与工具决策
    intent = "unknown"
    logger.info("intent_reason session_id=%s intent=%s reason=delegate_to_planner text=%s", state.session_id, intent, text)
    return intent


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
    base = smart_system_prompt({"context": state.context or {}})
    return (
        f"{base}\n\n"
        "## Fast Path 输出约束\n"
        "- 在无需工具调用时，直接用自然语言回答用户。\n"
        "- 禁止输出 JSON、代码块、字段名或结构化包装。\n"
        "- 若问题需要实时信息/定位/检索能力，请遵循 system.md 的工具流程。"
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


def _extract_location_from_observations_by_tool(
    observations: list[dict[str, Any]],
    tool_name: str,
) -> tuple[float | None, float | None]:
    for item in reversed(observations):
        if not isinstance(item, dict) or item.get("tool") != tool_name:
            continue
        result = item.get("result")
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

    food_preference = _extract_food_preference(text)
    if food_preference:
        return food_preference

    # 用户在更新地址时，不应把整句地址当搜索关键词，默认回退为通用美食检索
    if _looks_like_location_update(text):
        return "美食"

    generic_phrases = {
        "出去吃",
        "附近吃什么",
        "附近有啥吃的",
        "吃什么",
        "找吃的",
        "下馆子",
        "附近餐厅",
        "推荐",
        "推荐一下",
        "推荐几个",
    }
    if text in generic_phrases:
        return "美食"

    # 口语偏好句归一化：我想吃火锅 -> 火锅
    for prefix in ("我想吃", "想吃", "想来点", "来点", "想整点"):
        if text.startswith(prefix) and len(text) > len(prefix):
            candidate = text[len(prefix):].strip(" ，。,.!?？！")
            if candidate:
                return candidate

    return text


def _has_tool_observation(observations: list[dict[str, Any]], tool_name: str) -> bool:
    for item in observations:
        if isinstance(item, dict) and item.get("tool") == tool_name:
            return True
    return False


def _tool_observation_count(observations: list[dict[str, Any]], tool_name: str) -> int:
    count = 0
    for item in observations:
        if isinstance(item, dict) and item.get("tool") == tool_name:
            count += 1
    return count


def smart_tool_plan_router(state: ChatState) -> list[dict[str, Any]] | None:
    """结构化守门：只保证关键依赖与阶段推进，其余交给 LLM。"""
    if state.intent != "eat_out":
        return None

    text = (state.message or "").strip()
    has_explicit_location = _looks_like_explicit_address(text)
    geocode_count = _tool_observation_count(state.observations, "geocode_location")
    search_count = _tool_observation_count(state.observations, "search_restaurants")
    ip_count = _tool_observation_count(state.observations, "get_ip_location")

    # stage 1: 用户给了明确地址 -> geocode
    if has_explicit_location and geocode_count == 0:
        geocode_query = _extract_geocode_query(text)
        _set_task_stage(state, "need_geocode", cause="explicit_location")
        logger.info("router_decision session_id=%s stage=%s reason=explicit_location tool=geocode_location query=%s", state.session_id, state.task_stage, geocode_query)
        return [{"name": "geocode_location", "args": {"query": geocode_query}}]

    # stage 2: geocode 完成后，强制用 geocode 坐标搜索一次
    if has_explicit_location and geocode_count >= 1 and search_count == 0:
        lat, lng = _extract_location_from_observations_by_tool(state.observations, "geocode_location")
        if lat is not None and lng is not None:
            _set_task_stage(state, "location_ready", cause="geocode_resolved")
            logger.info("router_decision session_id=%s stage=%s reason=use_geocoded_location tool=search_restaurants lat=%s lng=%s", state.session_id, state.task_stage, lat, lng)
            return [{"name": "search_restaurants", "args": {"query": _normalize_restaurant_query(state.message), "lat": lat, "lng": lng}}]

    lat_ctx, lng_ctx = _extract_location_from_context(state.context)
    lat_obs, lng_obs = _extract_location_from_observations(state.observations)
    has_any_location = (lat_ctx is not None and lng_ctx is not None) or (lat_obs is not None and lng_obs is not None)

    # stage 3: 没位置才兜底 IP
    if not has_any_location and ip_count == 0:
        _set_task_stage(state, "need_location", cause="missing_location")
        logger.info("router_decision session_id=%s stage=%s reason=no_location tool=get_ip_location", state.session_id, state.task_stage)
        return [{"name": "get_ip_location", "args": {}}]

    # stage 4: 有位置但还没搜，补一次搜索
    if has_any_location and search_count == 0:
        if lat_obs is not None and lng_obs is not None:
            lat, lng = lat_obs, lng_obs
            source = "observations"
        else:
            lat, lng = lat_ctx, lng_ctx
            source = "context"
        if lat is not None and lng is not None:
            _set_task_stage(state, "location_ready", cause="location_available")
            logger.info("router_decision session_id=%s stage=%s reason=have_location_no_search tool=search_restaurants source=%s lat=%s lng=%s", state.session_id, state.task_stage, source, lat, lng)
            return [{"name": "search_restaurants", "args": {"query": _normalize_restaurant_query(state.message), "lat": lat, "lng": lng}}]

    # 已经完成搜索，或者无需守门：交给 LLM 自由规划回应
    if search_count > 0:
        _set_task_stage(state, "searched", cause="search_completed")
    logger.info("router_decision session_id=%s stage=%s reason=delegate_to_llm", state.session_id, state.task_stage or "unknown")
    return None


def smart_context_extender(state: ChatState) -> dict:
    """扩展 LLM 上下文，注入业务相关字段。"""
    extra = {}
    if state.intent:
        extra["intent"] = state.intent
    if state.location_source:
        extra["location_source"] = state.location_source
    if state.task_stage:
        extra["task_stage"] = state.task_stage
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
        if "query" not in updated and isinstance(updated.get("keyword"), str):
            updated["query"] = updated.pop("keyword")

        location = updated.get("location")
        if isinstance(location, dict):
            lat = location.get("lat")
            lng = location.get("lng")
            if "lat" not in updated:
                updated["lat"] = lat
            if "lng" not in updated:
                updated["lng"] = lng
            updated.pop("location", None)

        # 当前 search_restaurants 工具未使用 radius，避免无效参数干扰
        updated.pop("radius", None)

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
        _set_task_stage(state, "searched", cause="tool_result_search_restaurants")
        if isinstance(result, dict):
            if result.get("error") == "missing_location":
                return FinalAnswer(
                    recommendations=[
                        {
                            "type": "note",
                            "title": "我还没拿到你的位置，先告诉我城市或地标，我马上给你筛附近店。",
                            "reason": "定位暂时不可用。",
                        }
                    ],
                    followups=["你现在在哪个城市？", "附近有什么地标/商场？", "也可以开启定位后再试一次。"],
                    warnings=[],
                ).model_dump()
            return FinalAnswer(
                recommendations=[
                    {
                        "type": "note",
                        "title": "我刚搜附近餐厅时出了点小问题。",
                        "reason": "检索服务暂时异常。",
                    }
                ],
                followups=["你想吃什么口味？我先按偏好给你一版。", "稍后我也可以再帮你重试附近搜索。"],
                warnings=[],
            ).model_dump()
        if isinstance(result, list) and not result:
            return FinalAnswer(
                recommendations=[
                    {
                        "type": "note",
                        "title": "我按你当前位置搜了下，附近暂时没刷到合适的餐厅。",
                        "reason": "可扩大范围或换关键词继续找。",
                    }
                ],
                followups=["要不要我把范围放大到 5-10 公里？", "或者你说个口味（比如火锅/烧烤/粤菜），我再精确筛。"],
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
        _set_task_stage(state, "location_ready", cause="tool_result_location")
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

