from __future__ import annotations

import asyncio
import os
import json
import re
from dataclasses import dataclass, field
import logging
from typing import Annotated, Any, Callable, TypedDict

from app.agent.agents.base import default_writer_prompt, normalize_action_from_raw
from app.common.config import settings
from app.agent.langgraph_context import (
    build_active_context_report,
    build_model_messages,
    build_summary_prompt,
    build_summary_repair_prompt,
    build_summary_update,
    detect_compact_thrash,
    load_user_memories,
    normalize_summary_output,
    parse_model_context_windows,
    persist_summary_memories,
    resolve_model_context_window,
    save_compaction_run,
    save_source_event,
    should_summarize_context,
)
from app.agent.schemas import FinalAction, FinalAnswer, FinalAnswerArgs
from app.agent import history
from app.agent.tools.location_cache import load_cached_location
from app.agent.tools.restaurant_cache import load_cached_restaurants
from langgraph.graph.message import add_messages

# 规则分层说明：
# - 代码规则（本文件）：可测试、可确定执行的逻辑（意图判定、工具编排、参数归一化、结果兜底）。
# - Prompt 规则（system.md）：给 LLM 的行为策略与表达规范。
# 系统提示词文件路径
SYSTEM_PROMPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "prompts", "system.md"
)

logger = logging.getLogger("agent.intent")


STATE_CONTEXT_KEYS: dict[str, str] = {
    "fridge_items": "fridge_items",
    "last_search_error": "last_search_error",
    "restaurant_retries": "restaurant_retries",
    "suggested_radius_km": "suggested_radius_km",
    "last_location_error": "last_location_error",
    "location": "location",
    "city": "city",
    "location_source": "location_source",
    "last_restaurants": "last_restaurants",
}

STATE_CONTEXT_OVERRIDE_KEYS: dict[str, str] = {
    "system_directive": "system_directive",
    "fridge_empty": "fridge_empty",
    "restaurant_search_retries": "restaurant_search_retries",
    "rag_recipe_hits": "rag_recipe_hits",
    "latest_route": "latest_route",
}

AGENT_METRIC_NAMES: dict[str, str] = {
    "location_resolution_failed": "location_resolution_failed",
    "location_resolution_success": "location_resolution_success",
    "restaurant_search_error": "restaurant_search_error",
    "restaurant_search_empty": "restaurant_search_empty",
    "restaurant_search_success": "restaurant_search_success",
}

CONTEXT_EXTENDER_KEYS: dict[str, str] = {
    "intent": "intent",
    "intent_confidence": "intent_confidence",
    "intent_slots": "intent_slots",
    "intent_need_clarify": "intent_need_clarify",
    "intent_clarify_question": "intent_clarify_question",
    "location_source": "location_source",
    "task_stage": "task_stage",
    "recovery_path": "recovery_path",
    "tool_plan": "tool_plan",
    "system_directive": "system_directive",
    "latest_route": "latest_route",
    "cached_location": "cached_location",
    "last_restaurants": "last_restaurants",
}

TASK_STAGE_VALUES: dict[str, str] = {
    "unknown": "unknown",
    "location_ready": "location_ready",
    "searched": "searched",
}

INTENT_CLARIFY_CONFIDENCE_THRESHOLD = 0.6

TOOL_NAMES: dict[str, str] = {
    "submit_final_answer": "submit_final_answer",
    "get_weather": "get_weather",
    "get_fridge_items": "get_fridge_items",
    "search_recipes": "search_recipes",
    "rag_search_recipes": "rag_search_recipes",
    "search_restaurants": "search_restaurants",
    "plan_route": "plan_route",
    "get_ip_location": "get_ip_location",
    "geocode_location": "geocode_location",
    "get_user_info": "get_user_info",
    "memory_search": "memory_search",
    "memory_write": "memory_write",
    "memory_update": "memory_update",
    "memory_forget": "memory_forget",
    "source_event_search": "source_event_search",
    "travel_search_poi": "travel_search_poi",
    "travel_create_personal_map": "travel_create_personal_map",
}

TOOL_ERROR_CODES: dict[str, str] = {
    "empty_result": "empty_result",
    "missing_location": "missing_location",
    "missing_ip": "missing_ip",
    "missing_origin": "missing_origin",
    "missing_destination": "missing_destination",
}


TOOL_RESULT_SYSTEM_DIRECTIVES: dict[str, str] = {
    "plan_route_ready": (
        "你已经拿到路线规划结果。请不要再调用其他工具，立即调用 submit_final_answer。"
        "请严格基于 context.latest_route 与最新的 plan_route 观察结果给出最终回复："
        "先给路线结论，再给关键步骤（例如距离、预计时长、分步指引）；"
        "若存在 steps/segments，优先提炼其中关键信息。"
    ),
}

NOTE_TEMPLATE_KEYS: dict[str, str] = {
    "fallback": "fallback",
    "intent_clarify": "intent_clarify",
    "route_missing_origin": "route_missing_origin",
    "route_missing_destination": "route_missing_destination",
    "route_generic_error": "route_generic_error",
    "best_effort_location_error": "best_effort_location_error",
    "best_effort_fridge_empty": "best_effort_fridge_empty",
}

NOTE_RESPONSE_TEMPLATES: dict[str, dict[str, Any]] = {
    NOTE_TEMPLATE_KEYS["fallback"]: {
        "title": "抱歉，我暂时没能完成这个请求。",
        "reason": "fallback",
        "followups": ["可以换个说法试试吗？", "你更想在家做还是出去吃？"],
    },
    NOTE_TEMPLATE_KEYS["intent_clarify"]: {
        "title": "你是想出去吃，还是在家做饭？",
        "reason": "我先确认下你的需求，再给你更准的建议。",
        "followups": [],
    },
    NOTE_TEMPLATE_KEYS["route_missing_origin"]: {
        "title": "还需要你的出发位置，才能规划路线。",
        "reason": "系统判定缺少起点信息。",
        "followups": ["你现在在哪个城市或位置？", "告诉我你的出发地/地标？"],
    },
    NOTE_TEMPLATE_KEYS["route_missing_destination"]: {
        "title": "还需要你的目的地，才能规划路线。",
        "reason": "终点信息缺失。",
        "followups": ["想去哪儿？给我目的地名称。"],
    },
    NOTE_TEMPLATE_KEYS["route_generic_error"]: {
        "title": "路线规划失败",
        "reason": "暂时无法获取路线信息。",
        "followups": ["换个出发地或目的地试试？"],
    },
    NOTE_TEMPLATE_KEYS["best_effort_location_error"]: {
        "title": "我还缺少精确位置，暂时没法推荐附近餐厅。",
        "reason": "位置信息不足",
        "followups": ["你可以发我当前城市或地标", "或者改为在家做饭也可以。"],
    },
    NOTE_TEMPLATE_KEYS["best_effort_fridge_empty"]: {
        "title": "冰箱空啦，我先给你几道简单快手菜思路。",
        "reason": "状态：冰箱为空",
        "followups": ["要不要我按 10 分钟内完成给你 3 道菜？", "或者你想改成附近餐厅推荐也可以。"],
    },
}


@dataclass
class SmartEatsState:
    session_id: str
    user_id: str | None = None
    message: str | None = None
    trace_id: str | None = None
    scene: str = "chat"
    context_overrides: dict[str, Any] | None = None
    snapshot: dict[str, Any] | None = None
    context: dict[str, Any] | None = None
    thought: str | None = None
    steps_left: int = 0
    turn_index: int = 0
    step_index: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    pending_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results_batch: list[dict[str, Any]] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
    final_json: dict[str, Any] | None = None
    planner_retry_count: int = 0
    action: Any | None = None
    provider: str | None = None
    resolved_model_config: dict[str, Any] | None = None
    agent_type: str | None = None
    client_ip: str | None = None
    intent: str | None = None
    intent_confidence: float = 0.0
    intent_slots: dict[str, Any] = field(default_factory=dict)
    intent_need_clarify: bool = False
    intent_clarify_question: str | None = None
    location_source: str | None = None
    task_stage: str | None = None
    tool_plan: list[dict[str, Any]] = field(default_factory=list)
    recovery_path: list[str] = field(default_factory=list)
    resume_from_checkpoint: bool = False
    checkpoint_ref: str | None = None
    replay_from_checkpoint: bool = False
    resume_payload: dict[str, Any] | None = None
    last_user_message: str | None = None
    user_message_logged: bool = False
    history: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    summary: str | None = None
    context_budget: dict[str, Any] = field(default_factory=dict)
    retrieved_memories: list[dict[str, Any]] = field(default_factory=list)
    source_refs: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class SmartEatsGraphConfig:
    name: str
    scene: str
    tool_names: list[str]
    max_steps: int = 4
    system_prompt_builder: Any = None
    writer_prompt_builder: Any = None
    action_normalizer: Any = normalize_action_from_raw
    tool_args_normalizer: Any = None
    serial_execution_decider: Any = None
    tool_result_previewer: Any = None
    final_action_hook: Any = None
    best_effort_fallback_handler: Any = None


