from __future__ import annotations

import re
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agent.agent_state import agent_context_from_mapping
from app.agent.contracts import (
    AgentRouteDecision,
    build_agent_run_result,
    final_json_for_failure,
)
from app.agent.intent import infer_chat_intent, infer_food_worker_intent
from app.agent.runtime.graph import AgentRuntimeGraphState
from app.agent.supervisor.workers import WORKER_SPECS, build_worker_agent


TRAVEL_SCENES = {"travel", "travel_planner"}
FOOD_SCENES = {"eat", "food_decision", "restaurant"}
HOME_SCENES = {"home", "home_chef", "cook_home"}
ROUTE_SCENES = {"route", "navigation"}
TRAVEL_INTENT_KEYWORDS = (
    "旅行",
    "旅游",
    "攻略",
    "行程",
    "自由行",
    "怎么玩",
    "几日游",
    "一日游",
    "两日游",
    "三日游",
    "周末出去玩",
    "景点",
    "酒店",
    "生成地图",
)


def _runtime_diagnostics_from_output(output: dict[str, Any]) -> dict[str, Any]:
    context = output.get("context")
    if not isinstance(context, dict):
        return {}
    diagnostics: dict[str, Any] = {}
    active_tools = context.get("allowed_tools")
    if isinstance(active_tools, list):
        diagnostics["active_tools"] = [item for item in active_tools if isinstance(item, str)]
    active_skills = context.get("active_skills")
    if isinstance(active_skills, list):
        diagnostics["active_skills"] = [item for item in active_skills if isinstance(item, dict)]
    skill_diagnostics = context.get("skill_diagnostics")
    if isinstance(skill_diagnostics, dict):
        diagnostics["skill_diagnostics"] = skill_diagnostics
    diagnostics.update(_model_diagnostics(output.get("provider"), output.get("resolved_model_config")))
    return diagnostics


def _model_diagnostics(provider: Any, resolved_model_config: Any) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {}
    model_config: dict[str, Any] = {}
    if isinstance(provider, str) and provider:
        model_config["provider_value"] = provider
    if isinstance(resolved_model_config, dict):
        for key in (
            "source",
            "provider",
            "provider_value",
            "config_id",
            "display_name",
            "base_url",
            "model_planner",
            "model_writer",
            "model_vision_planner",
        ):
            value = resolved_model_config.get(key)
            if value not in (None, "", [], {}):
                model_config[key] = value
    if model_config:
        diagnostics["model_config"] = model_config
    return diagnostics


def _message_text(state: dict[str, Any]) -> str:
    message = state.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    messages = state.get("messages")
    if isinstance(messages, list):
        for item in reversed(messages):
            content = getattr(item, "content", None)
            if isinstance(content, str) and content.strip():
                return content.strip()
    return ""


def _client_context(state: dict[str, Any]) -> dict[str, Any]:
    context = state.get("context_overrides")
    return context if isinstance(context, dict) else {}


def _has_attachments(state: dict[str, Any], context: dict[str, Any]) -> bool:
    for value in (state.get("attachments"), context.get("attachments")):
        if isinstance(value, list) and any(isinstance(item, dict) for item in value):
            return True
    payload = state.get("payload") if isinstance(state.get("payload"), dict) else {}
    travel_payload = state.get("travel_payload") if isinstance(state.get("travel_payload"), dict) else {}
    for value in (payload.get("new_attachments"), travel_payload.get("new_attachments")):
        if isinstance(value, list) and any(isinstance(item, dict) for item in value):
            return True
    return False


def _is_travel_intent(message: str) -> bool:
    return any(token in message for token in TRAVEL_INTENT_KEYWORDS)


def _is_navigation_intent(message: str) -> bool:
    if _negates_navigation_intent(message):
        return False
    if any(token in message for token in ("导航", "怎么走", "怎么去", "带我去", "前往")):
        return True
    if "路线" not in message:
        return False
    return any(token in message for token in ("从", "到", "去", "开车", "步行", "骑行", "地铁"))


def _negates_navigation_intent(message: str) -> bool:
    return any(
        token in str(message or "")
        for token in (
            "不要规划路线",
            "先不要规划路线",
            "不用规划路线",
            "先不用规划路线",
            "不需要路线",
            "暂时不规划路线",
            "先不规划路线",
        )
    )


def _is_structured_travel_request(message: str) -> bool:
    return any(token in message for token in ("目的地", "出行时间", "出行天数", "旅行天数", "游玩天数", "候选行程", "每日行程"))


