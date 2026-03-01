from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections import Counter
from typing import Any
from uuid import uuid4

import redis.asyncio as redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import history, memory
from app.agent.agent_registry import AgentConfig
from app.agent.schemas import FinalAnswer, FinalAnswerArgs
from app.agent.state import LegacyChatState as ChatState
from app.agent.tools_registry import preview_result
from app.common.config import settings
from app.infra.models.chat import ChatMessage, ChatSession

logger = logging.getLogger("agent")
_METRIC_COUNTER: Counter[str] = Counter()
_METRIC_LOCK = threading.Lock()
_CHAT_STATE_FIELDS = set(ChatState.__dataclass_fields__.keys())


def _state_from_dict(payload: dict[str, Any]) -> ChatState:
    filtered = {key: value for key, value in payload.items() if key in _CHAT_STATE_FIELDS}
    return ChatState(**filtered)


def _state_to_dict(state: ChatState) -> dict[str, Any]:
    return dict(state.__dict__)


def _build_submit_final_answer_tool() -> Any:
    from langchain_core.tools import StructuredTool

    async def _submit_final_answer(**kwargs: Any) -> dict[str, Any]:
        args = FinalAnswerArgs.model_validate(kwargs)
        return {"_final_answer": args.model_dump()}

    return StructuredTool.from_function(
        coroutine=_submit_final_answer,
        name="submit_final_answer",
        description="当你已收集足够信息并准备给用户最终回复时调用。",
        args_schema=FinalAnswerArgs,
        infer_schema=False,
    )


async def _apply_official_tool_postprocess(
    chat_state: ChatState,
    *,
    tool_messages: list[Any],
    call_args_map: dict[str, dict[str, Any]],
    db: AsyncSession,
    redis_client: redis.Redis,
    agent_config: AgentConfig,
) -> None:
    for message in tool_messages:
        tool_name = getattr(message, "name", None)
        if not isinstance(tool_name, str) or not tool_name:
            continue
        tool_call_id = getattr(message, "tool_call_id", None)
        args = call_args_map.get(tool_call_id, {}) if isinstance(tool_call_id, str) else {}

        artifact = getattr(message, "artifact", None)
        raw_payload = artifact if artifact is not None else getattr(message, "content", None)
        if isinstance(raw_payload, str):
            try:
                result = json.loads(raw_payload)
            except json.JSONDecodeError:
                result = raw_payload
        else:
            result = raw_payload

        if tool_name == "submit_final_answer" and isinstance(result, dict):
            final_payload = result.get("_final_answer")
            if isinstance(final_payload, dict):
                chat_state.final_json = final_payload
            continue

        result_preview = _build_result_preview(agent_config, tool_name, result)
        chat_state.tool_calls.append({"name": tool_name, "args": args, "latency_ms": 0})
        chat_state.observations.append({"tool": tool_name, "result": result})
        _observe_recovery(chat_state, tool_name, result)

        if agent_config.tool_result_handler:
            handled = agent_config.tool_result_handler(chat_state, tool_name, result)
            if handled:
                chat_state.final_json = handled

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


def _finalize_official_after_tools(chat_state: ChatState, agent_config: AgentConfig) -> None:
    chat_state.steps_left -= 1
    if chat_state.steps_left <= 0 and not chat_state.final_json:
        chat_state.final_json = _best_effort_final_from_observations(chat_state, agent_config)
    chat_state.pending_tool_calls = []


def _official_is_final(state: dict[str, Any]) -> bool:
    return bool(state.get("next_action") == "final" or state.get("final_json"))


def _record_metric(state: ChatState, name: str, value: int | float = 1, **tags: Any) -> None:
    payload = {
        "metric": name,
        "value": value,
        "session_id": state.session_id,
        **tags,
    }
    with _METRIC_LOCK:
        try:
            _METRIC_COUNTER[name] += int(value)
        except Exception:
            _METRIC_COUNTER[name] += 1
    logger.info("metric %s", json.dumps(payload, ensure_ascii=False))


def get_agent_metrics_snapshot() -> dict[str, int]:
    with _METRIC_LOCK:
        return dict(_METRIC_COUNTER)


def reset_agent_metrics() -> None:
    with _METRIC_LOCK:
        _METRIC_COUNTER.clear()