@dataclass(frozen=True)
class SmartEatsRuntimeContext:
    redis_client: Any
    db: Any
    user_id: str | None
    context: dict[str, Any] | None
    session_id: str
    client_ip: str | None
    last_user_message: str | None
    servers_path: str | None

    def as_tool_payload(self) -> dict[str, Any]:
        return dict(self.__dict__)


class SmartEatsGraphState(TypedDict, total=False):
    messages: Annotated[list[Any], add_messages]
    session_id: str
    user_id: str | None
    message: str | None
    trace_id: str | None
    scene: str
    context_overrides: dict[str, Any] | None
    snapshot: dict[str, Any] | None
    context: dict[str, Any] | None
    thought: str | None
    steps_left: int
    turn_index: int
    step_index: int
    tool_calls: list[dict[str, Any]]
    pending_tool_calls: list[dict[str, Any]]
    tool_results_batch: list[dict[str, Any]]
    observations: list[dict[str, Any]]
    final_json: dict[str, Any] | None
    planner_retry_count: int
    action: Any | None
    provider: str | None
    resolved_model_config: dict[str, Any] | None
    agent_type: str | None
    client_ip: str | None
    intent: str | None
    intent_confidence: float
    intent_slots: dict[str, Any]
    intent_need_clarify: bool
    intent_clarify_question: str | None
    location_source: str | None
    task_stage: str | None
    tool_plan: list[dict[str, Any]]
    recovery_path: list[str]
    resume_from_checkpoint: bool
    checkpoint_ref: str | None
    replay_from_checkpoint: bool
    resume_payload: dict[str, Any] | None
    last_user_message: str | None
    user_message_logged: bool
    history: list[dict[str, Any]]
    events: list[dict[str, Any]]
    summary: str | None
    context_budget: dict[str, Any]
    retrieved_memories: list[dict[str, Any]]
    source_refs: list[dict[str, Any]]
    runtime_context: dict[str, Any]


_SMART_EATS_STATE_FIELDS = set(SmartEatsState.__dataclass_fields__.keys())


def _state_from_dict(payload: dict[str, Any]) -> SmartEatsState:
    filtered = {key: value for key, value in payload.items() if key in _SMART_EATS_STATE_FIELDS}
    filtered["events"] = []
    return SmartEatsState(**filtered)


def _state_to_dict(state: SmartEatsState) -> dict[str, Any]:
    return dict(state.__dict__)


def _state_update(state: SmartEatsState) -> dict[str, Any]:
    return _state_to_dict(state)


def _budget_model_name(state: SmartEatsState) -> str | None:
    config = state.resolved_model_config if isinstance(state.resolved_model_config, dict) else {}
    for key in ("model_planner", "model", "planner_model"):
        value = config.get(key)
        if isinstance(value, str) and value:
            return value
    provider = state.provider or settings.LLM_PROVIDER
    attr = f"{str(provider or '').upper()}_MODEL_PLANNER"
    value = getattr(settings, attr, None)
    return value if isinstance(value, str) and value else None


def _initialize_graph_state(payload: dict[str, Any]) -> dict[str, Any]:
    from langchain_core.messages import HumanMessage

    state = _state_from_dict(payload)
    output = _state_update(state)
    pending_messages: list[Any] = []
    current = (state.message or "").strip()
    existing_messages = payload.get("messages")
    latest_content = None
    if isinstance(existing_messages, list):
        for item in reversed(existing_messages):
            if isinstance(item, HumanMessage):
                latest_content = str(item.content or "").strip()
                break
    if current and latest_content != current:
        pending_messages.append(HumanMessage(content=current))
    output["messages"] = pending_messages
    return output


async def _prepare_langgraph_context(
    db: Any,
    redis_client: Any,
    state: SmartEatsState,
    agent_config: SmartEatsGraphConfig,
    *,
    store: Any = None,
    messages: list[Any] | None = None,
    emit_context_event: bool = True,
) -> None:
    from langchain_core.messages import HumanMessage

    context_overrides = state.context_overrides if isinstance(state.context_overrides, dict) else None
    cached_location, cached_restaurants = await asyncio.gather(
        load_cached_location(redis_client, state.session_id),
        load_cached_restaurants(redis_client, state.session_id),
    )
    state.history = []
    state.retrieved_memories = await load_user_memories(
        store,
        user_id=state.user_id,
        query=state.message or "",
        limit=5,
    )
    base_context = _build_base_prompt_context(
        state,
        memories=[str(item.get("content") or "") for item in state.retrieved_memories],
        cached_location=cached_location,
        cached_restaurants=cached_restaurants,
        summary=state.summary,
    )
    base_context = _merge_prompt_context_overrides(state, base_context)
    base_context, skill_prompt = _resolve_runtime_skills(state, base_context, agent_config)
    system_prompt = agent_config.system_prompt_builder(
        {
            "context": base_context,
            "skill_prompt": skill_prompt,
        }
    )
    if _should_persist_user_message(state) and db is not None and hasattr(db, "execute"):
        await history.save_user_message(
            db,
            redis_client,
            state.session_id,
            state.message,
        )
        state.user_message_logged = True
        state.last_user_message = state.message

    visible_messages = messages or []
    state.turn_index = sum(1 for msg in visible_messages if isinstance(msg, HumanMessage))
    state.intent = smart_intent_resolver(state) or "unknown"
    context_budget = dict(state.context_budget or {})
    context_budget.setdefault("status", "ok")
    context_budget["message_count"] = len(visible_messages)
    context_budget["retrieved_memory_count"] = len(state.retrieved_memories)
    model_context_window = resolve_model_context_window(
        provider=state.provider or settings.LLM_PROVIDER,
        model=_budget_model_name(state),
        fallback=settings.LLM_MODEL_CONTEXT_SIZE,
        overrides=parse_model_context_windows(settings.LLM_MODEL_CONTEXT_WINDOWS),
    )
    active_report = build_active_context_report(
        system_prompt=system_prompt,
        messages=visible_messages,
        summary=state.summary,
        memories=state.retrieved_memories,
        model_context_window=model_context_window,
        trigger_ratio=settings.CHAT_COMPACT_TRIGGER_RATIO,
        hard_ratio=settings.CHAT_COMPACT_HARD_RATIO,
        reserved_output_tokens=settings.CHAT_COMPACT_RESERVED_OUTPUT_TOKENS,
        reserved_tool_tokens=settings.CHAT_COMPACT_RESERVED_TOOL_TOKENS,
    )
    thrash = detect_compact_thrash(
        context_budget,
        active_report,
        max_attempts=settings.CHAT_COMPACT_MAX_ATTEMPTS,
        min_reduction_ratio=settings.CHAT_COMPACT_MIN_REDUCTION_RATIO,
    )
    if thrash["blocked"]:
        context_budget["status"] = "compact_blocked"
        context_budget["compact_blocked"] = True
        context_budget["compact_blocked_reason"] = thrash["reason"]
    context_budget["active_context"] = active_report
    context_budget["should_compact"] = should_summarize_context(
        active_report,
        min_messages=settings.CHAT_COMPACT_MIN_MESSAGES,
        previous_budget=context_budget,
    )
    state.context_budget = context_budget
    native_context = {
        "langgraph_native": True,
        "context_budget": context_budget,
        "retrieved_memory_count": len(state.retrieved_memories),
        "allowed_tools": list(base_context.get("allowed_tools") or agent_config.tool_names),
        "system_prompt": system_prompt,
    }
    state.context = _merge_context(base_context, native_context)
    if emit_context_event:
        state.events.append(
            {
                "event": "context",
                "data": {
                    "langgraph_native": True,
                    "context_budget": state.context.get("context_budget"),
                    "allowed_tools": state.context.get("allowed_tools"),
                    "retrieved_memory_count": len(state.retrieved_memories),
                },
            }
        )


def _latest_ai_messages(messages: Any) -> list[Any]:
    from langchain_core.messages import AIMessage

    if not isinstance(messages, list):
        return []
    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], AIMessage):
            return [messages[index]]
    return []


def _latest_tool_messages(messages: Any) -> list[Any]:
    from langchain_core.messages import AIMessage, ToolMessage

    if not isinstance(messages, list):
        return []
    latest_ai_index: int | None = None
    latest_tool_call_ids: set[str] = set()
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if isinstance(message, AIMessage) and message.tool_calls:
            latest_ai_index = index
            latest_tool_call_ids = {
                call.get("id")
                for call in message.tool_calls
                if isinstance(call, dict) and isinstance(call.get("id"), str)
            }
            break
    if latest_ai_index is None or not latest_tool_call_ids:
        return []
    return [
        message
        for message in messages[latest_ai_index + 1:]
        if isinstance(message, ToolMessage) and message.tool_call_id in latest_tool_call_ids
    ]


