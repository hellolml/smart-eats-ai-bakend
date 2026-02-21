from __future__ import annotations

import asyncio
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
from langgraph.graph import END, StateGraph
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
from app.agent.factory import build_agent_graph
from app.agent.agent_registry import AgentConfig, get_agent_config
from app.agent.schemas import FinalAction, FinalAnswer, ToolAction, ToolCallsAction
from app.agent.state import ChatState
from app.agent.tools_registry import get_tool, list_tools, preview_result
from app.agent.tool_executor import ToolExecutor
from app.common.config import settings
from app.common.errors import LLM_UPSTREAM_ERROR, envelope
from app.agent import history, memory
from app.infra.models.chat import ChatMessage, ChatSession

logger = logging.getLogger("agent")
MAX_SAME_TOOL_CALLS_PER_TURN = 2

_METRIC_COUNTER: Counter[str] = Counter()
_METRIC_LOCK = threading.Lock()


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
        followups=["可以换个说法试试吗？", "你更想在家做还是出去吃？"],
        warnings=[],
    ).model_dump()


def _best_effort_final_from_observations(state: ChatState) -> dict[str, Any]:
    # 优先消费已有可用结果，尽量避免 steps_exhausted 直接 fallback
    if isinstance(state.context, dict) and state.context.get("fridge_items") == []:
        return FinalAnswer(
            recommendations=[
                {
                    "type": "note",
                    "title": "看起来你冰箱现在是空的，我先给你几道不用复杂食材也能做的快手菜。",
                    "reason": "已获取到空冰箱状态",
                }
            ],
            followups=["要不要我按 10 分钟内完成给你 3 道菜？", "你也可以告诉我想吃什么，我直接给详细做法。"],
            warnings=[],
        ).model_dump()

    last_search_list: list[dict[str, Any]] | None = None
    last_error: str | None = None
    for item in reversed(state.observations):
        if not isinstance(item, dict):
            continue
        tool_name = item.get("tool")
        result = item.get("result")
        if tool_name == "search_restaurants":
            if isinstance(result, list):
                last_search_list = result
                break
            if isinstance(result, dict) and isinstance(result.get("error"), str):
                last_error = result.get("error")
        if tool_name in {"get_ip_location", "geocode_location"} and isinstance(result, dict):
            if isinstance(result.get("error"), str):
                last_error = result.get("error")

    if isinstance(last_search_list, list) and last_search_list:
        top = []
        for row in last_search_list[:3]:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            addr = ""
            raw = row.get("raw")
            if isinstance(raw, dict):
                addr = str(raw.get("address") or "").strip()
            if name:
                top.append(f"{name}（{addr}）" if addr else name)
        if top:
            return FinalAnswer(
                recommendations=[
                    {"type": "note", "title": "我先给你整理了附近可选店", "reason": "基于已拿到的检索结果"}
                ],
                followups=[f"你可以先看这几家：{'；'.join(top)}", "要不要我再按口味/预算帮你筛一轮？"],
                warnings=[],
            ).model_dump()

    if last_error in {"missing_location", "missing_ip"}:
        return FinalAnswer(
            recommendations=[
                {"type": "note", "title": "我还缺少精确位置，暂时没法稳妥推荐附近餐厅。", "reason": "位置信息不足"}
            ],
            followups=["你可以发我当前城市或地标", "或者开启定位后再试一次"],
            warnings=[],
        ).model_dump()

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