def _build_observation_context(
    state: ChatState,
    agent_config: AgentConfig,
    summary: str | None,
    memories: list[str],
) -> dict[str, Any]:
    context = {"ui_scene": state.scene or "chat"}
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
    if agent_config.context_extender:
        extra = agent_config.context_extender(state)
        if extra:
            context = _merge_context(context, extra)
    if isinstance(state.context_overrides, dict) and state.context_overrides:
        context = _merge_context(context, state.context_overrides)
    context["system_prompt"] = agent_config.system_prompt_builder({"context": context})
    return context


def _merge_context(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_context(merged[key], value)
        else:
            merged[key] = value
    return merged


def _count_user_turns(history_items: list[dict[str, Any]]) -> int:
    return sum(1 for item in history_items if item.get("role") == "user")


def _observe_recovery(state: ChatState, tool_name: str | None, result: Any) -> None:
    if not isinstance(result, dict):
        return
    error = result.get("error")
    if not error:
        return
    step = f"{tool_name}:{error}" if tool_name else str(error)
    if step not in state.recovery_path:
        state.recovery_path.append(step)


async def _refresh_observation_context(
    db: AsyncSession,
    redis_client: redis.Redis,
    state: ChatState,
    agent_config: AgentConfig,
    emit_context_event: bool = True,
) -> None:
    should_log = False
    if state.message:
        should_log = not state.user_message_logged or state.last_user_message != state.message

    history_coro = history.load_history(
        db,
        redis_client,
        state.session_id,
        settings.CHAT_HISTORY_LIMIT,
        state.message if not should_log else None,
    )
    memory_coro = memory.search_memories(db, state.user_id, state.message or "", redis_client=redis_client)
    state.history, memories = await asyncio.gather(history_coro, memory_coro)

    if state.message and should_log:
        logger.info(
            "user_message session_id=%s message=%s",
            state.session_id,
            state.message,
        )
        await history.save_user_message(
            db,
            redis_client,
            state.session_id,
            state.message,
        )
        state.user_message_logged = True
        state.last_user_message = state.message
    state.history, summary = await history.maybe_compress_history(
        redis_client,
        state.provider or settings.LLM_PROVIDER,
        state.session_id,
        state.history,
    )
    state.turn_index = _count_user_turns(state.history) + (1 if state.message else 0)
    if agent_config.intent_resolver:
        state.intent = agent_config.intent_resolver(state) or "unknown"
    elif not state.intent:
        state.intent = "unknown"

    state.context = _build_observation_context(
        state,
        agent_config,
        summary,
        memories,
    )
    if emit_context_event:
        logger.info(
            "context_snapshot session_id=%s keys=%s",
            state.session_id,
            sorted(state.context.keys()),
        )
        state.events.append({"event": "context", "data": state.context})


def _fallback_final() -> dict[str, Any]:
    return FinalAnswer(
        recommendations=[
            {
                "type": "note",
                "title": "抱歉，我暂时没能完成这个请求。",
                "reason": "fallback",
            }
        ],
        followups=["可以换个说法试试吗？", "可以补充一点你的具体需求吗？"],
        warnings=[],
    ).model_dump()


def _best_effort_final_from_observations(state: ChatState, agent_config: AgentConfig) -> dict[str, Any]:
    if agent_config.best_effort_fallback_handler:
        try:
            business_fallback = agent_config.best_effort_fallback_handler(state)
        except Exception:
            business_fallback = None
        if isinstance(business_fallback, dict):
            return business_fallback
    return _fallback_final()


def _build_result_preview(
    agent_config: AgentConfig,
    tool_name: str | None,
    result: Any,
) -> Any:
    if tool_name and agent_config.tool_result_previewer:
        customized = agent_config.tool_result_previewer(tool_name, result)
        if customized is not None:
            return customized
    return preview_result(result)


async def _ensure_chat_session(db: AsyncSession, state: ChatState) -> None:
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


async def _log_plan_event(
    db: AsyncSession,
    state: ChatState,
    event: str,
    detail: dict[str, Any],
) -> None:
    payload = {
        "event": event,
        "detail": detail,
        "trace_id": state.trace_id,
        "session_id": state.session_id,
    }
    msg = ChatMessage(
        id=str(uuid4()),
        session_id=state.session_id,
        role="tool",
        tool_name="planner",
        tool_payload_json=payload,
    )
    db.add(msg)
    await db.commit()