def _build_submit_final_answer_tool() -> Any:
    from langchain_core.tools import StructuredTool

    async def _submit_final_answer(**kwargs: Any) -> dict[str, Any]:
        args = FinalAnswerArgs.model_validate(kwargs)
        return {"_final_answer": args.model_dump()}

    return StructuredTool.from_function(
        coroutine=_submit_final_answer,
        name=TOOL_NAMES["submit_final_answer"],
        description="当你已收集足够信息并准备给用户最终回复时调用。",
        args_schema=FinalAnswerArgs,
        infer_schema=False,
    )


def _build_official_runtime_context(
    state: SmartEatsState,
    *,
    db: Any,
    redis_client: Any,
    servers_path: str | None,
) -> dict[str, Any]:
    return SmartEatsRuntimeContext(
        redis_client=redis_client,
        db=db,
        user_id=state.user_id,
        context=state.context,
        session_id=state.session_id,
        client_ip=state.client_ip,
        last_user_message=state.last_user_message or state.message,
        servers_path=servers_path,
    ).as_tool_payload()


def _decode_tool_content(raw_content: Any) -> Any:
    if not isinstance(raw_content, str):
        return raw_content
    try:
        return json.loads(raw_content)
    except json.JSONDecodeError:
        return raw_content


def _extract_tool_name_from_message(message: Any) -> str | None:
    name = getattr(message, "name", None)
    if isinstance(name, str) and name:
        return name
    return None


def _normalize_official_tool_messages(tool_output: Any) -> list[Any]:
    tool_messages = tool_output.get("messages") if isinstance(tool_output, dict) else []
    if not isinstance(tool_messages, list):
        return []
    return tool_messages


def _collect_tool_call_args(ai_messages: list[Any]) -> dict[str, dict[str, Any]]:
    from langchain_core.messages import AIMessage

    latest_ai_message = ai_messages[-1] if ai_messages else None
    call_args_map: dict[str, dict[str, Any]] = {}
    if isinstance(latest_ai_message, AIMessage):
        for call in latest_ai_message.tool_calls or []:
            call_id = call.get("id")
            args = call.get("args")
            if isinstance(call_id, str) and isinstance(args, dict):
                call_args_map[call_id] = args
    return call_args_map


def _skill_max_tool_calls_per_turn(context: dict[str, Any] | None) -> int | None:
    if not isinstance(context, dict):
        return None
    diagnostics = context.get("skill_diagnostics")
    if not isinstance(diagnostics, dict):
        return None
    value = diagnostics.get("max_tool_calls_per_turn")
    return value if isinstance(value, int) and value >= 0 else None


def _limit_skill_tool_calls(
    tool_calls: list[dict[str, Any]],
    *,
    max_tool_calls: int | None,
) -> list[dict[str, Any]]:
    if max_tool_calls is None:
        return tool_calls
    limited: list[dict[str, Any]] = []
    external_count = 0
    for call in tool_calls:
        if call.get("name") == TOOL_NAMES["submit_final_answer"]:
            limited.append(call)
            continue
        if external_count >= max_tool_calls:
            continue
        limited.append(call)
        external_count += 1
    return limited


def _clear_system_directive_override(state: SmartEatsState) -> None:
    if isinstance(state.context_overrides, dict):
        state.context_overrides.pop(STATE_CONTEXT_OVERRIDE_KEYS["system_directive"], None)
        _prune_empty_context_overrides(state)


def _build_tool_runtime_payload(
    chat_state: SmartEatsState,
    *,
    db: Any,
    redis_client: Any,
    store: Any = None,
) -> dict[str, Any]:
    payload = _build_official_runtime_context(
        chat_state,
        db=db,
        redis_client=redis_client,
        servers_path=settings.MCP_SERVERS_CONFIG_PATH,
    )
    if store is not None:
        payload["langgraph_store"] = store
    return payload


async def _invoke_tool_node_with_runtime(
    tool_node: Any,
    ai_messages: list[Any],
    *,
    chat_state: SmartEatsState,
    db: Any,
    redis_client: Any,
    store: Any = None,
) -> Any:
    runtime_payload = _build_tool_runtime_payload(
        chat_state,
        db=db,
        redis_client=redis_client,
        store=store,
    )
    return await tool_node.ainvoke({"messages": ai_messages, "runtime_context": runtime_payload})


def _build_tools_node_output(chat_state: SmartEatsState, tool_output: Any) -> dict[str, Any]:
    output = _state_update(chat_state)
    output["messages"] = _normalize_official_tool_messages(tool_output)
    return output


def _observe_recovery(state: SmartEatsState, tool_name: str | None, result: Any) -> None:
    if not isinstance(result, dict):
        return
    error = result.get("error")
    if not error:
        return
    step = f"{tool_name}:{error}" if tool_name else str(error)
    if step not in state.recovery_path:
        state.recovery_path.append(step)


def _build_result_preview(
    agent_config: SmartEatsGraphConfig,
    tool_name: str | None,
    result: Any,
) -> Any:
    from app.agent.tools_registry import preview_result

    if tool_name and agent_config.tool_result_previewer:
        customized = agent_config.tool_result_previewer(tool_name, result)
        if customized is not None:
            return customized
    return preview_result(result)


def _fallback_final() -> dict[str, Any]:
    return _build_note_template_final_answer(NOTE_TEMPLATE_KEYS["fallback"])


def _best_effort_final_from_observations(state: SmartEatsState, agent_config: SmartEatsGraphConfig) -> dict[str, Any]:
    if agent_config.best_effort_fallback_handler:
        try:
            business_fallback = agent_config.best_effort_fallback_handler(state)
        except Exception:
            business_fallback = None
        if isinstance(business_fallback, dict):
            return business_fallback
    return _fallback_final()


async def _apply_official_tool_postprocess(
    chat_state: SmartEatsState,
    *,
    tool_messages: list[Any],
    call_args_map: dict[str, dict[str, Any]],
    db: Any,
    redis_client: Any,
    agent_config: SmartEatsGraphConfig,
    store: Any = None,
) -> None:
    for message in tool_messages:
        tool_name = _extract_tool_name_from_message(message)
        if not tool_name:
            continue
        tool_call_id = getattr(message, "tool_call_id", None)
        args = call_args_map.get(tool_call_id, {}) if isinstance(tool_call_id, str) else {}

        artifact = getattr(message, "artifact", None)
        raw_payload = artifact if artifact is not None else getattr(message, "content", None)
        result = _decode_tool_content(raw_payload)

        if tool_name == TOOL_NAMES["submit_final_answer"] and isinstance(result, dict):
            final_payload = result.get("_final_answer")
            if isinstance(final_payload, dict):
                chat_state.final_json = final_payload
            continue

        result_preview = _build_result_preview(agent_config, tool_name, result)
        chat_state.tool_calls.append({"name": tool_name, "args": args, "latency_ms": 0})
        chat_state.observations.append({"tool": tool_name, "result": result})
        _observe_recovery(chat_state, tool_name, result)

        handled = _tool_result_handler(chat_state, tool_name, result)
        if handled:
            chat_state.final_json = handled

        if db is not None and hasattr(db, "execute"):
            await history.save_tool_message(
                db,
                redis_client,
                chat_state.session_id,
                tool_name,
                {
                    "args": args,
                    "latency_ms": 0,
                    "result": result,
                    "result_preview": result_preview,
                },
            )
        source_ref = await save_source_event(
            store,
            thread_id=chat_state.session_id,
            tool_name=tool_name,
            tool_call_id=tool_call_id if isinstance(tool_call_id, str) else None,
            args=args,
            result=result,
            preview=result_preview,
        )
        if source_ref:
            chat_state.source_refs.append(
                {
                    "event_id": source_ref.get("event_id"),
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                }
            )
        chat_state.events.append(
            {
                "event": "tool_call",
                "data": {
                    "name": tool_name,
                    "args": args,
                    "latency_ms": 0,
                    "result_preview": result_preview,
                },
            }
        )
    extra = smart_context_extender(chat_state)
    if extra:
        refreshed_context = _merge_context(chat_state.context or {}, extra)
        prompt_context = dict(refreshed_context)
        prompt_context.pop("system_prompt", None)
        refreshed_context["system_prompt"] = agent_config.system_prompt_builder(
            {
                "context": prompt_context,
                "skill_prompt": "",
            }
        )
        chat_state.context = refreshed_context


def _preview_tool_messages(
    tool_messages: list[Any],
    agent_config: SmartEatsGraphConfig,
) -> list[Any]:
    from langchain_core.messages import ToolMessage

    preview_messages: list[Any] = []
    for message in tool_messages:
        tool_name = _extract_tool_name_from_message(message)
        if not tool_name or tool_name == TOOL_NAMES["submit_final_answer"]:
            preview_messages.append(message)
            continue
        artifact = getattr(message, "artifact", None)
        raw_payload = artifact if artifact is not None else getattr(message, "content", None)
        result = _decode_tool_content(raw_payload)
        preview = _build_result_preview(agent_config, tool_name, result)
        preview_messages.append(
            ToolMessage(
                content=json.dumps(preview, ensure_ascii=False),
                name=tool_name,
                tool_call_id=str(getattr(message, "tool_call_id", None) or ""),
                id=getattr(message, "id", None),
            )
        )
    return preview_messages


