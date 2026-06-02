from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages

from app.agent import conversation
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
from app.agent.runtime.finalization import fallback_final, final_json_from_text
from app.agent.runtime.hooks import SkillHookManager
from app.agent.schemas import FinalAnswerArgs
from app.common.config import settings

logger = logging.getLogger("agent.runtime")

SUBMIT_FINAL_TOOL_NAME = "submit_final_answer"
CORE_TOOL_NAMES: tuple[str, ...] = (
    "memory_search",
    "memory_write",
    "memory_update",
    "memory_forget",
    "source_event_search",
)


@dataclass
class AgentRuntimeState:
    session_id: str
    user_id: str | None = None
    message: str | None = None
    trace_id: str | None = None
    scene: str = "chat"
    agent_id: str | None = None
    plan_type: str | None = None
    context_overrides: dict[str, Any] | None = None
    snapshot: dict[str, Any] | None = None
    context: dict[str, Any] | None = None
    steps_left: int = 0
    turn_index: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    pending_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
    final_json: dict[str, Any] | None = None
    provider: str | None = None
    resolved_model_config: dict[str, Any] | None = None
    client_ip: str | None = None
    resume_from_checkpoint: bool = False
    checkpoint_ref: str | None = None
    replay_from_checkpoint: bool = False
    resume_payload: dict[str, Any] | None = None
    last_user_message: str | None = None
    user_message_logged: bool = False
    history: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    skill_state: dict[str, Any] = field(default_factory=dict)
    summary: str | None = None
    context_budget: dict[str, Any] = field(default_factory=dict)
    retrieved_memories: list[dict[str, Any]] = field(default_factory=list)
    source_refs: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class AgentRuntimeConfig:
    name: str
    core_tool_names: list[str]
    max_steps: int = 6
    system_prompt_builder: Any = None


@dataclass(frozen=True)
class AgentRuntimeContext:
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


class AgentRuntimeGraphState(TypedDict, total=False):
    messages: Annotated[list[Any], add_messages]
    session_id: str
    user_id: str | None
    message: str | None
    trace_id: str | None
    scene: str
    agent_id: str | None
    plan_type: str | None
    context_overrides: dict[str, Any] | None
    snapshot: dict[str, Any] | None
    context: dict[str, Any] | None
    steps_left: int
    turn_index: int
    tool_calls: list[dict[str, Any]]
    pending_tool_calls: list[dict[str, Any]]
    observations: list[dict[str, Any]]
    final_json: dict[str, Any] | None
    provider: str | None
    resolved_model_config: dict[str, Any] | None
    client_ip: str | None
    resume_from_checkpoint: bool
    checkpoint_ref: str | None
    replay_from_checkpoint: bool
    resume_payload: dict[str, Any] | None
    last_user_message: str | None
    user_message_logged: bool
    history: list[dict[str, Any]]
    events: list[dict[str, Any]]
    skill_state: dict[str, Any]
    summary: str | None
    context_budget: dict[str, Any]
    retrieved_memories: list[dict[str, Any]]
    source_refs: list[dict[str, Any]]
    runtime_context: dict[str, Any]


_AGENT_RUNTIME_STATE_FIELDS = set(AgentRuntimeState.__dataclass_fields__.keys())


def _state_from_dict(payload: dict[str, Any]) -> AgentRuntimeState:
    filtered = {key: value for key, value in payload.items() if key in _AGENT_RUNTIME_STATE_FIELDS}
    filtered["events"] = []
    return AgentRuntimeState(**filtered)


def _state_to_dict(state: AgentRuntimeState) -> dict[str, Any]:
    return dict(state.__dict__)


def _state_update(state: AgentRuntimeState) -> dict[str, Any]:
    return _state_to_dict(state)


def _registered_tool_names() -> list[str]:
    from app.agent.tools import tool_names

    return tool_names()


