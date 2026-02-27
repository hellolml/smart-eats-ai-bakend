from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import re
import time
import inspect
import threading
from collections import Counter
from uuid import uuid4
from typing import Any, AsyncGenerator, Callable

import redis.asyncio as redis
from fastapi import Request
from langchain_core.messages import AIMessage
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
try:
    from langgraph.types import interrupt, Command
except ImportError:
    interrupt = None
    Command = None
try:
    from langgraph.errors import GraphInterrupt
except ImportError:
    GraphInterrupt = None
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.checkpoint import checkpointer_context
from app.agent.llm_adapters import (
    OpenAIPlanner,
    OpenAIWriter,
    set_llm_log_context,
    reset_llm_log_context,
)
from app.agent.agents.smart_eats import build_smart_eats_graph
from app.agent.factory import build_agent_graph
from app.agent.agent_registry import AgentConfig, get_agent_config
from app.agent.schemas import FinalAction, FinalAnswer, FinalAnswerArgs, ToolAction, ToolCallsAction
from app.agent.state import ChatState
from app.agent.tools_registry import get_tool, list_tools, preview_result, to_langchain_tools
from app.agent.tool_executor import ToolExecutor
from app.common.config import settings
from app.common.errors import LLM_UPSTREAM_ERROR, envelope
from app.agent import history, memory
from app.infra.models.chat import ChatMessage, ChatSession

logger = logging.getLogger("agent")
MAX_SAME_TOOL_CALLS_PER_TURN = 2

_METRIC_COUNTER: Counter[str] = Counter()
_METRIC_LOCK = threading.Lock()
_OFFICIAL_TOOL_RUNTIME_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "official_tool_runtime_context",
    default={},
)
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
                chat_state.action_type = "final"
            continue

        result_preview = _build_result_preview(agent_config, tool_name, result)
        chat_state.tool_calls.append({"name": tool_name, "args": args, "latency_ms": 0})
        chat_state.observations.append({"tool": tool_name, "result": result})
        _observe_recovery(chat_state, tool_name, result)

        if agent_config.tool_result_handler:
            handled = agent_config.tool_result_handler(chat_state, tool_name, result)
            if handled:
                chat_state.final_json = handled
                chat_state.action_type = "final"

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
        chat_state.action_type = "final"
    chat_state.pending_tool_calls = []


def _official_is_final(state: dict[str, Any]) -> bool:
    return bool(state.get("next_action") == "final" or state.get("final_json"))


def get_agent_metrics_snapshot() -> dict[str, int]:
    with _METRIC_LOCK:
        return dict(_METRIC_COUNTER)


def reset_agent_metrics() -> None:
    with _METRIC_LOCK:
        _METRIC_COUNTER.clear()


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
    # 通过回调注入业务层上下文
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


def _iter_delta_chunks(text: str, chunk_size: int = 4) -> list[str]:
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


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

    # 并行执行独立的 IO 操作：加载历史 + 搜索记忆
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
    # 框架层只负责分发，不包含任何具体业务语义
    if agent_config.best_effort_fallback_handler:
        try:
            business_fallback = agent_config.best_effort_fallback_handler(state)
        except Exception:
            business_fallback = None
        if isinstance(business_fallback, dict):
            return business_fallback
    return _fallback_final()

def _is_fallback_payload(final_json: dict[str, Any]) -> bool:
    recs = final_json.get("recommendations") if isinstance(final_json, dict) else None
    if not isinstance(recs, list) or not recs:
        return False
    for item in recs:
        if isinstance(item, dict) and str(item.get("reason") or "") == "fallback":
            return True
    return False


def _render_final_text(final_json: dict[str, Any]) -> str:
    recommendations = final_json.get("recommendations")
    followups = final_json.get("followups")
    warnings = final_json.get("warnings")

    rec_lines: list[str] = []
    if isinstance(recommendations, list):
        for item in recommendations:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    rec_lines.append(text)
                continue
            if isinstance(item, dict):
                title = str(item.get("title") or "").strip()
                reason = str(item.get("reason") or "").strip()
                if title and reason:
                    rec_lines.append(f"{title}（{reason}）")
                elif title:
                    rec_lines.append(title)

    follow_lines: list[str] = []
    if isinstance(followups, list):
        follow_lines = [str(item).strip() for item in followups if str(item).strip()]

    warning_lines: list[str] = []
    if isinstance(warnings, list):
        warning_lines = [str(item).strip() for item in warnings if str(item).strip()]

    chunks: list[str] = []
    if rec_lines:
        chunks.append("\n".join([f"- {line}" for line in rec_lines]))
    if follow_lines:
        chunks.append("**你可以继续：**\n" + "\n".join([f"- {line}" for line in follow_lines]))
    if warning_lines:
        chunks.append("**注意：**\n" + "\n".join([f"- {line}" for line in warning_lines]))

    text = "\n\n".join(chunks).strip()
    if text:
        return text
    return "好的。"