def _is_travel_scene_food_followup(message: str) -> bool:
    if _is_travel_revision_intent(message) or _is_primary_travel_planning_request(message):
        return False
    if not any(token in message for token in ("附近", "周边", "餐厅", "饭店", "吃什么", "好吃的", "找几家")):
        return False
    if "附近" in message and not any(token in message for token in ("附近找", "附近有什么好吃", "附近餐厅", "附近饭店", "附近吃")):
        return False
    return any(token in message for token in ("吃", "餐厅", "饭店", "火锅", "小吃", "烧烤", "湘菜", "粤菜"))


def _is_primary_travel_planning_request(message: str) -> bool:
    text = str(message or "")
    if not _is_travel_intent(text):
        return False
    return any(token in text for token in ("旅行计划", "旅游计划", "旅行攻略", "旅游攻略", "行程计划", "几天", "天旅行", "天旅游"))


def _is_travel_revision_intent(message: str) -> bool:
    text = str(message or "")
    return any(token in text for token in ("改成", "不去", "少走", "膝盖", "不要太早", "行程", "每日安排", "每日行程"))


def _is_general_chat_override(message: str) -> bool:
    text = str(message or "")
    return any(
        token in text
        for token in (
            "先别管吃饭",
            "先不聊吃饭",
            "先别管旅行",
            "先不聊旅行",
            "先别管行程",
            "先不聊行程",
            "陪我随便聊",
            "随便聊一句",
            "陪我聊一句",
            "陪我吐槽",
        )
    )


def _message_selects_recent_restaurant(message: str, context: dict[str, Any]) -> bool:
    hints = _selection_hints(message)
    text = _normalize_selection_text(message)
    restaurants = context.get("last_restaurants")
    if not text or not isinstance(restaurants, list):
        return False
    if restaurants and _selection_index(message) is not None:
        return True
    for item in restaurants:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("title") or "").strip()
        for alias in _restaurant_aliases(name):
            if alias and alias in text:
                return True
            if hints and any(hint in alias for hint in hints):
                return True
    return False


def _message_is_restaurant_selection_followup(message: str, context: dict[str, Any]) -> bool:
    restaurants = context.get("last_restaurants")
    selected = context.get("selected_restaurant")
    if not (isinstance(restaurants, list) and restaurants) and not isinstance(selected, dict):
        return False
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


def _restaurant_aliases(name: str) -> list[str]:
    aliases: list[str] = []
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


def route_agent_request(state: dict[str, Any]) -> AgentRouteDecision:
    scene = str(state.get("scene") or "chat").strip() or "chat"
    context = _client_context(state)
    context_model = agent_context_from_mapping(context)
    explicit_intent = getattr(context_model, "intent", None) if context_model else context.get("intent")
    message = _message_text(state)
    chat_intent = infer_chat_intent(message)
    food_intent = infer_food_worker_intent(message, explicit_intent=explicit_intent if isinstance(explicit_intent, str) else None)

    if scene in ROUTE_SCENES or explicit_intent == "route" or _is_navigation_intent(message):
        return AgentRouteDecision(
            worker="route_planner",
            intent="route",
            confidence=0.9,
            reason="route_intent",
        )

    if _is_general_chat_override(message):
        return AgentRouteDecision(
            worker="general_chat",
            intent="chat",
            confidence=0.9,
            reason="explicit_general_chat",
        )

    if scene in TRAVEL_SCENES and (
        state.get("travel_action")
        or state.get("travel_payload")
        or _has_attachments(state, context)
        or _is_structured_travel_request(message)
        or _is_primary_travel_planning_request(message)
        or _is_travel_revision_intent(message)
        or (_is_travel_intent(message) and not _is_travel_scene_food_followup(message))
    ) and not (_message_selects_recent_restaurant(message, context) or _message_is_restaurant_selection_followup(message, context)):
        return AgentRouteDecision(
            worker="travel_planner",
            intent="travel",
            confidence=0.95,
            reason="travel_scene_context",
        )

    if _message_selects_recent_restaurant(message, context) or _message_is_restaurant_selection_followup(message, context):
        return AgentRouteDecision(
            worker="food_advisor",
            intent="eat_out",
            confidence=0.95,
            reason="recent_restaurant_selection",
        )

    if scene in HOME_SCENES or food_intent == "cook_home":
        return AgentRouteDecision(
            worker="home_chef",
            intent="cook_home",
            confidence=0.9,
            reason="home_cooking_intent",
        )

    if scene in FOOD_SCENES or explicit_intent in {"food", "eat_out"} or food_intent in {"eat_out", "decide_food"} or chat_intent == "food":
        return AgentRouteDecision(
            worker="food_advisor",
            intent=food_intent or "eat_out",
            confidence=0.9,
            reason="food_intent",
        )

    if (
        scene in TRAVEL_SCENES
        or state.get("travel_action")
        or state.get("travel_payload")
        or _has_attachments(state, context)
        or _is_travel_intent(message)
    ):
        return AgentRouteDecision(
            worker="travel_planner",
            intent="travel",
            confidence=0.95,
            reason="scene_or_travel_payload",
        )

    return AgentRouteDecision(
        worker="general_chat",
        intent="chat",
        confidence=0.75,
        reason="default_chat",
    )