def _budget_model_name(state: AgentRuntimeState) -> str | None:
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
    state: AgentRuntimeState,
    agent_config: AgentRuntimeConfig,
    *,
    store: Any = None,
    messages: list[Any] | None = None,
    emit_context_event: bool = True,
) -> None:
    from langchain_core.messages import HumanMessage

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
        summary=state.summary,
    )
    base_context = _merge_prompt_context_overrides(state, base_context)
    base_context, skill_prompt = await _resolve_runtime_skills(
        state,
        base_context,
        agent_config,
        runtime={"db": db, "redis_client": redis_client, "store": store},
    )
    system_prompt = agent_config.system_prompt_builder(
        {
            "context": base_context,
            "skill_prompt": skill_prompt,
        }
    )
    if _should_persist_user_message(state) and db is not None and hasattr(db, "execute"):
        await conversation.save_user_message(
            db,
            redis_client,
            state.session_id,
            state.message,
        )
        state.user_message_logged = True
        state.last_user_message = state.message

    visible_messages = messages or []
    state.turn_index = sum(1 for msg in visible_messages if isinstance(msg, HumanMessage))
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
        "allowed_tools": list(base_context.get("allowed_tools") or agent_config.core_tool_names),
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


def _build_submit_final_answer_tool() -> Any:
    from langchain_core.tools import StructuredTool

    async def _submit_final_answer(**kwargs: Any) -> dict[str, Any]:
        args = FinalAnswerArgs.model_validate(kwargs)
        return {"_final_answer": args.model_dump()}

    return StructuredTool.from_function(
        coroutine=_submit_final_answer,
        name=SUBMIT_FINAL_TOOL_NAME,
        description="当你已收集足够信息并准备给用户最终回复时调用。",
        args_schema=FinalAnswerArgs,
        infer_schema=False,
    )