def _finalize_official_after_tools(chat_state: SmartEatsState, agent_config: SmartEatsGraphConfig) -> None:
    chat_state.steps_left -= 1
    if chat_state.steps_left <= 0 and not chat_state.final_json:
        chat_state.final_json = _best_effort_final_from_observations(chat_state, agent_config)
    chat_state.pending_tool_calls = []


def _official_is_final(state: dict[str, Any]) -> bool:
    return bool(state.get("final_json"))


def _route_after_prepare(state: dict[str, Any]) -> str:
    context_budget = state.get("context_budget")
    if _official_is_final(state):
        route = "__end__"
    elif isinstance(context_budget, dict) and context_budget.get("should_compact"):
        route = "summarize"
    else:
        route = "agent"
    logger.info("graph_route node=prepare route=%s session_id=%s", route, state.get("session_id"))
    return route


def _route_after_agent(state: dict[str, Any]) -> str:
    if _official_is_final(state):
        route = "__end__"
    else:
        from langgraph.prebuilt import tools_condition

        route = tools_condition(state, messages_key="messages")
    logger.info("graph_route node=agent route=%s session_id=%s", route, state.get("session_id"))
    return route


def _route_after_tools(state: dict[str, Any]) -> str:
    route = "__end__" if _official_is_final(state) else "agent"
    logger.info("graph_route node=tools route=%s session_id=%s", route, state.get("session_id"))
    return route


def _merge_context(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_context(merged[key], value)
        else:
            merged[key] = value
    return merged


async def _ensure_chat_session(db: Any, state: SmartEatsState) -> None:
    from sqlalchemy import select

    from app.infra.models.chat import ChatSession

    result = await db.execute(select(ChatSession).where(ChatSession.id == state.session_id))
    session = result.scalar_one_or_none()
    if session is None:
        session = ChatSession(
            id=state.session_id,
            user_id=state.user_id,
            scene=state.scene,
        )
        db.add(session)
        await db.commit()


def _should_persist_user_message(state: SmartEatsState) -> bool:
    return bool(state.message and (not state.user_message_logged or state.last_user_message != state.message))


def _build_base_prompt_context(
    state: SmartEatsState,
    *,
    memories: list[str],
    cached_location: Any,
    cached_restaurants: Any,
    summary: str | None,
) -> dict[str, Any]:
    context: dict[str, Any] = {"ui_scene": state.scene or "chat"}
    context["user_message"] = state.message or ""
    context["history"] = state.history
    if summary:
        context["history_summary"] = summary
    if memories:
        context["memories"] = memories
    context["observations"] = list(state.observations)
    if isinstance(cached_location, dict) and cached_location.get("lat") is not None and cached_location.get("lng") is not None:
        context[CONTEXT_EXTENDER_KEYS["cached_location"]] = {
            "lat": cached_location.get("lat"),
            "lng": cached_location.get("lng"),
            "city": cached_location.get("city"),
        }
        if not isinstance(context.get(STATE_CONTEXT_KEYS["location"]), dict):
            context[STATE_CONTEXT_KEYS["location"]] = {
                "lat": cached_location.get("lat"),
                "lng": cached_location.get("lng"),
            }
        if isinstance(cached_location.get("city"), str) and cached_location.get("city").strip() and not context.get(STATE_CONTEXT_KEYS["city"]):
            context[STATE_CONTEXT_KEYS["city"]] = cached_location.get("city")

    if isinstance(cached_restaurants, list) and cached_restaurants:
        cleaned_restaurants = [row for row in cached_restaurants if isinstance(row, dict)]
        if cleaned_restaurants:
            context[CONTEXT_EXTENDER_KEYS["last_restaurants"]] = cleaned_restaurants
            context[STATE_CONTEXT_KEYS["last_restaurants"]] = cleaned_restaurants

            route_target_candidate = _extract_route_target_from_cached_restaurants(state.message, cleaned_restaurants)
            if isinstance(route_target_candidate, dict):
                context["route_target_candidate"] = route_target_candidate
    context["checkpoint"] = {
        "resume_from_checkpoint": state.resume_from_checkpoint,
        "checkpoint_ref": state.checkpoint_ref,
        "replay_from_checkpoint": state.replay_from_checkpoint,
    }
    return context


def _merge_prompt_context_overrides(state: SmartEatsState, context: dict[str, Any]) -> dict[str, Any]:
    extra = smart_context_extender(state)
    if extra:
        context = _merge_context(context, extra)
    if isinstance(state.context_overrides, dict) and state.context_overrides:
        context = _merge_context(context, state.context_overrides)
    return context


def _resolve_runtime_skills(
    state: SmartEatsState,
    context: dict[str, Any],
    agent_config: SmartEatsGraphConfig,
) -> tuple[dict[str, Any], str]:
    from app.agent.skills.runtime import SkillRuntime

    requested_tools = context.get("allowed_tools")
    base_allowlist = [
        item for item in requested_tools
        if isinstance(item, str) and item in agent_config.tool_names
    ] if isinstance(requested_tools, list) else list(agent_config.tool_names)
    if not base_allowlist:
        base_allowlist = list(agent_config.tool_names)

    skill_runtime = SkillRuntime(
        skills_path=settings.AGENT_SKILLS_PATH,
        enabled=settings.AGENT_SKILLS_ENABLED,
        max_active=settings.AGENT_SKILLS_MAX_ACTIVE,
        max_prompt_chars=settings.AGENT_SKILLS_MAX_PROMPT_CHARS,
        global_allowlist=base_allowlist,
        log_diagnostics=settings.AGENT_SKILLS_LOG_DIAGNOSTICS,
    ).resolve(
        state,
        context,
        base_tools=[],
    )
    if skill_runtime.context:
        context = _merge_context(context, skill_runtime.context)
    context["allowed_tools"] = (
        skill_runtime.allowed_tools
        if skill_runtime.active_skills
        else base_allowlist
    )
    return context, skill_runtime.system_prompt_addendum


def _record_agent_metric(state: SmartEatsState, name: str, **tags: Any) -> None:
    logger.info(
        "metric session_id=%s name=%s tags=%s",
        state.session_id,
        name,
        tags,
    )


def _set_task_stage(state: SmartEatsState, next_stage: str, *, cause: str) -> None:
    prev = state.task_stage or TASK_STAGE_VALUES["unknown"]
    if prev != next_stage:
        logger.info(
            "stage_transition session_id=%s from=%s to=%s cause=%s",
            state.session_id,
            prev,
            next_stage,
            cause,
        )
    state.task_stage = next_stage


RESTAURANT_CONFIRM_CUES: tuple[str, ...] = (
    "就去",
    "去",
    "选",
    "就这家",
    "这家",
    "那家",
    "安排",
    "走起",
    "前往",
    "带我去",
    "导航",
    "路线",
    "怎么走",
)

RESTAURANT_INFO_QUERY_CUES: tuple[str, ...] = (
    "怎么样",
    "好吃吗",
    "评价",
    "电话",
    "营业",
    "地址",
    "菜单",
    "人均",
)

RESTAURANT_NAME_SUFFIXES: tuple[str, ...] = (
    "火锅店",
    "烧烤店",
    "餐厅",
    "饭店",
    "酒店",
    "酒家",
    "小馆",
    "馆",
    "店",
)


def _normalize_match_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip().lower()
    if not text:
        return ""
    return re.sub(r"[\s，。！？、,.!?:：；;（）()\[\]{}<>\-_'\"“”‘’]", "", text)


def _coerce_geo_candidate(payload: Any) -> dict[str, float] | None:
    if not isinstance(payload, dict):
        return None
    lat_raw = payload.get("lat")
    lng_raw = payload.get("lng")
    try:
        lat = float(lat_raw)
        lng = float(lng_raw)
    except (TypeError, ValueError):
        return None
    return {"lat": lat, "lng": lng}


def _extract_origin_from_context(context: Any) -> dict[str, float] | None:
    if not isinstance(context, dict):
        return None

    location = _coerce_geo_candidate(context.get(STATE_CONTEXT_KEYS["location"]))
    if location:
        return location

    cached_location = _coerce_geo_candidate(context.get(CONTEXT_EXTENDER_KEYS["cached_location"]))
    if cached_location:
        return cached_location

    environment = context.get("environment") if isinstance(context.get("environment"), dict) else None
    if isinstance(environment, dict):
        env_location = _coerce_geo_candidate(environment.get("location"))
        if env_location:
            return env_location

    return None


def _extract_route_target_from_cached_restaurants(
    user_message: str | None,
    cached_restaurants: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    normalized_message = _normalize_match_text(user_message)
    if not normalized_message or not (isinstance(cached_restaurants, list) and cached_restaurants):
        return None

    raw_message = user_message or ""
    if any(token in raw_message for token in RESTAURANT_INFO_QUERY_CUES):
        return None

    has_confirm_cue = any(token in raw_message for token in RESTAURANT_CONFIRM_CUES)

    best_match: dict[str, Any] | None = None
    best_score = -1

    for row in cached_restaurants:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or row.get("title") or "").strip()
        if not name:
            continue

        geo = _coerce_geo_candidate(row.get("geo"))
        if not geo:
            continue

        normalized_name = _normalize_match_text(name)
        if not normalized_name:
            continue

        aliases = {normalized_name}
        for suffix in RESTAURANT_NAME_SUFFIXES:
            normalized_suffix = _normalize_match_text(suffix)
            if (
                normalized_suffix
                and normalized_name.endswith(normalized_suffix)
                and len(normalized_name) > len(normalized_suffix) + 1
            ):
                aliases.add(normalized_name[: -len(normalized_suffix)])

        matched_alias = None
        for alias in aliases:
            if alias and (alias in normalized_message or normalized_message in alias):
                matched_alias = alias
                break
        if not matched_alias:
            continue

        if not has_confirm_cue and not any(token in raw_message for token in ("导航", "路线", "怎么走", "过去", "到")):
            continue

        score = len(matched_alias)
        if score > best_score:
            best_score = score
            best_match = {
                "name": name,
                "geo": geo,
            }

    return best_match



def smart_intent_resolver(state: SmartEatsState) -> str | None:
    """意图识别下放给 Planner（LLM）。

    这里不再做关键词硬编码判意图，避免规则僵化。
    代码层只保留可观测标签，真正的意图与路由由 planner 在 think 阶段决定。
    """
    intent = "unknown"
    logger.info(
        "intent_reason session_id=%s intent=%s reason=llm_owned_intent",
        state.session_id,
        intent,
    )
    return intent


def smart_context_extender(state: SmartEatsState) -> dict:
    """扩展 LLM 上下文，注入业务相关字段。"""
    extra = {}
    if state.intent:
        extra[CONTEXT_EXTENDER_KEYS["intent"]] = state.intent
        extra[CONTEXT_EXTENDER_KEYS["intent_confidence"]] = state.intent_confidence
        extra[CONTEXT_EXTENDER_KEYS["intent_slots"]] = dict(state.intent_slots)
        extra[CONTEXT_EXTENDER_KEYS["intent_need_clarify"]] = state.intent_need_clarify
        if state.intent_clarify_question:
            extra[CONTEXT_EXTENDER_KEYS["intent_clarify_question"]] = state.intent_clarify_question
    if state.location_source:
        extra[CONTEXT_EXTENDER_KEYS["location_source"]] = state.location_source
    if state.task_stage:
        extra[CONTEXT_EXTENDER_KEYS["task_stage"]] = state.task_stage
    if state.recovery_path:
        extra[CONTEXT_EXTENDER_KEYS["recovery_path"]] = list(state.recovery_path)
    if state.tool_plan:
        extra[CONTEXT_EXTENDER_KEYS["tool_plan"]] = list(state.tool_plan)

    if isinstance(state.context_overrides, dict):
        latest_route = state.context_overrides.pop(STATE_CONTEXT_OVERRIDE_KEYS["latest_route"], None)
        if isinstance(latest_route, dict) and latest_route:
            extra[CONTEXT_EXTENDER_KEYS["latest_route"]] = latest_route

        directive = state.context_overrides.pop(STATE_CONTEXT_OVERRIDE_KEYS["system_directive"], None)
        if isinstance(directive, str) and directive.strip():
            extra[CONTEXT_EXTENDER_KEYS["system_directive"]] = directive.strip()
        if not state.context_overrides:
            state.context_overrides = None

    return extra


def _normalize_geocode_location_args(args: dict[str, Any]) -> dict[str, Any]:
    if "query" not in args and "location" in args:
        updated = dict(args)
        updated["query"] = updated.pop("location")
        return updated
    return args


def _normalize_search_restaurants_args(args: dict[str, Any]) -> dict[str, Any]:
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


_TOOL_ARGS_NORMALIZERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    TOOL_NAMES["geocode_location"]: _normalize_geocode_location_args,
    TOOL_NAMES["search_restaurants"]: _normalize_search_restaurants_args,
}