def build_supervisor_runtime_graph(
    db: Any,
    redis_client: Any,
    provider: str | None = None,
    resolved_model_config: dict[str, Any] | None = None,
    model: Any | None = None,
    planner: Any | None = None,
    tool_node: Any | None = None,
) -> Any:
    worker_graphs = {
        spec.name: build_worker_agent(
            spec,
            db=db,
            redis_client=redis_client,
            provider=provider,
            resolved_model_config=resolved_model_config,
            planner=planner,
            tool_node=tool_node,
        )
        for spec in WORKER_SPECS
    }

    async def route_node(state: dict[str, Any]) -> dict[str, Any]:
        decision = route_agent_request(state)
        return {"route_decision": decision.model_dump()}

    async def worker_node(state: dict[str, Any]) -> dict[str, Any]:
        route_payload = state.get("route_decision") if isinstance(state.get("route_decision"), dict) else {}
        worker_name = route_payload.get("worker")
        if not isinstance(worker_name, str) or worker_name not in worker_graphs:
            final_json = final_json_for_failure("route_no_worker")
            return {
                "final_json": final_json,
                "agent_result": build_agent_run_result(
                    final_json=final_json,
                    route_decision=route_payload,
                    worker=worker_name if isinstance(worker_name, str) else None,
                    failure_class="route_no_worker",
                ),
            }

        output = await worker_graphs[worker_name].ainvoke(state)
        if not isinstance(output, dict):
            final_json = final_json_for_failure("worker_no_final")
            return {
                "final_json": final_json,
                "agent_id": worker_name,
                "agent_result": build_agent_run_result(
                    final_json=final_json,
                    route_decision=route_payload,
                    worker=worker_name,
                    failure_class="worker_no_final",
                ),
            }

        final_json = output.get("final_json") if isinstance(output.get("final_json"), dict) else None
        if not final_json:
            final_json = final_json_for_failure("worker_no_final")
            output["final_json"] = final_json
        output["route_decision"] = route_payload
        output["agent_result"] = build_agent_run_result(
            final_json=final_json,
            route_decision=route_payload,
            worker=worker_name,
            diagnostics={
                **_runtime_diagnostics_from_output(output),
                **_model_diagnostics(provider, resolved_model_config),
                "tools": output.get("tool_calls") if isinstance(output.get("tool_calls"), list) else [],
                "worker": worker_name,
            },
        )
        return output

    async def finalize_node(state: dict[str, Any]) -> dict[str, Any]:
        final_json = state.get("final_json") if isinstance(state.get("final_json"), dict) else None
        route_payload = state.get("route_decision") if isinstance(state.get("route_decision"), dict) else None
        agent_result = state.get("agent_result") if isinstance(state.get("agent_result"), dict) else None
        if agent_result:
            return {}
        if not final_json:
            final_json = final_json_for_failure("worker_no_final")
        return {
            "final_json": final_json,
            "agent_result": build_agent_run_result(
                final_json=final_json,
                route_decision=route_payload,
                worker=(route_payload or {}).get("worker") if isinstance(route_payload, dict) else None,
            ),
        }

    graph = StateGraph(AgentRuntimeGraphState)
    graph.add_node("route", route_node)
    graph.add_node("worker", worker_node)
    graph.add_node("finalize", finalize_node)
    graph.add_edge(START, "route")
    graph.add_edge("route", "worker")
    graph.add_edge("worker", "finalize")
    graph.add_edge("finalize", END)
    return graph


def worker_names() -> list[str]:
    return [spec.name for spec in WORKER_SPECS]