def _validate_args(
    input_schema: dict[str, Any],
    args: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    if input_schema.get("type") != "object":
        return False, {"planner_error": "invalid_schema"}

    properties = input_schema.get("properties", {})
    required = input_schema.get("required", [])
    missing = [key for key in required if key not in args]
    if missing:
        return False, {
            "planner_error": "invalid_args",
            "missing": missing,
            "allowed_properties": list(properties.keys()),
            "received_args": args,
            "expected_schema": input_schema,
        }

    for key, value in args.items():
        if key not in properties:
            continue
        expected = properties[key].get("type")
        if expected == "string" and not isinstance(value, str):
            return False, {
                "planner_error": "invalid_args",
                "field": key,
                "expected": expected,
                "received": type(value).__name__,
                "allowed_properties": list(properties.keys()),
                "expected_schema": input_schema,
            }
        if expected == "number" and not isinstance(value, (int, float)):
            return False, {
                "planner_error": "invalid_args",
                "field": key,
                "expected": expected,
                "received": type(value).__name__,
                "allowed_properties": list(properties.keys()),
                "expected_schema": input_schema,
            }
        if expected == "integer" and not isinstance(value, int):
            return False, {
                "planner_error": "invalid_args",
                "field": key,
                "expected": expected,
                "received": type(value).__name__,
                "allowed_properties": list(properties.keys()),
                "expected_schema": input_schema,
            }
        if expected == "boolean" and not isinstance(value, bool):
            return False, {
                "planner_error": "invalid_args",
                "field": key,
                "expected": expected,
                "received": type(value).__name__,
                "allowed_properties": list(properties.keys()),
                "expected_schema": input_schema,
            }

    return True, {}


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
            question = state.intent_clarify_question or "你是想出去吃，还是在家做饭？"
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
                planner.plan(system, user, action_normalizer=agent_config.action_normalizer),
                timeout=20,
            )
        except Exception as exc:
            state.observations.append({"planner_error": "planner_exception", "detail": str(exc)})
            state.final_json = _fallback_final()
            state.action_type = "final"
            logger.info(
                "agent_decision session_id=%s action_type=final reason=plan_exception detail=%s",
                state.session_id,
                str(exc),
            )
            state.events.append(
                {
                    "event": "plan_exception",
                    "data": {"detail": str(exc)},
                }
            )
            await _log_plan_event(
                db,
                state,
                "plan_exception",
                {"detail": str(exc)},
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

        if isinstance(action, ToolCallsAction) or getattr(action, "type", None) == "tool_calls":
            calls = action.calls if isinstance(action, ToolCallsAction) else getattr(action, "calls", [])
            if not isinstance(calls, list) or not calls:
                state.observations.append({"planner_error": "invalid_tool_calls", "detail": "empty"})
                state.final_json = _fallback_final()
                state.action_type = "final"
                await _log_plan_event(
                    db,
                    state,
                    "plan_invalid_tool_calls",
                    {"detail": "empty"},
                )
                logger.info(
                    "agent_decision session_id=%s action_type=invalid_tool_calls",
                    state.session_id,
                )
                return state
            normalized_calls: list[dict[str, Any]] = []
            errors: list[dict[str, Any]] = []
            for call in calls:
                if not isinstance(call, dict) or len(call) != 1:
                    errors.append({"reason": "invalid_format", "call": call})
                    continue
                tool_name, args = next(iter(call.items()))
                if not isinstance(tool_name, str) or not isinstance(args, dict):
                    errors.append({"reason": "invalid_format", "call": call})
                    continue
                args = tool_executor.normalize_args(tool_name, args)
                tool = get_tool(tool_name, allowed_tools)
                if not tool:
                    errors.append({"reason": "unknown_tool", "tool": tool_name})
                    continue
                valid, error_obs = _validate_args(tool.input_schema, args)
                if not valid:
                    error_obs["tool"] = tool_name
                    errors.append(error_obs)
                    continue
                normalized_calls.append({"name": tool_name, "args": args})

            # 防抖：同一轮次同一工具调用次数过多，直接拦截
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
                errors.extend(repeated_limit_errors)

            # eat_out 场景：如果已拿到餐厅检索结果，禁止再反复 geocode，直接给用户可用结果
            if (
                state.intent == "eat_out"
                and normalized_calls
                and all(isinstance(item, dict) and item.get("name") == "geocode_location" for item in normalized_calls)
                and any(
                    isinstance(obs, dict)
                    and obs.get("tool") == "search_restaurants"
                    and isinstance(obs.get("result"), list)
                    and len(obs.get("result") or []) > 0
                    for obs in state.observations
                )
            ):
                state.final_json = _best_effort_final_from_observations(state)
                state.action_type = "final"
                logger.info(
                    "agent_decision session_id=%s action_type=final reason=redundant_geocode_after_search",
                    state.session_id,
                )
                return state

            if errors:
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
                    "agent_decision session_id=%s action_type=invalid_tool_calls",
                    state.session_id,
                )
                if state.planner_retry_count >= 2:
                    # 对常见死循环做业务兜底，避免直接 fallback
                    repeated_tools = {
                        str(item.get("tool"))
                        for item in errors
                        if isinstance(item, dict) and item.get("reason") == "max_same_tool_calls_per_turn"
                    }
                    if "get_fridge_items" in repeated_tools and isinstance(state.context, dict) and state.context.get("fridge_items") == []:
                        state.final_json = _best_effort_final_from_observations(state)
                    else:
                        state.final_json = _fallback_final()
                    state.action_type = "final"
                else:
                    state.action_type = "retry"
                return state

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

        if not isinstance(action, ToolAction):
            state.observations.append({"planner_error": "invalid_action"})
            state.final_json = _fallback_final()
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

        tool_name = action.name
        args = tool_executor.normalize_args(action.name, action.args)
        tool = get_tool(tool_name, allowed_tools)
        if not tool:
            state.planner_retry_count += 1
            state.observations.append(
                {
                    "planner_error": "unknown_tool",
                    "tool": tool_name,
                    "allowed_tools": [item["name"] for item in list_tools(allowed_tools)],
                }
            )
            state.events.append(
                {
                    "event": "retry",
                    "data": {"reason": "unknown_tool", "tool": tool_name},
                }
            )
            await _log_plan_event(
                db,
                state,
                "plan_unknown_tool",
                {"tool": tool_name},
            )
            logger.info(
                "agent_decision session_id=%s action_type=unknown_tool tool=%s",
                state.session_id,
                tool_name,
            )
            if state.planner_retry_count >= 2:
                state.final_json = _fallback_final()
                state.action_type = "final"
            else:
                state.action_type = "retry"
            return state

        valid, error_obs = _validate_args(tool.input_schema, args)
        if not valid:
            state.planner_retry_count += 1
            state.observations.append(error_obs)
            state.events.append(
                {
                    "event": "retry",
                    "data": {"reason": "invalid_args", "detail": error_obs},
                }
            )
            await _log_plan_event(
                db,
                state,
                "plan_invalid_args",
                error_obs,
            )
            logger.info(
                "agent_decision session_id=%s action_type=invalid_args tool=%s",
                state.session_id,
                tool_name,
            )
            if state.planner_retry_count >= 2:
                state.final_json = _fallback_final()
                state.action_type = "final"
            else:
                state.action_type = "retry"
            return state

        existing_tool_calls = sum(
            1
            for obs in state.observations
            if isinstance(obs, dict) and obs.get("tool") == tool_name
        )
        if existing_tool_calls >= MAX_SAME_TOOL_CALLS_PER_TURN:
            state.observations.append(
                {
                    "planner_error": "max_same_tool_calls_per_turn",
                    "tool": tool_name,
                    "limit": MAX_SAME_TOOL_CALLS_PER_TURN,
                    "existing": existing_tool_calls,
                }
            )
            state.final_json = _best_effort_final_from_observations(state)
            state.action_type = "final"
            logger.info(
                "agent_decision session_id=%s action_type=final reason=max_same_tool_calls_per_turn tool=%s",
                state.session_id,
                tool_name,
            )
            return state

        state.planner_retry_count = 0
        state.action_type = "tool"
        state.action = action
        state.tool_plan = [{"name": tool_name, "args": args}]
        state.events.append(
            {
                "event": "plan_step",
                "data": {"type": "tool", "name": tool_name, "args": args},
            }
        )
        await _log_plan_event(
            db,
            state,
            "plan_tool",
            {"tool": tool_name, "args": args},
        )
        logger.info(
            "agent_decision session_id=%s action_type=tool tool=%s args=%s intent=%s tool_plan=%s",
            state.session_id,
            tool_name,
            args,
            state.intent,
            state.tool_plan,
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
                    state.final_json = _best_effort_final_from_observations(state)
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
                state.final_json = _best_effort_final_from_observations(state)
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
            state.final_json = _best_effort_final_from_observations(state)
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
            state.final_json = _best_effort_final_from_observations(state)
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


async def run_chat_stream(
    request: Request,
    db: AsyncSession,
    redis_client: redis.Redis,
    state: ChatState,
) -> AsyncGenerator[dict[str, Any], None]:
    provider = state.provider or settings.LLM_PROVIDER
    writer = OpenAIWriter(provider=provider)
    cancel_key = f"chat:cancel:{state.session_id}"
    agent_config = get_agent_config(state.agent_type)
    if state.scene == "chat" and agent_config.scene != "chat":
        state.scene = agent_config.scene

    trace_id = state.trace_id or ""
    history_cache = history.create_history_cache()
    history.set_current_cache(history_cache)
    try:
        # ---- 快速通道：由业务 agent 的回调决定是否命中 ----
        if agent_config.fast_path_decider and agent_config.fast_path_decider(state):
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
            temp_state = ChatState(**latest_state)
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