def _build_official_runtime_context(
    state: AgentRuntimeState,
    *,
    db: Any,
    redis_client: Any,
    servers_path: str | None,
) -> dict[str, Any]:
    return AgentRuntimeContext(
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
        if call.get("name") == SUBMIT_FINAL_TOOL_NAME:
            limited.append(call)
            continue
        if external_count >= max_tool_calls:
            continue
        limited.append(call)
        external_count += 1
    return limited


def _build_tool_runtime_payload(
    chat_state: AgentRuntimeState,
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
    chat_state: AgentRuntimeState,
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


def _build_result_preview(
    state: AgentRuntimeState,
    tool_name: str | None,
    result: Any,
) -> Any:
    from app.agent.runtime.tool_preview import preview_result

    if tool_name:
        customized = _hook_manager_from_context(state.context).preview_tool_result(state, tool_name, result)
        if customized is not None:
            return customized
    return preview_result(result)


def _best_effort_final_from_observations(
    state: AgentRuntimeState,
    agent_config: AgentRuntimeConfig | None = None,
) -> dict[str, Any]:
    try:
        skill_fallback = _hook_manager_from_context(state.context).best_effort_fallback(state)
    except Exception:
        skill_fallback = None
    if isinstance(skill_fallback, dict):
        return skill_fallback
    return fallback_final()


async def _apply_official_tool_postprocess(
    chat_state: AgentRuntimeState,
    *,
    tool_messages: list[Any],
    call_args_map: dict[str, dict[str, Any]],
    db: Any,
    redis_client: Any,
    agent_config: AgentRuntimeConfig,
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

        if tool_name == SUBMIT_FINAL_TOOL_NAME and isinstance(result, dict):
            final_payload = result.get("_final_answer")
            if isinstance(final_payload, dict):
                chat_state.final_json = final_payload
            # 先让 hooks 处理 submit_final_answer，如果 hooks 返回了结构化数据则覆盖 LLM 的输出
            handled = _hook_manager_from_context(chat_state.context).handle_tool_result(chat_state, tool_name, result)
            if handled:
                chat_state.final_json = handled
            continue

        result_preview = _build_result_preview(chat_state, tool_name, result)
        chat_state.tool_calls.append({"name": tool_name, "args": args, "latency_ms": 0})
        chat_state.observations.append({"tool": tool_name, "result": result})

        handled = _hook_manager_from_context(chat_state.context).handle_tool_result(chat_state, tool_name, result)
        if handled:
            chat_state.final_json = handled

        if db is not None and hasattr(db, "execute"):
            await conversation.save_tool_message(
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
    if chat_state.context_overrides:
        refreshed_context = _merge_context(chat_state.context or {}, chat_state.context_overrides)
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
    chat_state: AgentRuntimeState,
) -> list[Any]:
    from langchain_core.messages import ToolMessage

    preview_messages: list[Any] = []
    for message in tool_messages:
        tool_name = _extract_tool_name_from_message(message)
        if not tool_name or tool_name == SUBMIT_FINAL_TOOL_NAME:
            preview_messages.append(message)
            continue
        artifact = getattr(message, "artifact", None)
        raw_payload = artifact if artifact is not None else getattr(message, "content", None)
        result = _decode_tool_content(raw_payload)
        preview = _build_result_preview(chat_state, tool_name, result)
        preview_messages.append(
            ToolMessage(
                content=json.dumps(preview, ensure_ascii=False),
                name=tool_name,
                tool_call_id=str(getattr(message, "tool_call_id", None) or ""),
                id=getattr(message, "id", None),
            )
        )
    return preview_messages


def _finalize_official_after_tools(chat_state: AgentRuntimeState, agent_config: AgentRuntimeConfig) -> None:
    chat_state.steps_left -= 1
    if chat_state.steps_left <= 0 and not chat_state.final_json:
        chat_state.final_json = _best_effort_final_from_observations(chat_state, agent_config)
    chat_state.pending_tool_calls = []


def _official_is_final(state: dict[str, Any]) -> bool:
    return bool(state.get("final_json"))


def _next_after_prepare(state: dict[str, Any]) -> str:
    context_budget = state.get("context_budget")
    if _official_is_final(state):
        next_node = "__end__"
    elif isinstance(context_budget, dict) and context_budget.get("should_compact"):
        next_node = "summarize"
    else:
        next_node = "agent"
    logger.info("graph_next node=prepare next=%s session_id=%s", next_node, state.get("session_id"))
    return next_node


def _next_after_agent(state: dict[str, Any]) -> str:
    if _official_is_final(state):
        next_node = "__end__"
    else:
        from langgraph.prebuilt import tools_condition

        next_node = tools_condition(state, messages_key="messages")
    logger.info("graph_next node=agent next=%s session_id=%s", next_node, state.get("session_id"))
    return next_node


def _next_after_tools(state: dict[str, Any]) -> str:
    next_node = "__end__" if _official_is_final(state) else "agent"
    logger.info("graph_next node=tools next=%s session_id=%s", next_node, state.get("session_id"))
    return next_node


def _merge_context(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_context(merged[key], value)
        else:
            merged[key] = value
    return merged


async def _ensure_chat_session(db: Any, state: AgentRuntimeState) -> None:
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


def _should_persist_user_message(state: AgentRuntimeState) -> bool:
    return bool(state.message and (not state.user_message_logged or state.last_user_message != state.message))


def _build_base_prompt_context(
    state: AgentRuntimeState,
    *,
    memories: list[str],
    summary: str | None,
) -> dict[str, Any]:
    context: dict[str, Any] = {"ui_scene": state.scene or "chat"}
    if state.agent_id:
        context["agent_id"] = state.agent_id
    if state.plan_type:
        context["plan_type"] = state.plan_type
    context["user_message"] = state.message or ""
    context["history"] = state.history
    if summary:
        context["history_summary"] = summary
    if memories:
        context["memories"] = memories
    context["observations"] = list(state.observations)
    context["checkpoint"] = {
        "resume_from_checkpoint": state.resume_from_checkpoint,
        "checkpoint_ref": state.checkpoint_ref,
        "replay_from_checkpoint": state.replay_from_checkpoint,
    }
    return context


def _merge_prompt_context_overrides(state: AgentRuntimeState, context: dict[str, Any]) -> dict[str, Any]:
    if isinstance(state.context_overrides, dict) and state.context_overrides:
        context = _merge_context(context, state.context_overrides)
    return context


async def _resolve_runtime_skills(
    state: AgentRuntimeState,
    context: dict[str, Any],
    agent_config: AgentRuntimeConfig,
    *,
    runtime: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    from app.agent.skills.runtime import SkillRuntime

    registered_tools = _registered_tool_names()
    requested_tools = context.get("allowed_tools")
    if isinstance(requested_tools, list):
        base_tools = [
            item
            for item in requested_tools
            if isinstance(item, str) and item in set(agent_config.core_tool_names)
        ]
    else:
        base_tools = list(agent_config.core_tool_names)
    base_tools = [item for item in base_tools if item in registered_tools]

    skill_runtime = SkillRuntime(
        skills_path=settings.AGENT_SKILLS_PATH,
        enabled=settings.AGENT_SKILLS_ENABLED,
        max_active=settings.AGENT_SKILLS_MAX_ACTIVE,
        max_prompt_chars=settings.AGENT_SKILLS_MAX_PROMPT_CHARS,
        global_allowlist=registered_tools,
        log_diagnostics=settings.AGENT_SKILLS_LOG_DIAGNOSTICS,
    ).resolve(
        state,
        context,
        base_tools=base_tools,
    )
    if skill_runtime.context:
        context = _merge_context(context, skill_runtime.context)
    hook_context = await SkillHookManager.from_skills(skill_runtime.active_skill_specs).build_context(
        state,
        context,
        runtime,
    )
    if hook_context:
        context = _merge_context(context, hook_context)
    allowed_tools = skill_runtime.allowed_tools if skill_runtime.active_skills else base_tools
    context["allowed_tools"] = SkillHookManager.from_skills(skill_runtime.active_skill_specs).filter_allowed_tools(
        state,
        allowed_tools,
    )
    return context, skill_runtime.system_prompt_addendum


def _hook_manager_from_context(context: dict[str, Any] | None) -> SkillHookManager:
    if not isinstance(context, dict):
        return SkillHookManager()
    active_payload = context.get("active_skills")
    if not isinstance(active_payload, list):
        return SkillHookManager()
    active_ids = [
        item.get("id")
        for item in active_payload
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]
    if not active_ids:
        return SkillHookManager()
    from app.agent.skills.registry import SkillRegistry

    registry = SkillRegistry(settings.AGENT_SKILLS_PATH)
    skills = [
        skill
        for skill in registry.get_enabled()
        if skill.id in set(active_ids)
    ]
    skills.sort(key=lambda skill: active_ids.index(skill.id))
    return SkillHookManager.from_skills(skills)


def generic_system_prompt(payload: dict[str, Any]) -> str:
    skill_prompt = payload.get("skill_prompt")
    skill_block = f"\n\n{skill_prompt.strip()}" if isinstance(skill_prompt, str) and skill_prompt.strip() else ""
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    directive = context.get("system_directive") if isinstance(context, dict) else None
    directive_block = (
        f"\n\n## Active System Directive\n{directive.strip()}"
        if isinstance(directive, str) and directive.strip()
        else ""
    )
    context_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return (
        "你是一个通用的 Skill Agent Runtime。只使用当前绑定的工具；"
        "当需要结构化结束时调用 submit_final_answer，也可以在无需工具时直接给出最终文本。"
        f"{skill_block}{directive_block}\n\n"
        "## Runtime Context（系统注入，非用户输入）\n"
        f"- output_language: {settings.DEFAULT_LANGUAGE}\n"
        f"- context: {context_json}"
    )


def _agent_runtime_config() -> AgentRuntimeConfig:
    return AgentRuntimeConfig(
        name="generic_runtime",
        core_tool_names=list(CORE_TOOL_NAMES),
        max_steps=6,
        system_prompt_builder=generic_system_prompt,
    )


def get_agent_runtime_config() -> AgentRuntimeConfig:
    return _agent_runtime_config()


def build_agent_runtime_graph(
    db: Any,
    redis_client: Any,
    provider: str | None = None,
    resolved_model_config: dict[str, Any] | None = None,
) -> Any:
    from uuid import uuid4

    from langchain_core.messages import HumanMessage, SystemMessage
    from langgraph.graph import StateGraph
    from langgraph.prebuilt import ToolNode

    from app.agent.llm_adapters import OpenAIPlanner, ProviderRegistry, build_planner
    from app.agent.tools import select_tools

    agent_config = get_agent_runtime_config()
    planner_config = (
        ProviderRegistry.from_resolved_config(resolved_model_config)
        if isinstance(resolved_model_config, dict) and resolved_model_config.get("source") == "user_config"
        else None
    )
    planner = build_planner(provider=provider, config=planner_config)
    registered_tools = _registered_tool_names()

    tool_node = ToolNode(
        [
            *select_tools(registered_tools),
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
        hook_manager = _hook_manager_from_context(chat_state.context)

        short_circuit_final = hook_manager.short_circuit_final(chat_state)
        if short_circuit_final:
            chat_state.final_json = short_circuit_final
            return _state_update(chat_state)

        system = chat_state.context.get("system_prompt") if isinstance(chat_state.context, dict) else None
        if not system:
            system = agent_config.system_prompt_builder({"context": chat_state.context})
        user = chat_state.message or ""
        current_allowed_tools = list(agent_config.core_tool_names)
        if isinstance(chat_state.context, dict) and isinstance(chat_state.context.get("allowed_tools"), list):
            current_allowed_tools = [
                item
                for item in chat_state.context.get("allowed_tools", [])
                if isinstance(item, str) and item in registered_tools
            ]
        active_skills_payload = chat_state.context.get("active_skills") if isinstance(chat_state.context, dict) else None
        active_skill_ids = [
            item.get("id")
            for item in active_skills_payload
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ] if isinstance(active_skills_payload, list) else None
        logger.info(
            "agent_tools_resolved session_id=%s scene=%s active_skills=%s allowed_tools=%s",
            chat_state.session_id,
            chat_state.scene,
            active_skill_ids,
            current_allowed_tools,
        )
        current_langchain_tools = [*select_tools(current_allowed_tools)]
        if hook_manager.allow_submit_final_answer(chat_state):
            current_langchain_tools.append(_build_submit_final_answer_tool())

        image_parts: list[dict[str, Any]] = []
        active_planner = planner
        has_attachments = bool(isinstance(chat_state.context, dict) and chat_state.context.get("attachments"))
        should_build_vision = settings.LLM_VISION_ENABLED and hook_manager.should_build_vision_input(chat_state)
        logger.info(
            "vision_check session_id=%s vision_enabled=%s has_attachments=%s should_build=%s",
            chat_state.session_id,
            settings.LLM_VISION_ENABLED,
            has_attachments,
            should_build_vision,
        )
        if should_build_vision:
            try:
                from app.agent.vision import build_vision_content_parts
                from app.infra.minio import get_minio

                image_parts = await build_vision_content_parts(
                    chat_state.context.get("attachments"),
                    minio=await get_minio(),
                )
                logger.info(
                    "vision_parts_built session_id=%s image_count=%s",
                    chat_state.session_id,
                    len(image_parts),
                )
                if image_parts and settings.LLM_VISION_PROVIDER:
                    active_planner = OpenAIPlanner(provider=settings.LLM_VISION_PROVIDER)
                logger.info(
                    "vision_llm_call session_id=%s model=%s has_image_parts=%s vision_provider=%s",
                    chat_state.session_id,
                    active_planner.config.model_planner,
                    bool(image_parts),
                    settings.LLM_VISION_PROVIDER,
                )
            except Exception as exc:
                logger.warning(
                    "vision_input_build_failed session_id=%s reason=%s",
                    chat_state.session_id,
                    str(exc),
                )
                chat_state.events.append(
                    {
                        "event": "vision_error",
                        "data": {
                            "message": f"图片处理失败：{exc}，请重新上传或用文字描述地点"
                        },
                    }
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
        try:
            ai_message = await active_planner.ainvoke_with_tools(
                planner_messages,
                current_langchain_tools,
                image_parts=image_parts or None,
            )
        except Exception as exc:
            msg = str(exc)
            if image_parts and ("image input" in msg.lower() or "image" in msg.lower() or "vision" in msg.lower()):
                logger.warning(
                    "vision_llm_rejected session_id=%s model=%s reason=%s",
                    chat_state.session_id,
                    active_planner.config.model_planner,
                    msg[:200],
                )
                chat_state.events.append(
                    {
                        "event": "vision_error",
                        "data": {
                            "message": "当前模型不支持图片识别，请用文字描述地点"
                        },
                    }
                )
                image_parts = []
                ai_message = await active_planner.ainvoke_with_tools(
                    planner_messages,
                    current_langchain_tools,
                    image_parts=None,
                )
            else:
                raise
        raw_content = ai_message.content
        content = raw_content if isinstance(raw_content, str) else ""
        if content:
            skill_state = dict(chat_state.skill_state or {})
            skill_state["last_ai_message_content"] = content
            ai_message_contents = skill_state.get("ai_message_contents")
            if not isinstance(ai_message_contents, list):
                ai_message_contents = []
            ai_message_contents.append(content)
            skill_state["ai_message_contents"] = ai_message_contents[-6:]
            chat_state.skill_state = skill_state
        normalized_tool_calls = ai_message.tool_calls
        if not normalized_tool_calls:
            forced_tool_calls = hook_manager.forced_tool_calls(chat_state)
            if forced_tool_calls:
                logger.info(
                    "agent_forcing_skill_tool_calls session_id=%s tools=%s",
                    chat_state.session_id,
                    [call.get("name") for call in forced_tool_calls],
                )
                normalized_tool_calls = forced_tool_calls
        logger.info(
            "agent_tool_choice session_id=%s scene=%s tool_calls=%s",
            chat_state.session_id,
            chat_state.scene,
            [call.get("name") for call in normalized_tool_calls] if isinstance(normalized_tool_calls, list) else [],
        )

        if isinstance(normalized_tool_calls, list) and normalized_tool_calls:
            tool_calls: list[dict[str, Any]] = []
            for index, call in enumerate(normalized_tool_calls):
                tool_name = call.get("name") if isinstance(call, dict) else None
                args = call.get("args") if isinstance(call, dict) else None
                call_id = call.get("id") if isinstance(call, dict) else None
                if not isinstance(tool_name, str) or not isinstance(args, dict):
                    continue
                normalized_args = args
                if tool_name not in current_allowed_tools and tool_name != SUBMIT_FINAL_TOOL_NAME:
                    continue
                if tool_name in current_allowed_tools:
                    normalized_args = hook_manager.normalize_tool_args(
                        chat_state,
                        tool_name,
                        args,
                    )
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

        chat_state.final_json = final_json_from_text(content)
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
        preview_tool_messages = _preview_tool_messages(tool_messages, chat_state)
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

    graph = StateGraph(AgentRuntimeGraphState)
    graph.add_node("prepare", prepare_node)
    graph.add_node("summarize", summarize_node)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)

    graph.add_conditional_edges("prepare", _next_after_prepare)
    graph.add_edge("summarize", "agent")
    graph.add_conditional_edges("agent", _next_after_agent)
    graph.add_conditional_edges("tools", _next_after_tools)
    graph.set_entry_point("prepare")
    logger.info("agent_graph_runtime mode=official agent=generic_runtime")
    return graph