def _strip_structured_wrapper(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""

    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", raw, flags=re.IGNORECASE)
    if fenced:
        raw = fenced.group(1).strip()

    try:
        payload = json.loads(raw)
    except Exception:
        return text.strip()

    if isinstance(payload, dict):
        for key in ("answer", "final", "output", "content", "message", "text"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return text.strip()


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




def build_langgraph(
    db: AsyncSession,
    redis_client: redis.Redis,
    provider: str | None = None,
    agent_config: AgentConfig | None = None,
) -> StateGraph:
    planner = OpenAIPlanner(provider=provider)
    agent_config = agent_config or get_agent_config(None)
    allowed_tools = agent_config.tool_names
    available_tool_schemas = list_tools(allowed_tools)
    tool_executor = ToolExecutor(
        allowed_tools,
        redis_client,
        db,
        max_workers=6,
        args_normalizer=agent_config.tool_args_normalizer,
        serial_execution_decider=agent_config.serial_execution_decider,
    )

    async def observe_node(state: ChatState) -> ChatState:
        first_round = state.steps_left <= 0 and not state.tool_calls and not state.observations
        if first_round:
            state.steps_left = agent_config.max_steps
        await _ensure_chat_session(db, state)
        await _refresh_observation_context(
            db,
            redis_client,
            state,
            agent_config,
            emit_context_event=first_round,
        )
        logger.info(
            "agent_observe session_id=%s history_count=%s observations_count=%s steps_left=%s intent=%s location_source=%s recovery_path=%s",
            state.session_id,
            len(state.history),
            len(state.observations),
            state.steps_left,
            state.intent,
            state.location_source,
            state.recovery_path,
        )

        pause_key = f"chat:pause:{state.session_id}"
        if await redis_client.get(pause_key):
            await redis_client.delete(pause_key)
            if interrupt:
                resume_payload = interrupt(
                    {
                        "reason": "manual_pause",
                        "session_id": state.session_id,
                    }
                )
                if isinstance(resume_payload, dict):
                    state.message = resume_payload.get("message") or state.message
                    if resume_payload.get("context_overrides"):
                        state.context_overrides = resume_payload.get("context_overrides")
            else:
                state.final_json = _fallback_final()
                state.action_type = "final"
        if not state.final_json and state.action_type == "final":
            state.final_json = _fallback_final()
        return state

    async def think_node(state: ChatState) -> ChatState:
        routed_calls = agent_config.tool_plan_router(state) if agent_config.tool_plan_router else None
        if routed_calls:
            state.planner_retry_count = 0
            state.action_type = "tool_calls"
            state.pending_tool_calls = routed_calls
            state.tool_plan = routed_calls
            state.events.append(
                {
                    "event": "plan_step",
                    "data": {"type": "tool_calls", "calls": routed_calls, "source": "intent_router"},
                }
            )
            logger.info(
                "agent_decision session_id=%s action_type=tool_calls source=intent_router intent=%s tool_plan=%s",
                state.session_id,
                state.intent,
                routed_calls,
            )
            return state

        # LLM-owned intent classification: run once per user turn (or until we get non-unknown intent).
        if state.message and (not state.intent or state.intent == "unknown"):
            try:
                decision = await planner.classify_intent(state.message, state.context)
                state.intent = decision.intent
                state.intent_confidence = decision.confidence
                state.intent_slots = dict(decision.slots)
                state.intent_need_clarify = decision.need_clarify
                state.intent_clarify_question = decision.clarify_question
                logger.info(
                    "intent_decision session_id=%s intent=%s confidence=%s need_clarify=%s",
                    state.session_id,
                    state.intent,
                    state.intent_confidence,
                    state.intent_need_clarify,
                )
                state.events.append(
                    {
                        "event": "intent_decision",
                        "data": {
                            "intent": state.intent,
                            "confidence": state.intent_confidence,
                            "need_clarify": state.intent_need_clarify,
                        },
                    }
                )
                _record_metric(
                    state,
                    "intent_decision",
                    intent=state.intent,
                    need_clarify=state.intent_need_clarify,
                )
                if state.intent_need_clarify:
                    _record_metric(state, "clarify_triggered", intent=state.intent)
            except Exception as exc:
                logger.info("intent_decision_fallback session_id=%s reason=%s", state.session_id, str(exc))

        if state.intent_need_clarify and state.intent_confidence < 0.6:
            question = state.intent_clarify_question or "可以再具体描述下你的需求吗？"
            state.final_json = FinalAnswer(
                recommendations=[
                    {
                        "type": "note",
                        "title": question,
                        "reason": "我先确认下你的需求，再给你更准的建议。",
                    }
                ],
                followups=[],
                warnings=[],
            ).model_dump()
            state.action_type = "final"
            _record_metric(state, "clarify_final", intent=state.intent)
            return state

        system = None
        if state.context:
            system = state.context.get("system_prompt")
        if not system:
            system = agent_config.system_prompt_builder({"context": state.context})
        user = state.message or ""
        state.step_index = agent_config.max_steps - state.steps_left + 1
        token = set_llm_log_context(
            {
                "session_id": state.session_id,
                "turn": state.turn_index,
                "step": state.step_index,
            }
        )
        try:
            action = await asyncio.wait_for(
                planner.plan(
                    system,
                    user,
                    available_tool_schemas,
                    action_normalizer=agent_config.action_normalizer,
                ),
                timeout=35,
            )
        except Exception as exc:
            state.planner_retry_count += 1
            state.observations.append({"planner_error": "planner_exception", "detail": str(exc)})
            state.events.append(
                {
                    "event": "plan_exception",
                    "data": {"detail": str(exc), "retry_count": state.planner_retry_count},
                }
            )
            await _log_plan_event(
                db,
                state,
                "plan_exception",
                {"detail": str(exc), "retry_count": state.planner_retry_count},
            )
            if state.planner_retry_count >= 2:
                state.final_json = _best_effort_final_from_observations(state, agent_config)
                state.action_type = "final"
                logger.info(
                    "agent_decision session_id=%s action_type=final reason=plan_exception_retry_exhausted detail=%s",
                    state.session_id,
                    str(exc),
                )
            else:
                state.action_type = "retry"
                logger.info(
                    "agent_decision session_id=%s action_type=retry reason=plan_exception detail=%s",
                    state.session_id,
                    str(exc),
                )
            return state
        finally:
            reset_llm_log_context(token)

        if isinstance(action, FinalAction) or getattr(action, "type", None) == "final":
            final = action.answer
            state.final_json = final.model_dump() if isinstance(final, FinalAnswer) else final
            state.action_type = "final"
            if agent_config.final_action_hook:
                await agent_config.final_action_hook(state, state.final_json, db)
            await _log_plan_event(
                db,
                state,
                "plan_final",
                {"summary": "planner returned final"},
            )
            logger.info(
                "agent_decision session_id=%s action_type=final",
                state.session_id,
            )
            return state

        async def _retry_invalid_tool_calls(errors: list[dict[str, Any]]) -> ChatState:
            state.planner_retry_count += 1
            state.observations.append(
                {
                    "planner_error": "invalid_tool_calls",
                    "errors": errors,
                }
            )
            state.events.append(
                {
                    "event": "retry",
                    "data": {"reason": "invalid_tool_calls", "detail": errors},
                }
            )
            await _log_plan_event(
                db,
                state,
                "plan_invalid_tool_calls",
                {"errors": errors},
            )
            logger.info(
                "agent_decision session_id=%s action_type=invalid_tool_calls errors=%s",
                state.session_id,
                errors,
            )
            if state.planner_retry_count >= 2:
                state.final_json = _best_effort_final_from_observations(state, agent_config)
                state.action_type = "final"
            else:
                state.action_type = "retry"
            return state

        if isinstance(action, ToolAction):
            action = ToolCallsAction(calls=[{action.name: action.args}])

        if isinstance(action, ToolCallsAction) or getattr(action, "type", None) == "tool_calls":
            try:
                raw_calls = action.calls if isinstance(action, ToolCallsAction) else getattr(action, "calls", [])
                if not isinstance(raw_calls, list) or not raw_calls:
                    raise ValueError("empty_tool_calls")

                normalized_calls: list[dict[str, Any]] = []
                for call in raw_calls:
                    if not isinstance(call, dict) or len(call) != 1:
                        raise ValueError(f"invalid_call_format:{call}")
                    tool_name, args = next(iter(call.items()))
                    if not isinstance(tool_name, str) or not isinstance(args, dict):
                        raise TypeError(f"invalid_call_types:{call}")

                    tool = get_tool(tool_name, allowed_tools)
                    if not tool:
                        raise ValueError(f"unknown_tool:{tool_name}")

                    normalized_calls.append(
                        {
                            "name": tool_name,
                            "args": tool_executor.normalize_args(tool_name, args),
                        }
                    )
            except Exception as exc:
                return await _retry_invalid_tool_calls(
                    [{"reason": "parse_error", "detail": str(exc)}]
                )

            repeated_limit_errors: list[dict[str, Any]] = []
            for call in normalized_calls:
                tool_name = call.get("name")
                if not isinstance(tool_name, str):
                    continue
                existing = sum(
                    1
                    for obs in state.observations
                    if isinstance(obs, dict) and obs.get("tool") == tool_name
                )
                if existing >= MAX_SAME_TOOL_CALLS_PER_TURN:
                    repeated_limit_errors.append(
                        {
                            "reason": "max_same_tool_calls_per_turn",
                            "tool": tool_name,
                            "limit": MAX_SAME_TOOL_CALLS_PER_TURN,
                            "existing": existing,
                        }
                    )

            if repeated_limit_errors:
                return await _retry_invalid_tool_calls(repeated_limit_errors)

            state.planner_retry_count = 0
            state.action_type = "tool_calls"
            state.action = action
            state.pending_tool_calls = normalized_calls
            state.tool_plan = normalized_calls
            state.events.append(
                {
                    "event": "plan_step",
                    "data": {"type": "tool_calls", "calls": normalized_calls},
                }
            )
            await _log_plan_event(
                db,
                state,
                "plan_tool_calls",
                {"calls": normalized_calls},
            )
            logger.info(
                "agent_decision session_id=%s action_type=tool_calls tools=%s intent=%s tool_plan=%s",
                state.session_id,
                [item["name"] for item in normalized_calls],
                state.intent,
                normalized_calls,
            )
            return state

        state.observations.append({"planner_error": "invalid_action"})
        state.final_json = _best_effort_final_from_observations(state, agent_config)
        state.action_type = "final"
        await _log_plan_event(
            db,
            state,
            "plan_invalid_action",
            {"action_type": str(getattr(action, "type", None))},
        )
        logger.info(
            "agent_decision session_id=%s action_type=invalid",
            state.session_id,
        )
        return state

    async def validate_node(state: ChatState) -> ChatState:
        """执行前校验层：不做语义判定，只做结构/预算护栏。"""
        if state.action_type not in {"tool", "tool_calls"}:
            return state

        if state.action_type == "tool_calls":
            if not state.pending_tool_calls:
                state.planner_retry_count += 1
                state.observations.append({"planner_error": "empty_tool_calls_after_plan"})
                state.action_type = "retry" if state.planner_retry_count < 2 else "final"
                if state.action_type == "final":
                    state.final_json = _best_effort_final_from_observations(state, agent_config)
                return state
            if len(state.pending_tool_calls) > 4:
                state.pending_tool_calls = state.pending_tool_calls[:4]
                state.events.append({
                    "event": "plan_guardrail",
                    "data": {"reason": "trim_tool_calls", "max": 4},
                })
            return state

        if state.action_type == "tool" and not isinstance(state.action, ToolAction):
            state.planner_retry_count += 1
            state.observations.append({"planner_error": "invalid_tool_action_after_plan"})
            state.action_type = "retry" if state.planner_retry_count < 2 else "final"
            if state.action_type == "final":
                state.final_json = _best_effort_final_from_observations(state, agent_config)
        return state

    async def act_node(state: ChatState) -> ChatState:
        if state.action_type == "tool_calls":
            if not state.pending_tool_calls:
                state.final_json = _fallback_final()
                state.action_type = "final"
                return state
            results = await tool_executor.execute_calls(
                state.pending_tool_calls,
                state,
                servers_path=settings.MCP_SERVERS_CONFIG_PATH,
            )
            for item in results:
                tool_name = item.get("name")
                args = item.get("args") or {}
                latency_ms = item.get("latency_ms") or 0
                result = item.get("result")
                result_preview = _build_result_preview(agent_config, tool_name, result)
                state.tool_calls.append({"name": tool_name, "args": args, "latency_ms": latency_ms})
                state.observations.append({"tool": tool_name, "result": result})
                _observe_recovery(state, tool_name, result)
                # 通过回调处理工具结果（业务层逻辑）
                if agent_config.tool_result_handler:
                    agent_config.tool_result_handler(state, tool_name, result)
                logger.info(
                    "tool_call session_id=%s trace_id=%s tool=%s latency_ms=%s",
                    state.session_id,
                    state.trace_id,
                    tool_name,
                    latency_ms,
                )
                logger.info(
                    "tool_result session_id=%s tool=%s result_preview=%s",
                    state.session_id,
                    tool_name,
                    result_preview,
                )
                await history.save_tool_message(
                    db,
                    redis_client,
                    state.session_id,
                    tool_name,
                    {
                        "args": args,
                        "latency_ms": latency_ms,
                        "result": result,
                        "result_preview": result_preview,
                    },
                )
                state.events.append(
                    {
                        "event": "tool_call",
                        "data": {
                            "name": tool_name,
                            "args": args,
                            "latency_ms": latency_ms,
                            "result_preview": result_preview,
                        },
                    }
                )

            state.steps_left -= 1
            state.tool_results_batch = results
            state.pending_tool_calls = []
            state.action_type = "merge"
            logger.info(
                "agent_decision session_id=%s action_type=merge tools=%s",
                state.session_id,
                [item.get("name") for item in results],
            )
            return state

        action = state.action
        if not isinstance(action, ToolAction):
            state.final_json = _fallback_final()
            state.action_type = "final"
            return state

        tool_name = action.name
        args = action.args
        results = await tool_executor.execute_calls(
            [{"name": tool_name, "args": args}],
            state,
            servers_path=settings.MCP_SERVERS_CONFIG_PATH,
        )
        result_item = results[0] if results else {"name": tool_name, "args": args, "result": {}}
        latency_ms = result_item.get("latency_ms") or 0
        result = result_item.get("result")
        args = result_item.get("args") or args

        result_preview = _build_result_preview(agent_config, tool_name, result)
        state.tool_calls.append({"name": tool_name, "args": args, "latency_ms": latency_ms})
        state.observations.append({"tool": tool_name, "result": result})
        _observe_recovery(state, tool_name, result)
        state.steps_left -= 1
        logger.info(
            "tool_call session_id=%s trace_id=%s tool=%s latency_ms=%s",
            state.session_id,
            state.trace_id,
            tool_name,
            latency_ms,
        )
        logger.info(
            "tool_result session_id=%s tool=%s result_preview=%s",
            state.session_id,
            tool_name,
            result_preview,
        )
        await history.save_tool_message(
            db,
            redis_client,
            state.session_id,
            tool_name,
            {
                "args": args,
                "latency_ms": latency_ms,
                "result": result,
                "result_preview": result_preview,
            },
        )

        state.events.append(
            {
                "event": "tool_call",
                "data": {
                    "name": tool_name,
                    "args": args,
                    "latency_ms": latency_ms,
                    "result_preview": result_preview,
                },
            }
        )

        if agent_config.tool_result_handler:
            handled = agent_config.tool_result_handler(state, tool_name, result)
            if handled:
                state.final_json = handled
                state.action_type = "final"
                logger.info(
                    "agent_decision session_id=%s action_type=final tool=%s",
                    state.session_id,
                    tool_name,
                )
                return state

        if state.steps_left <= 0:
            state.final_json = _best_effort_final_from_observations(state, agent_config)
            state.action_type = "final"
            logger.info(
                "agent_decision session_id=%s action_type=final reason=steps_exhausted_best_effort",
                state.session_id,
            )
        else:
            state.action_type = "plan"
            logger.info(
                "agent_decision session_id=%s action_type=plan",
                state.session_id,
            )
        return state

    async def merge_node(state: ChatState) -> ChatState:
        if not state.tool_results_batch:
            state.action_type = "plan"
            return state
        if agent_config.tool_result_handler:
            for item in state.tool_results_batch:
                tool_name = item.get("name")
                result = item.get("result")
                handled = agent_config.tool_result_handler(state, tool_name, result)
                if handled:
                    state.final_json = handled
                    state.action_type = "final"
                    logger.info(
                        "agent_decision session_id=%s action_type=final tool=%s",
                        state.session_id,
                        tool_name,
                    )
                    state.tool_results_batch = []
                    return state
        state.tool_results_batch = []
        if state.steps_left <= 0:
            state.final_json = _best_effort_final_from_observations(state, agent_config)
            state.action_type = "final"
            logger.info(
                "agent_decision session_id=%s action_type=final reason=steps_exhausted_best_effort",
                state.session_id,
            )
        else:
            state.action_type = "plan"
            logger.info(
                "agent_decision session_id=%s action_type=plan",
                state.session_id,
            )
        return state

    graph = StateGraph(ChatState)
    graph.add_node("observe", observe_node)
    graph.add_node("think", think_node)
    graph.add_node("validate", validate_node)
    graph.add_node("act", act_node)
    graph.add_node("merge", merge_node)

    def _think_route(state: ChatState) -> str:
        if state.action_type in {"tool", "tool_calls"}:
            return "validate"
        if state.action_type == "retry":
            return "think"
        return "observe"

    graph.add_conditional_edges("think", _think_route)

    def _validate_route(state: ChatState) -> str:
        if state.action_type in {"tool", "tool_calls"}:
            return "act"
        if state.action_type == "retry":
            return "think"
        return "observe"

    graph.add_conditional_edges("validate", _validate_route)

    def _act_route(state: ChatState) -> str:
        if state.action_type == "merge":
            return "merge"
        if state.action_type == "final":
            return "observe"
        return "observe"

    graph.add_conditional_edges("act", _act_route)
    graph.add_conditional_edges("merge", lambda state: "observe")
    graph.add_conditional_edges(
        "observe",
        lambda state: END if state.action_type == "final" else "think",
    )
    graph.set_entry_point("observe")
    return graph


def build_langgraph_official(
    db: AsyncSession,
    redis_client: redis.Redis,
    provider: str | None = None,
    agent_config: AgentConfig | None = None,
) -> StateGraph:
    resolved_agent_config = agent_config or get_agent_config(None)
    if getattr(resolved_agent_config, "name", None) == "smart_eats":
        from app.agent.agents.smart_eats import build_smart_eats_graph

        logger.info("agent_graph_runtime mode=official phase=delegated agent=smart_eats")
        return build_smart_eats_graph(
            db=db,
            redis_client=redis_client,
            provider=provider,
        )

    planner = OpenAIPlanner(provider=provider)
    agent_config = resolved_agent_config
    allowed_tools = agent_config.tool_names
    available_tool_schemas = list_tools(allowed_tools)

    tool_node = ToolNode(
        [
            *to_langchain_tools(
                allowlist=allowed_tools,
                runtime_context_factory=lambda: _OFFICIAL_TOOL_RUNTIME_CONTEXT.get(),
            ),
            _build_submit_final_answer_tool(),
        ],
        messages_key="messages",
    )

    async def observe_node(state: dict[str, Any]) -> dict[str, Any]:
        chat_state = _state_from_dict(state)
        first_round = (
            chat_state.steps_left <= 0
            and not chat_state.tool_calls
            and not chat_state.observations
        )
        if first_round:
            chat_state.steps_left = agent_config.max_steps
        await _ensure_chat_session(db, chat_state)
        await _refresh_observation_context(
            db,
            redis_client,
            chat_state,
            agent_config,
            emit_context_event=first_round,
        )

        output = dict(state)
        output.update(_state_to_dict(chat_state))
        if chat_state.final_json:
            output["next_action"] = "final"
            return output
        output["next_action"] = "think"
        return output

    async def think_node(state: dict[str, Any]) -> dict[str, Any]:
        chat_state = _state_from_dict(state)

        if chat_state.intent_need_clarify and chat_state.intent_confidence < 0.6:
            question = chat_state.intent_clarify_question or "可以再具体描述下你的需求吗？"
            chat_state.final_json = FinalAnswer(
                recommendations=[
                    {
                        "type": "note",
                        "title": question,
                        "reason": "我先确认下你的需求，再给你更准的建议。",
                    }
                ],
                followups=[],
                warnings=[],
            ).model_dump()
            chat_state.action_type = "final"
            output = dict(state)
            output.update(_state_to_dict(chat_state))
            output["next_action"] = "final"
            return output

        system = None
        if chat_state.context:
            system = chat_state.context.get("system_prompt")
        if not system:
            system = agent_config.system_prompt_builder({"context": chat_state.context})
        user = chat_state.message or ""

        decision = await planner.plan_tool_calls(system, user, available_tool_schemas)
        output = dict(state)

        raw_content = decision.get("content") if isinstance(decision, dict) else ""
        normalized_tool_calls = decision.get("tool_calls") if isinstance(decision, dict) else []

        if isinstance(normalized_tool_calls, list) and normalized_tool_calls:
            tool_calls: list[dict[str, Any]] = []
            for index, call in enumerate(normalized_tool_calls):
                tool_name = call.get("name") if isinstance(call, dict) else None
                args = call.get("args") if isinstance(call, dict) else None
                call_id = call.get("id") if isinstance(call, dict) else None
                if not isinstance(tool_name, str) or not isinstance(args, dict):
                    continue
                normalized_args = args
                if tool_name in allowed_tools and agent_config.tool_args_normalizer:
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
                output.update(_state_to_dict(chat_state))
                output["messages"] = [AIMessage(content="", tool_calls=tool_calls)]
                output["next_action"] = tools_condition(output, messages_key="messages")
                return output

        content = raw_content if isinstance(raw_content, str) else ""
        if content and agent_config.action_normalizer:
            mapped = agent_config.action_normalizer(content)
            if isinstance(mapped, FinalAction):
                final = mapped.answer
                chat_state.final_json = final.model_dump() if isinstance(final, FinalAnswer) else final
                chat_state.action_type = "final"
                output.update(_state_to_dict(chat_state))
                output["next_action"] = "final"
                return output

        final_action = planner.final_action_from_text(content)
        final = final_action.answer
        chat_state.final_json = final.model_dump() if isinstance(final, FinalAnswer) else final
        chat_state.action_type = "final"
        output.update(_state_to_dict(chat_state))
        output["next_action"] = "final"
        return output

    async def tools_node(state: dict[str, Any]) -> dict[str, Any]:
        chat_state = _state_from_dict(state)
        ai_messages = state.get("messages") if isinstance(state.get("messages"), list) else []
        if not ai_messages:
            output = dict(state)
            output.update(_state_to_dict(chat_state))
            output["next_action"] = "think"
            return output

        runtime_payload = {
            "redis_client": redis_client,
            "db": db,
            "user_id": chat_state.user_id,
            "context": chat_state.context,
            "session_id": chat_state.session_id,
            "client_ip": chat_state.client_ip,
            "last_user_message": chat_state.last_user_message or chat_state.message,
            "servers_path": settings.MCP_SERVERS_CONFIG_PATH,
        }
        token = _OFFICIAL_TOOL_RUNTIME_CONTEXT.set(runtime_payload)
        try:
            tool_output = await tool_node.ainvoke({"messages": ai_messages})
        finally:
            _OFFICIAL_TOOL_RUNTIME_CONTEXT.reset(token)

        tool_messages = tool_output.get("messages") if isinstance(tool_output, dict) else []
        if not isinstance(tool_messages, list):
            tool_messages = []
        latest_ai_message = ai_messages[-1] if ai_messages else None
        call_args_map: dict[str, dict[str, Any]] = {}
        if isinstance(latest_ai_message, AIMessage):
            for call in latest_ai_message.tool_calls or []:
                call_id = call.get("id")
                args = call.get("args")
                if isinstance(call_id, str) and isinstance(args, dict):
                    call_args_map[call_id] = args

        output = dict(state)
        output.update(_state_to_dict(chat_state))
        output["_tool_messages"] = tool_messages
        output["_tool_call_args"] = call_args_map
        output["next_action"] = "tool_postprocess"
        return output

    async def tool_postprocess_node(state: dict[str, Any]) -> dict[str, Any]:
        chat_state = _state_from_dict(state)
        tool_messages = state.get("_tool_messages") if isinstance(state.get("_tool_messages"), list) else []
        call_args_map = state.get("_tool_call_args") if isinstance(state.get("_tool_call_args"), dict) else {}

        await _apply_official_tool_postprocess(
            chat_state,
            tool_messages=tool_messages,
            call_args_map=call_args_map,
            db=db,
            redis_client=redis_client,
            agent_config=agent_config,
        )
        _finalize_official_after_tools(chat_state, agent_config)

        output = dict(state)
        output.update(_state_to_dict(chat_state))
        output.pop("_tool_messages", None)
        output.pop("_tool_call_args", None)
        output["messages"] = []
        output["next_action"] = "final" if chat_state.final_json else "observe"
        return output

    async def finalize_node(state: dict[str, Any]) -> dict[str, Any]:
        chat_state = _state_from_dict(state)
        if not chat_state.final_json:
            chat_state.final_json = _fallback_final()
        chat_state.action_type = "final"
        output = dict(state)
        output.update(_state_to_dict(chat_state))
        output["next_action"] = "final"
        return output

    graph = StateGraph(dict)
    graph.add_node("observe", observe_node)
    graph.add_node("think", think_node)
    graph.add_node("tools", tools_node)
    graph.add_node("tool_postprocess", tool_postprocess_node)
    graph.add_node("finalize", finalize_node)

    graph.add_conditional_edges(
        "observe",
        lambda state: END if _official_is_final(state) else "think",
    )
    graph.add_conditional_edges(
        "think",
        lambda state: "tools" if state.get("next_action") == "tools" else "finalize",
    )
    graph.add_conditional_edges(
        "tools",
        lambda state: "tool_postprocess" if state.get("next_action") == "tool_postprocess" else "observe",
    )
    graph.add_conditional_edges(
        "tool_postprocess",
        lambda state: "finalize" if _official_is_final(state) else "observe",
    )
    graph.add_edge("finalize", END)
    graph.set_entry_point("observe")
    logger.info("agent_graph_runtime mode=official phase=toolnode")
    return graph


def _should_use_dedicated_smart_eats_runtime(state: ChatState) -> bool:
    runtime = (settings.AGENT_GRAPH_RUNTIME or "legacy").strip().lower()
    if runtime != "official":
        return False
    return (state.agent_type or "smart_eats") == "smart_eats"


async def run_chat_stream(
    request: Request,
    db: AsyncSession,
    redis_client: redis.Redis,
    state: ChatState,
) -> AsyncGenerator[dict[str, Any], None]:
    provider = state.provider or settings.LLM_PROVIDER
    writer = OpenAIWriter(provider=provider)
    cancel_key = f"chat:cancel:{state.session_id}"
    use_dedicated_smart_eats = _should_use_dedicated_smart_eats_runtime(state)
    agent_config = None if use_dedicated_smart_eats else get_agent_config(state.agent_type)
    if agent_config and state.scene == "chat" and agent_config.scene != "chat":
        state.scene = agent_config.scene

    trace_id = state.trace_id or ""
    history_cache = history.create_history_cache()
    history.set_current_cache(history_cache)
    try:
        # ---- 快速通道：仅 legacy/config-based agent 使用 ----
        if agent_config and agent_config.fast_path_decider and agent_config.fast_path_decider(state):
            await _ensure_chat_session(db, state)
            await _refresh_observation_context(
                db, redis_client, state, agent_config, emit_context_event=False,
            )
            # intent_resolver 可能将 intent 修正为需要工具的类型
            if state.intent not in ("chat", "unknown"):
                logger.info(
                    "fast_path_rejected session_id=%s intent=%s",
                    state.session_id, state.intent,
                )
            else:
                logger.info(
                    "fast_path_enter session_id=%s message=%s",
                    state.session_id, state.message,
                )
                system_prompt: str | None = None
                if agent_config.fast_path_system_prompt_builder:
                    system_prompt = agent_config.fast_path_system_prompt_builder(state)
                if not system_prompt and state.context and state.context.get("system_prompt"):
                    system_prompt = state.context["system_prompt"]
                if not system_prompt:
                    system_prompt = "You are a helpful assistant. Reply with plain natural language only."
                writer_prompt = (
                    agent_config.fast_path_writer_prompt_builder(state)
                    if agent_config.fast_path_writer_prompt_builder
                    else (state.message or "")
                )
                token = set_llm_log_context(
                    {
                        "session_id": state.session_id,
                        "turn": state.turn_index,
                        "step": "fast_path",
                    }
                )
                assistant_chunks: list[str] = []
                try:
                    async for delta in writer.stream(system_prompt, writer_prompt):
                        if await request.is_disconnected():
                            return
                        if await redis_client.get(cancel_key):
                            yield {"event": "final", "data": {"stopped": True}}
                            return
                        assistant_chunks.append(delta)
                finally:
                    reset_llm_log_context(token)

                answer_text = _strip_structured_wrapper("".join(assistant_chunks))
                for chunk in _iter_delta_chunks(answer_text):
                    if await request.is_disconnected():
                        return
                    if await redis_client.get(cancel_key):
                        yield {"event": "final", "data": {"stopped": True}}
                        return
                    yield {"event": "delta", "data": {"token": chunk}}
                    await asyncio.sleep(0)
                fast_final = FinalAnswer(
                    recommendations=[],
                    followups=[],
                    warnings=[],
                ).model_dump()
                await history.save_assistant_message(
                    db, redis_client, state.session_id, answer_text, fast_final,
                )
                yield {"event": "final", "data": {"stopped": False, "answer": fast_final}}
                return

        # ---- 完整 Agent 流程 ----
        async with checkpointer_context() as checkpointer:
            if use_dedicated_smart_eats:
                graph = build_smart_eats_graph(
                    db=db,
                    redis_client=redis_client,
                    provider=provider,
                ).compile(checkpointer=checkpointer)
            else:
                graph = build_agent_graph(
                    db=db,
                    redis_client=redis_client,
                    agent_config=agent_config,
                    provider=provider,
                ).compile(checkpointer=checkpointer)
            latest_state = state
            last_final_json: dict[str, Any] | None = None
            config = {"configurable": {"thread_id": state.session_id}}
            if state.checkpoint_ref:
                config["configurable"]["checkpoint_id"] = state.checkpoint_ref

            user_message = (state.message or "").strip()
            snapshot = None
            if checkpointer:
                if hasattr(graph, "aget_state"):
                    snapshot = await graph.aget_state(config)
                else:
                    snapshot = graph.get_state(config)
            has_pending = bool(snapshot and getattr(snapshot, "next", None))

            if has_pending and checkpointer:
                resume_payload: dict[str, Any] = {}
                if user_message:
                    resume_payload["message"] = user_message
                if state.context_overrides:
                    resume_payload["context_overrides"] = state.context_overrides
                if Command and resume_payload:
                    input_payload = Command(resume=resume_payload)
                else:
                    input_payload = None
                logger.info(
                    "agent_auto_resume session_id=%s trace_id=%s",
                    state.session_id,
                    trace_id,
                )
            elif state.resume_from_checkpoint and checkpointer and Command:
                resume_payload = state.resume_payload or {}
                if state.message:
                    resume_payload.setdefault("message", state.message)
                if state.context_overrides:
                    resume_payload.setdefault("context_overrides", state.context_overrides)
                if resume_payload:
                    input_payload = Command(resume=resume_payload)
                else:
                    input_payload = None
            elif state.replay_from_checkpoint:
                input_payload = None
            else:
                input_payload = state.__dict__

            if input_payload is None:
                if not snapshot or not getattr(snapshot, "values", None):
                    input_payload = state.__dict__

            async def _stream_graph() -> AsyncGenerator[Any, None]:
                supports_durability = False
                try:
                    params = inspect.signature(graph.astream).parameters
                    supports_durability = "durability" in params
                except (TypeError, ValueError):
                    supports_durability = False

                if supports_durability:
                    async for item in graph.astream(
                        input_payload,
                        stream_mode="values",
                        config=config,
                        durability=settings.LANGGRAPH_DURABILITY,
                    ):
                        yield item
                    return

                async for item in graph.astream(
                    input_payload,
                    stream_mode="values",
                    config=config,
                ):
                    yield item

            # 立即发送 "thinking" 事件，前端可以马上显示思考动画
            yield {"event": "thinking", "data": {"status": "start"}}

            async for updated in _stream_graph():
                if await request.is_disconnected():
                    return
                if await redis_client.get(cancel_key):
                    yield {"event": "final", "data": {"stopped": True}}
                    return
                latest_state = updated
                if isinstance(updated, ChatState):
                    if updated.final_json:
                        last_final_json = updated.final_json
                elif isinstance(updated, dict):
                    if updated.get("final_json"):
                        last_final_json = updated.get("final_json")
                events = updated.events if hasattr(updated, "events") else updated.get("events", [])
                for item in events:
                    yield item
                if hasattr(updated, "events"):
                    updated.events.clear()
                else:
                    updated["events"] = []

        final_json = (
            latest_state.final_json
            if hasattr(latest_state, "final_json")
            else latest_state.get("final_json")
        )
        if not final_json:
            final_json = last_final_json
        if not final_json:
            final_json = _fallback_final()
            if hasattr(latest_state, "final_json"):
                latest_state.final_json = final_json
            else:
                latest_state["final_json"] = final_json

        metric_state = latest_state if isinstance(latest_state, ChatState) else state
        if _is_fallback_payload(final_json):
            _record_metric(metric_state, "fallback_final")
        else:
            _record_metric(metric_state, "non_fallback_final")

        # 通知前端思考阶段结束，即将开始输出文字
        yield {"event": "thinking", "data": {"status": "done"}}

        answer_text = _render_final_text(final_json)
        if await request.is_disconnected():
            return
        if await redis_client.get(cancel_key):
            yield {"event": "final", "data": {"stopped": True}}
            return

        yield {"event": "delta", "data": {"token": answer_text}}
        await asyncio.sleep(0)

        if isinstance(latest_state, ChatState):
            await history.save_assistant_message(
                db,
                redis_client,
                latest_state.session_id,
                answer_text,
                latest_state.final_json,
            )
        else:
            temp_state = _state_from_dict(latest_state)
            await history.save_assistant_message(
                db,
                redis_client,
                temp_state.session_id,
                answer_text,
                temp_state.final_json,
            )
        yield {"event": "final", "data": {"stopped": False, "answer": final_json}}
    except Exception as exc:
        if GraphInterrupt and isinstance(exc, GraphInterrupt):
            yield {"event": "paused", "data": {"reason": "manual_pause"}}
            return
        logger.exception(
            "agent_stream_error session_id=%s trace_id=%s error=%s",
            state.session_id,
            trace_id,
            str(exc),
        )
        yield {
            "event": "error",
            "data": envelope(None, trace_id, code=LLM_UPSTREAM_ERROR, message=str(exc)),
        }
        return
    finally:
        if history_cache:
            history_cache.close()
        history.clear_current_cache()


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