PLAN_ROUTE_PREVIEW_FIELDS: tuple[str, ...] = (
    "distance_m",
    "duration_s",
    "steps",
    "segments",
    "origin",
    "destination",
    "mode",
    "fallback_from",
    "error",
)

BEST_EFFORT_TEMPLATES: dict[str, Any] = {
    "recipe_reason_fallback": "基于知识库检索",
    "recipe_followups": [
        "你想学哪一道？告诉我菜名，我直接给你详细步骤。",
        "如果你有忌口或时间限制，我可以继续帮你筛。",
    ],
    "restaurant_note_title": "我先给你整理了附近可选店",
    "restaurant_note_reason": "基于已拿到的检索结果",
    "restaurant_followup_prefix": "你可以先看这几家：",
    "restaurant_followup_suffix": "要不要我再按口味帮你筛一轮？",
}

BEST_EFFORT_SCAN_KEYS: dict[str, str] = {
    "last_recipe_list": "last_recipe_list",
    "last_search_list": "last_search_list",
    "last_error": "last_error",
}


def smart_tool_args_normalizer(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    normalizer = _TOOL_ARGS_NORMALIZERS.get(tool_name)
    if not normalizer:
        return args
    return normalizer(args)


def smart_tool_result_previewer(tool_name: str, result: object) -> dict[str, Any] | None:
    if tool_name == TOOL_NAMES["plan_route"] and isinstance(result, dict):
        return {field: result.get(field) for field in PLAN_ROUTE_PREVIEW_FIELDS}
    return None


def smart_system_prompt(payload: dict) -> str:
    """从 system.md 加载 Prompt 规则，并注入运行时上下文。"""
    try:
        with open(SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as f:
            base_prompt = f.read()
    except FileNotFoundError:
        base_prompt = "你是 SmartEats 智能助手，帮助用户解决「吃什么」的问题。"

    skill_prompt = payload.get("skill_prompt")
    skill_block = f"\n\n{skill_prompt.strip()}" if isinstance(skill_prompt, str) and skill_prompt.strip() else ""
    context_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return (
        f"{base_prompt}{skill_block}\n\n"
        "## Runtime Context（系统注入，非用户输入）\n"
        f"- output_language: {settings.DEFAULT_LANGUAGE}\n"
        f"- context: {context_json}"
    )


def _scan_rag_recipe_observation(result: Any, scan_state: dict[str, Any]) -> None:
    if not isinstance(result, dict):
        return
    items = result.get("items")
    if scan_state[BEST_EFFORT_SCAN_KEYS["last_recipe_list"]] is None and isinstance(items, list) and items:
        scan_state[BEST_EFFORT_SCAN_KEYS["last_recipe_list"]] = items
    if isinstance(result.get("error"), str):
        scan_state[BEST_EFFORT_SCAN_KEYS["last_error"]] = result.get("error")


def _scan_restaurant_observation(result: Any, scan_state: dict[str, Any]) -> None:
    if scan_state[BEST_EFFORT_SCAN_KEYS["last_search_list"]] is None and isinstance(result, list) and result:
        scan_state[BEST_EFFORT_SCAN_KEYS["last_search_list"]] = result
    if isinstance(result, dict) and isinstance(result.get("error"), str):
        scan_state[BEST_EFFORT_SCAN_KEYS["last_error"]] = result.get("error")


def _scan_location_observation(result: Any, scan_state: dict[str, Any]) -> None:
    if isinstance(result, dict) and isinstance(result.get("error"), str):
        scan_state[BEST_EFFORT_SCAN_KEYS["last_error"]] = result.get("error")


_BEST_EFFORT_OBSERVATION_SCANNERS: dict[str, Callable[[Any, dict[str, Any]], None]] = {
    TOOL_NAMES["rag_search_recipes"]: _scan_rag_recipe_observation,
    TOOL_NAMES["search_restaurants"]: _scan_restaurant_observation,
    TOOL_NAMES["get_ip_location"]: _scan_location_observation,
    TOOL_NAMES["geocode_location"]: _scan_location_observation,
}


def _build_final_answer(
    *,
    recommendations: list[dict[str, Any]],
    followups: list[str],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return FinalAnswer(
        recommendations=list(recommendations),
        followups=list(followups),
        warnings=list(warnings) if warnings is not None else [],
    ).model_dump()


def _build_single_note_final_answer(
    *,
    title: str,
    reason: str,
    followups: list[str],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return _build_final_answer(
        recommendations=[{"type": "note", "title": title, "reason": reason}],
        followups=followups,
        warnings=warnings,
    )


def _build_note_template_final_answer(
    template_key: str,
    *,
    title_override: str | None = None,
    followups_override: list[str] | None = None,
) -> dict[str, Any]:
    template = NOTE_RESPONSE_TEMPLATES[template_key]
    title = title_override if isinstance(title_override, str) and title_override.strip() else template["title"]
    followups = list(followups_override) if followups_override is not None else list(template.get("followups", []))
    return _build_single_note_final_answer(
        title=title,
        reason=template["reason"],
        followups=followups,
    )


def _best_effort_from_recipe_hits(scan_state: dict[str, Any]) -> dict[str, Any] | None:
    last_recipe_list = scan_state.get(BEST_EFFORT_SCAN_KEYS["last_recipe_list"])
    if not (isinstance(last_recipe_list, list) and last_recipe_list):
        return None

    recipe_recommendations: list[dict[str, Any]] = []
    for recipe in last_recipe_list[:3]:
        if not isinstance(recipe, dict):
            continue
        title = str(recipe.get("title") or "").strip()
        snippet = str(recipe.get("snippet") or "").strip()
        if not title:
            continue
        recipe_recommendations.append(
            {
                "type": "recipe",
                "title": title,
                "reason": snippet[:80] if snippet else BEST_EFFORT_TEMPLATES["recipe_reason_fallback"],
            }
        )

    if not recipe_recommendations:
        return None

    return _build_final_answer(
        recommendations=recipe_recommendations,
        followups=list(BEST_EFFORT_TEMPLATES["recipe_followups"]),
    )


def _best_effort_from_restaurant_hits(scan_state: dict[str, Any]) -> dict[str, Any] | None:
    last_search_list = scan_state.get(BEST_EFFORT_SCAN_KEYS["last_search_list"])
    if not (isinstance(last_search_list, list) and last_search_list):
        return None

    top: list[str] = []
    for row in last_search_list[:3]:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if name:
            top.append(name)
    if not top:
        return None

    return _build_single_note_final_answer(
        title=BEST_EFFORT_TEMPLATES["restaurant_note_title"],
        reason=BEST_EFFORT_TEMPLATES["restaurant_note_reason"],
        followups=[
            f"{BEST_EFFORT_TEMPLATES['restaurant_followup_prefix']}{'；'.join(top)}",
            BEST_EFFORT_TEMPLATES["restaurant_followup_suffix"],
        ],
    )


def _best_effort_from_location_error(scan_state: dict[str, Any]) -> dict[str, Any] | None:
    last_error = scan_state.get(BEST_EFFORT_SCAN_KEYS["last_error"])
    if last_error not in {TOOL_ERROR_CODES["missing_location"], TOOL_ERROR_CODES["missing_ip"]}:
        return None

    return _build_note_template_final_answer(NOTE_TEMPLATE_KEYS["best_effort_location_error"])


_BEST_EFFORT_FINAL_STRATEGIES: list[Callable[[dict[str, Any]], dict[str, Any] | None]] = [
    _best_effort_from_recipe_hits,
    _best_effort_from_restaurant_hits,
    _best_effort_from_location_error,
]


def smart_best_effort_fallback(state: SmartEatsState) -> dict[str, Any] | None:
    """SmartEats 业务层兜底：当步骤耗尽时尽量返回可用结果。"""
    if isinstance(state.context, dict) and state.context.get(STATE_CONTEXT_KEYS["fridge_items"]) == []:
        return _build_note_template_final_answer(NOTE_TEMPLATE_KEYS["best_effort_fridge_empty"])

    scan_state: dict[str, Any] = {
        BEST_EFFORT_SCAN_KEYS["last_recipe_list"]: None,
        BEST_EFFORT_SCAN_KEYS["last_search_list"]: None,
        BEST_EFFORT_SCAN_KEYS["last_error"]: None,
    }

    for item in reversed(state.observations):
        if not isinstance(item, dict):
            continue
        tool_name = item.get("tool")
        result = item.get("result")

        scanner = _BEST_EFFORT_OBSERVATION_SCANNERS.get(tool_name) if isinstance(tool_name, str) else None
        if scanner:
            scanner(result, scan_state)

        if (
            scan_state[BEST_EFFORT_SCAN_KEYS["last_recipe_list"]] is not None
            and scan_state[BEST_EFFORT_SCAN_KEYS["last_search_list"]] is not None
        ):
            break

    for strategy in _BEST_EFFORT_FINAL_STRATEGIES:
        final_json = strategy(scan_state)
        if isinstance(final_json, dict):
            return final_json

    return None


def _sync_location_source(state: SmartEatsState, result: object) -> None:
    if isinstance(result, dict):
        loc_source = result.get(STATE_CONTEXT_KEYS["location_source"])
        if isinstance(loc_source, str) and loc_source:
            state.location_source = loc_source
    if isinstance(state.context, dict):
        ctx_loc_source = state.context.get(STATE_CONTEXT_KEYS["location_source"])
        if isinstance(ctx_loc_source, str) and ctx_loc_source:
            state.location_source = ctx_loc_source


def _ensure_context(state: SmartEatsState) -> dict[str, Any]:
    if state.context is None:
        state.context = {}
    return state.context


def _ensure_context_overrides(state: SmartEatsState) -> dict[str, Any]:
    if state.context_overrides is None:
        state.context_overrides = {}
    return state.context_overrides


def _prune_empty_context_overrides(state: SmartEatsState) -> None:
    if isinstance(state.context_overrides, dict) and not state.context_overrides:
        state.context_overrides = None


# ============================================================
# Tool result handlers navigation (read-only index)
# ------------------------------------------------------------
# 1) location
#    - _handle_location_result
# 2) restaurant
#    - _handle_search_restaurants_result
# 3) recipe
#    - _handle_get_fridge_items_result
#    - _handle_search_recipes_result
#    - _handle_rag_search_recipes_result
# 4) route
#    - _handle_plan_route_result
# 5) dispatch entry
#    - _TOOL_RESULT_DISPATCH
#    - _tool_result_handler
# ============================================================

# location handlers

def _handle_location_result(
    state: SmartEatsState,
    tool_name: str,
    result: object,
) -> dict | None:
    if not isinstance(result, dict):
        return None

    if result.get("error"):
        context = _ensure_context(state)
        context[STATE_CONTEXT_KEYS["last_location_error"]] = result.get("error")
        _record_agent_metric(
            state,
            AGENT_METRIC_NAMES["location_resolution_failed"],
            tool=tool_name,
            code=result.get("error"),
        )
        return None

    _set_task_stage(state, TASK_STAGE_VALUES["location_ready"], cause="tool_result_location")
    context = _ensure_context(state)
    location = {"lat": result.get("lat"), "lng": result.get("lng")}
    context[STATE_CONTEXT_KEYS["location"]] = location
    if result.get("city"):
        context[STATE_CONTEXT_KEYS["city"]] = result.get("city")
    _record_agent_metric(state, AGENT_METRIC_NAMES["location_resolution_success"], tool=tool_name)
    return None


# restaurant handlers

def _handle_search_restaurants_result(
    state: SmartEatsState,
    _tool_name: str,
    result: object,
) -> dict | None:
    _set_task_stage(state, TASK_STAGE_VALUES["searched"], cause="tool_result_search_restaurants")
    context = _ensure_context(state)

    if isinstance(result, dict) and result.get("error"):
        context[STATE_CONTEXT_KEYS["last_search_error"]] = result.get("error")
        context.pop(STATE_CONTEXT_KEYS["suggested_radius_km"], None)
        _clear_system_directive_override(state)
        _record_agent_metric(state, AGENT_METRIC_NAMES["restaurant_search_error"], code=result.get("error"))
        return None

    if isinstance(result, list) and not result:
        retries = int(context.get(STATE_CONTEXT_KEYS["restaurant_retries"]) or 0) + 1
        context[STATE_CONTEXT_KEYS["restaurant_retries"]] = retries
        context[STATE_CONTEXT_KEYS["last_search_error"]] = TOOL_ERROR_CODES["empty_result"]
        context.pop(STATE_CONTEXT_KEYS["suggested_radius_km"], None)
        _record_agent_metric(state, AGENT_METRIC_NAMES["restaurant_search_empty"], retries=retries)

        context_overrides = _ensure_context_overrides(state)
        context_overrides[STATE_CONTEXT_OVERRIDE_KEYS["restaurant_search_retries"]] = retries
        context_overrides.pop(STATE_CONTEXT_OVERRIDE_KEYS["system_directive"], None)
        _prune_empty_context_overrides(state)
        return None

    if isinstance(result, list) and result:
        context.pop(STATE_CONTEXT_KEYS["last_search_error"], None)
        context.pop(STATE_CONTEXT_KEYS["restaurant_retries"], None)
        context.pop(STATE_CONTEXT_KEYS["suggested_radius_km"], None)
        if isinstance(state.context_overrides, dict):
            state.context_overrides.pop(STATE_CONTEXT_OVERRIDE_KEYS["restaurant_search_retries"], None)
            state.context_overrides.pop(STATE_CONTEXT_OVERRIDE_KEYS["system_directive"], None)
            _prune_empty_context_overrides(state)
        _record_agent_metric(state, AGENT_METRIC_NAMES["restaurant_search_success"], size=len(result))
        return None

    return None


# recipe handlers

# get_fridge_items: 缓存食材。表达层交给 LLM，代码层只注入强指令与结构化上下文。
def _handle_get_fridge_items_result(
    state: SmartEatsState,
    _tool_name: str,
    result: object,
) -> dict | None:
    if not isinstance(result, dict):
        return None

    items = result.get("items") if isinstance(result.get("items"), list) else []
    context = _ensure_context(state)
    context[STATE_CONTEXT_KEYS["fridge_items"]] = items

    if not items:
        context_overrides = _ensure_context_overrides(state)
        context_overrides[STATE_CONTEXT_OVERRIDE_KEYS["fridge_empty"]] = True
        context_overrides.pop(STATE_CONTEXT_OVERRIDE_KEYS["system_directive"], None)
        _prune_empty_context_overrides(state)
        return None

    if isinstance(state.context_overrides, dict):
        state.context_overrides.pop(STATE_CONTEXT_OVERRIDE_KEYS["fridge_empty"], None)
        state.context_overrides.pop(STATE_CONTEXT_OVERRIDE_KEYS["system_directive"], None)
        _prune_empty_context_overrides(state)
    return None


def _handle_search_recipes_result(
    _state: SmartEatsState,
    _tool_name: str,
    _result: object,
) -> dict | None:
    return None


def _handle_rag_search_recipes_result(
    state: SmartEatsState,
    _tool_name: str,
    result: object,
) -> dict | None:
    if not isinstance(result, dict):
        return None

    items = result.get("items") if isinstance(result.get("items"), list) else []

    context_overrides = _ensure_context_overrides(state)
    if items:
        context_overrides[STATE_CONTEXT_OVERRIDE_KEYS["rag_recipe_hits"]] = items[:3]
    else:
        context_overrides.pop(STATE_CONTEXT_OVERRIDE_KEYS["rag_recipe_hits"], None)
    context_overrides.pop(STATE_CONTEXT_OVERRIDE_KEYS["system_directive"], None)
    _prune_empty_context_overrides(state)
    return None


# route handlers

def _plan_route_missing_origin_response() -> dict[str, Any]:
    return _build_note_template_final_answer(NOTE_TEMPLATE_KEYS["route_missing_origin"])


def _plan_route_missing_destination_response() -> dict[str, Any]:
    return _build_note_template_final_answer(NOTE_TEMPLATE_KEYS["route_missing_destination"])


def _plan_route_generic_error_response() -> dict[str, Any]:
    return _build_note_template_final_answer(NOTE_TEMPLATE_KEYS["route_generic_error"])


_PLAN_ROUTE_ERROR_HANDLERS: dict[str, Callable[[], dict[str, Any]]] = {
    TOOL_ERROR_CODES["missing_origin"]: _plan_route_missing_origin_response,
    TOOL_ERROR_CODES["missing_destination"]: _plan_route_missing_destination_response,
}


def _handle_plan_route_result(
    state: SmartEatsState,
    _tool_name: str,
    result: object,
) -> dict | None:
    if not isinstance(result, dict):
        return None

    error = result.get("error")
    if not error:
        if any(
            result.get(field)
            for field in (
                "distance_m",
                "duration_s",
                "origin",
                "destination",
                "steps",
                "segments",
            )
        ):
            context_overrides = _ensure_context_overrides(state)
            context_overrides[STATE_CONTEXT_OVERRIDE_KEYS["latest_route"]] = {
                field: result.get(field)
                for field in PLAN_ROUTE_PREVIEW_FIELDS
                if result.get(field) is not None
            }
            context_overrides[STATE_CONTEXT_OVERRIDE_KEYS["system_directive"]] = TOOL_RESULT_SYSTEM_DIRECTIVES["plan_route_ready"]
        return None

    handler = _PLAN_ROUTE_ERROR_HANDLERS.get(error) if isinstance(error, str) else None
    if handler:
        return handler()
    return _plan_route_generic_error_response()


_TOOL_RESULT_DISPATCH: dict[str, Callable[[SmartEatsState, str, object], dict | None]] = {
    # location
    TOOL_NAMES["get_ip_location"]: _handle_location_result,
    TOOL_NAMES["geocode_location"]: _handle_location_result,
    # restaurant
    TOOL_NAMES["search_restaurants"]: _handle_search_restaurants_result,
    # recipe
    TOOL_NAMES["get_fridge_items"]: _handle_get_fridge_items_result,
    TOOL_NAMES["search_recipes"]: _handle_search_recipes_result,
    TOOL_NAMES["rag_search_recipes"]: _handle_rag_search_recipes_result,
    # route
    TOOL_NAMES["plan_route"]: _handle_plan_route_result,
}


def _tool_result_handler(state: SmartEatsState, tool_name: str, result: object) -> dict | None:
    """处理工具返回结果，更新状态或生成最终回复。"""
    _sync_location_source(state, result)

    handler = _TOOL_RESULT_DISPATCH.get(tool_name)
    if not handler:
        return None
    return handler(state, tool_name, result)




def _smart_eats_graph_config() -> SmartEatsGraphConfig:
    return SmartEatsGraphConfig(
        name="smart_eats",
        scene="chat",
        tool_names=[
            TOOL_NAMES["get_weather"],
            TOOL_NAMES["get_fridge_items"],
            TOOL_NAMES["search_recipes"],
            TOOL_NAMES["rag_search_recipes"],
            TOOL_NAMES["search_restaurants"],
            TOOL_NAMES["plan_route"],
            TOOL_NAMES["get_ip_location"],
            TOOL_NAMES["geocode_location"],
            TOOL_NAMES["get_user_info"],
            TOOL_NAMES["memory_search"],
            TOOL_NAMES["memory_write"],
            TOOL_NAMES["memory_update"],
            TOOL_NAMES["memory_forget"],
            TOOL_NAMES["source_event_search"],
            TOOL_NAMES["travel_search_poi"],
            TOOL_NAMES["travel_create_personal_map"],
        ],
        max_steps=6,
        system_prompt_builder=smart_system_prompt,
        writer_prompt_builder=default_writer_prompt,
        action_normalizer=normalize_action_from_raw,
        tool_args_normalizer=smart_tool_args_normalizer,
        tool_result_previewer=smart_tool_result_previewer,
        best_effort_fallback_handler=smart_best_effort_fallback,
    )


def get_smart_eats_agent_config() -> SmartEatsGraphConfig:
    """返回 smart_eats dedicated graph 的配置。"""
    return _smart_eats_graph_config()


def build_smart_eats_graph(
    db: Any,
    redis_client: Any,
    provider: str | None = None,
    resolved_model_config: dict[str, Any] | None = None,
) -> Any:
    """SmartEats 专属官方图构建入口。"""
    from uuid import uuid4

    from langchain_core.messages import HumanMessage, SystemMessage
    from langgraph.graph import StateGraph
    from langgraph.prebuilt import ToolNode

    from app.agent.llm_adapters import OpenAIPlanner, ProviderRegistry, build_planner
    from app.agent.tools_registry import get_langchain_tools

    agent_config = get_smart_eats_agent_config()
    planner_config = (
        ProviderRegistry.from_resolved_config(resolved_model_config)
        if isinstance(resolved_model_config, dict) and resolved_model_config.get("source") == "user_config"
        else None
    )
    planner = build_planner(provider=provider, config=planner_config)
    allowed_tools = agent_config.tool_names

    tool_node = ToolNode(
        [
            *get_langchain_tools(allowlist=allowed_tools),
            _build_submit_final_answer_tool(),
        ],
        messages_key="messages",
    )

    async def prepare_node(state: dict[str, Any], store: Any = None) -> dict[str, Any]:
        initialized = _initialize_graph_state(state)
        chat_state = _state_from_dict(initialized)
        first_round = (
            chat_state.steps_left <= 0
            and not chat_state.tool_calls
            and not chat_state.observations
        )
        if first_round:
            chat_state.steps_left = agent_config.max_steps
        await _ensure_chat_session(db, chat_state)
        existing_messages = list(state.get("messages") or [])
        pending_messages = list(initialized.get("messages") or [])
        await _prepare_langgraph_context(
            db,
            redis_client,
            chat_state,
            agent_config,
            store=store,
            messages=[*existing_messages, *pending_messages],
            emit_context_event=first_round,
        )

        output = _state_update(chat_state)
        output["messages"] = pending_messages
        return output

    async def summarize_node(state: dict[str, Any], store: Any = None) -> dict[str, Any]:
        from app.agent.llm_adapters import OpenAIWriter

        chat_state = _state_from_dict(state)
        messages = list(state.get("messages") or [])
        previous_summary = chat_state.summary
        keep_recent = max(2, int(len(messages) * settings.CHAT_COMPACT_TAIL_RATIO))
        keep_recent = max(keep_recent, 4)
        user_turn_count = sum(1 for message in messages if getattr(message, "type", None) == "human")
        keep_recent_turns = max(2, int(user_turn_count * settings.CHAT_COMPACT_TAIL_RATIO))
        removable = messages[: max(0, len(messages) - keep_recent)]
        if not removable:
            return _state_update(chat_state)

        prompt = build_summary_prompt(previous_summary=previous_summary, messages=removable)
        chunks: list[str] = []
        try:
            writer = OpenAIWriter(provider=chat_state.provider or settings.LLM_PROVIDER)
            async for delta in writer.stream("你是对话摘要器。", prompt):
                chunks.append(delta)
            new_summary = "".join(chunks).strip()
        except Exception as exc:
            logger.info("langgraph_summary_failed session_id=%s reason=%s", chat_state.session_id, str(exc))
            new_summary = ""
        if not new_summary:
            new_summary = prompt[:1600]
        normalized = normalize_summary_output(new_summary)
        if not normalized.get("valid"):
            repair_chunks: list[str] = []
            repair_prompt = build_summary_repair_prompt(raw_output=new_summary, original_prompt=prompt)
            try:
                writer = OpenAIWriter(provider=chat_state.provider or settings.LLM_PROVIDER)
                async for delta in writer.stream("你是对话摘要器。", repair_prompt):
                    repair_chunks.append(delta)
                repaired = "".join(repair_chunks).strip()
                if repaired:
                    repaired_normalized = normalize_summary_output(repaired)
                    if repaired_normalized.get("valid"):
                        normalized = repaired_normalized
            except Exception as exc:
                logger.info("langgraph_summary_repair_failed session_id=%s reason=%s", chat_state.session_id, str(exc))

        update = build_summary_update(
            messages,
            previous_summary=previous_summary,
            new_summary=normalized["summary"],
            keep_recent=keep_recent,
            keep_recent_turns=keep_recent_turns,
            summary_json=normalized["summary_json"],
            source_refs=chat_state.source_refs,
        )
        chat_state.summary = update["summary"]
        chat_state.context_budget = update["context_budget"]
        reduction = float(chat_state.context_budget.get("last_compaction_reduction_ratio") or 0.0)
        previous_attempts = int((state.get("context_budget") or {}).get("compact_attempts") or 0)
        chat_state.context_budget["compact_attempts"] = (
            previous_attempts + 1
            if reduction < settings.CHAT_COMPACT_MIN_REDUCTION_RATIO
            else 0
        )
        if isinstance((state.get("context_budget") or {}).get("active_context"), dict):
            chat_state.context_budget["active_context_before"] = (state.get("context_budget") or {}).get("active_context")
        memory_writes = await persist_summary_memories(
            store,
            user_id=chat_state.user_id,
            summary_json=normalized["summary_json"],
        )
        if memory_writes:
            chat_state.context_budget["summary_memory_write_count"] = len(memory_writes)
        await save_compaction_run(store, thread_id=chat_state.session_id, summary_update=update)
        output = _state_update(chat_state)
        output["messages"] = update["messages"]
        return output

    async def agent_node(state: dict[str, Any]) -> dict[str, Any]:
        chat_state = _state_from_dict(state)

        if chat_state.intent_need_clarify and chat_state.intent_confidence < INTENT_CLARIFY_CONFIDENCE_THRESHOLD:
            question = chat_state.intent_clarify_question or "你是想出去吃，还是在家做饭？"
            chat_state.final_json = _build_note_template_final_answer(
                NOTE_TEMPLATE_KEYS["intent_clarify"],
                title_override=question,
            )
            return _state_update(chat_state)

        system = None
        if chat_state.context:
            system = chat_state.context.get("system_prompt")
        if not system:
            system = agent_config.system_prompt_builder({"context": chat_state.context})
        user = chat_state.message or ""
        current_allowed_tools = allowed_tools
        if isinstance(chat_state.context, dict) and isinstance(chat_state.context.get("allowed_tools"), list):
            current_allowed_tools = [
                item for item in chat_state.context.get("allowed_tools", [])
                if isinstance(item, str) and item in allowed_tools
            ] or allowed_tools
        current_langchain_tools = [
            *get_langchain_tools(allowlist=current_allowed_tools, inject_runtime_context=False),
            _build_submit_final_answer_tool(),
        ]

        image_parts: list[dict[str, Any]] = []
        active_planner = planner
        if (
            settings.LLM_VISION_ENABLED
            and chat_state.scene == "travel_planner"
            and isinstance(chat_state.context, dict)
            and isinstance(chat_state.context.get("attachments"), list)
        ):
            try:
                from app.agent.vision import build_vision_content_parts
                from app.infra.minio import get_minio

                image_parts = await build_vision_content_parts(
                    chat_state.context.get("attachments"),
                    minio=await get_minio(),
                )
                if image_parts and settings.LLM_VISION_PROVIDER:
                    active_planner = OpenAIPlanner(provider=settings.LLM_VISION_PROVIDER)
            except Exception as exc:
                logger.info(
                    "vision_input_build_failed session_id=%s reason=%s",
                    chat_state.session_id,
                    str(exc),
                )

        state_messages = state.get("messages")
        if isinstance(state_messages, list) and state_messages:
            planner_messages = build_model_messages(
                system_prompt=system,
                summary=chat_state.summary,
                messages=state_messages,
                memories=chat_state.retrieved_memories,
            )
        else:
            planner_messages = [SystemMessage(content=system), HumanMessage(content=user)]
        ai_message = await active_planner.ainvoke_with_tools(
            planner_messages,
            current_langchain_tools,
            image_parts=image_parts or None,
        )
        raw_content = ai_message.content
        normalized_tool_calls = ai_message.tool_calls

        if isinstance(normalized_tool_calls, list) and normalized_tool_calls:
            tool_calls: list[dict[str, Any]] = []
            for index, call in enumerate(normalized_tool_calls):
                tool_name = call.get("name") if isinstance(call, dict) else None
                args = call.get("args") if isinstance(call, dict) else None
                call_id = call.get("id") if isinstance(call, dict) else None
                if not isinstance(tool_name, str) or not isinstance(args, dict):
                    continue
                normalized_args = args
                if tool_name not in current_allowed_tools and tool_name != TOOL_NAMES["submit_final_answer"]:
                    continue
                if tool_name in current_allowed_tools and agent_config.tool_args_normalizer:
                    normalized_args = agent_config.tool_args_normalizer(tool_name, args)
                if not isinstance(call_id, str) or not call_id:
                    call_id = f"call_{uuid4().hex[:12]}_{index}"
                tool_calls.append(
                    {
                        "name": tool_name,
                        "args": normalized_args,
                        "id": call_id,
                        "type": "tool_call",
                    }
                )

            if tool_calls:
                max_tool_calls = _skill_max_tool_calls_per_turn(chat_state.context)
                limited_tool_calls = _limit_skill_tool_calls(tool_calls, max_tool_calls=max_tool_calls)
                if len(limited_tool_calls) < len(tool_calls):
                    logger.info(
                        "skill_tool_calls_limited session_id=%s max=%s original=%s kept=%s",
                        chat_state.session_id,
                        max_tool_calls,
                        len(tool_calls),
                        len(limited_tool_calls),
                    )
                ai_message.tool_calls = limited_tool_calls
                output = _state_update(chat_state)
                output["messages"] = [ai_message]
                return output

        content = raw_content if isinstance(raw_content, str) else ""
        if content and agent_config.action_normalizer:
            mapped = agent_config.action_normalizer(content)
            if isinstance(mapped, FinalAction):
                final = mapped.answer
                chat_state.final_json = final.model_dump() if isinstance(final, FinalAnswer) else final
                return _state_update(chat_state)

        final_action = planner.final_action_from_text(content)
        final = final_action.answer
        chat_state.final_json = final.model_dump() if isinstance(final, FinalAnswer) else final
        return _state_update(chat_state)

    async def tools_node(state: dict[str, Any], store: Any = None) -> dict[str, Any]:
        chat_state = _state_from_dict(state)
        ai_messages = _latest_ai_messages(state.get("messages"))
        if not ai_messages:
            return _state_update(chat_state)

        tool_output = await _invoke_tool_node_with_runtime(
            tool_node,
            ai_messages,
            chat_state=chat_state,
            db=db,
            redis_client=redis_client,
            store=store,
        )
        tool_messages = _normalize_official_tool_messages(tool_output)
        preview_tool_messages = _preview_tool_messages(tool_messages, agent_config)
        call_args_map = _collect_tool_call_args(ai_messages)

        await _apply_official_tool_postprocess(
            chat_state,
            tool_messages=tool_messages,
            call_args_map=call_args_map,
            db=db,
            redis_client=redis_client,
            agent_config=agent_config,
            store=store,
        )
        _finalize_official_after_tools(chat_state, agent_config)

        output = _state_update(chat_state)
        output["messages"] = preview_tool_messages
        return output

    graph = StateGraph(SmartEatsGraphState)
    graph.add_node("prepare", prepare_node)
    graph.add_node("summarize", summarize_node)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)

    graph.add_conditional_edges("prepare", _route_after_prepare)
    graph.add_edge("summarize", "agent")
    graph.add_conditional_edges("agent", _route_after_agent)
    graph.add_conditional_edges("tools", _route_after_tools)
    graph.set_entry_point("prepare")
    logger.info("agent_graph_runtime mode=official agent=smart_eats phase=dedicated")
    return graph
