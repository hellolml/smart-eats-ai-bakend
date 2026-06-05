from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, AsyncGenerator

import redis.asyncio as redis
from fastapi import Request
try:
    from langgraph.types import Command
except ImportError:
    Command = None
try:
    from langgraph.errors import GraphInterrupt
except ImportError:
    GraphInterrupt = None
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.checkpoint import checkpointer_context
from app.agent.langgraph_store import langgraph_store_context
from app.agent.runtime.graph import (
    AgentRuntimeState,
    _initialize_graph_state,
    _state_from_dict,
)
from app.agent.runtime.finalization import fallback_final
from app.agent.metrics import record_agent_metric
from app.agent.state import ChatState
from app.common.config import settings
from app.common.errors import LLM_UPSTREAM_ERROR, envelope
from app.agent import conversation
from app.domain.preferences.service import apply_extracted_preferences, extract_preferences_from_text

logger = logging.getLogger("agent")


def _normalize_llm_upstream_error_message(exc: Exception) -> str:
    body = getattr(exc, "body", None)
    error_type = ""
    error_message = ""

    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            error_type = str(error.get("type") or error.get("code") or "").strip()
            error_message = str(error.get("message") or "").strip()

    raw_message = str(exc).strip()
    combined = " ".join(part for part in (error_type, error_message, raw_message) if part)
    combined_lower = combined.lower()

    if error_type == "AllocationQuota.FreeTierOnly" or "free tier" in combined_lower:
        return "当前模型免费额度已用尽，请在模型管理控制台关闭“仅使用免费额度”模式，或切换到可用模型后重试。"

    if "coding_plan_subscription_expired" in combined_lower or "subscription is expired" in combined_lower:
        return "当前模型订阅已过期，请在模型管理中切换到可用模型，或更新后端 LLM_PROVIDER / 模型配置后重试。"

    if "request timed out" in combined_lower or "timed out" in combined_lower:
        return "模型响应超时，请稍后重试；如果旅行规划较复杂，请减少一次输入的信息量，或在后端调大 LLM_PLANNER_REQUEST_TIMEOUT_SECONDS。"

    if "unexpected item type in content" in combined_lower or "messages input is invalid" in combined_lower:
        return "模型未接受本次图片输入，请确认当前模型支持多模态图片，或重新上传图片后再试。"

    if error_message:
        return error_message
    return raw_message or "LLM 上游服务暂时不可用，请稍后重试。"


def _is_fallback_payload(final_json: dict[str, Any]) -> bool:
    recs = final_json.get("recommendations") if isinstance(final_json, dict) else None
    if not isinstance(recs, list) or not recs:
        return False
    for item in recs:
        if isinstance(item, dict) and str(item.get("reason") or "") == "fallback":
            return True
    return False


def _with_agent_metadata(final_json: dict[str, Any], state: AgentRuntimeState) -> dict[str, Any]:
    if not isinstance(final_json, dict):
        return final_json
    agent_id = state.agent_id
    plan_type = state.plan_type
    scene = state.scene
    context_overrides = state.context_overrides
    if isinstance(context_overrides, dict):
        agent_id = agent_id or context_overrides.get("agent_id")
        plan_type = plan_type or context_overrides.get("plan_type")
    if scene == "travel_planner":
        agent_id = agent_id or "travel_plan"
        plan_type = plan_type or "travel"
    if not agent_id and not plan_type:
        return final_json
    enriched = dict(final_json)
    if agent_id:
        enriched.setdefault("agent_id", agent_id)
    if plan_type:
        enriched.setdefault("plan_type", plan_type)
    return enriched


def _render_final_text(final_json: dict[str, Any]) -> str:
    raw_text = final_json.get("raw_text")
    if isinstance(raw_text, str) and raw_text.strip():
        return raw_text.strip()

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


async def _apply_turn_preference_extraction(
    db: AsyncSession,
    *,
    user_id: str | None,
    user_message: str | None,
) -> None:
    if not user_id:
        return
    extracted = extract_preferences_from_text(user_message)
    if not extracted:
        return
    await apply_extracted_preferences(db, user_id=user_id, extracted=extracted, allow_sensitive=False)
    await db.commit()


def _build_graph_config(state: ChatState) -> dict[str, Any]:
    config: dict[str, Any] = {
        "configurable": {"thread_id": state.session_id},
        "recursion_limit": 64,
    }
    if state.checkpoint_ref:
        config["configurable"]["checkpoint_id"] = state.checkpoint_ref
    return config


def _coerce_runtime_state(value: Any, fallback_state: ChatState) -> AgentRuntimeState:
    if isinstance(value, AgentRuntimeState):
        return value
    if isinstance(value, ChatState):
        return AgentRuntimeState.model_validate(value.model_dump())
    if isinstance(value, dict):
        payload = {**fallback_state.model_dump(), **value}
        return _state_from_dict(payload)
    return AgentRuntimeState.model_validate(fallback_state.model_dump())


async def _load_graph_snapshot(graph: Any, config: dict[str, Any], checkpointer: Any) -> Any:
    if not checkpointer:
        return None
    if hasattr(graph, "aget_state"):
        return await graph.aget_state(config)
    return graph.get_state(config)


def _resolve_graph_input(state: ChatState, snapshot: Any, checkpointer: Any) -> Any:
    has_pending = bool(snapshot and getattr(snapshot, "next", None))
    if has_pending and checkpointer:
        resume_payload: dict[str, Any] = {}
        user_message = (state.message or "").strip()
        if user_message:
            resume_payload["message"] = user_message
        if state.context_overrides:
            resume_payload["context_overrides"] = state.context_overrides
        if Command and resume_payload:
            return Command(resume=resume_payload)
        return None

    if state.resume_from_checkpoint and checkpointer:
        if snapshot and getattr(snapshot, "values", None):
            return None
        return state.__dict__

    if state.replay_from_checkpoint:
        return None

    return state.__dict__


async def run_chat_stream(
    request: Request,
    db: AsyncSession,
    redis_client: redis.Redis,
    state: ChatState,
) -> AsyncGenerator[dict[str, Any], None]:
    provider = state.provider or settings.LLM_PROVIDER
    cancel_key = f"chat:cancel:{state.session_id}"
    trace_id = state.trace_id or ""
    conversation_cache = conversation.create_conversation_cache()
    conversation.set_current_cache(conversation_cache)
    try:
        delete_cancel = getattr(redis_client, "delete", None)
        if callable(delete_cancel):
            result = delete_cancel(cancel_key)
            if inspect.isawaitable(result):
                await result
        async with checkpointer_context() as checkpointer, langgraph_store_context() as store:
            logger.info(
                "agent_runtime_dispatch session_id=%s trace_id=%s runtime_path=%s",
                state.session_id,
                trace_id,
                "supervisor_runtime",
            )
            from app.agent.supervisor import build_supervisor_runtime_graph

            if (
                state.persist_user_message
                and state.message
                and db is not None
                and hasattr(db, "execute")
                and not state.user_message_logged
            ):
                await conversation.save_user_message(
                    db,
                    redis_client,
                    state.session_id,
                    state.message,
                )
                state.user_message_logged = True
                state.last_user_message = state.message
            graph_builder = build_supervisor_runtime_graph(
                db=db,
                redis_client=redis_client,
                provider=provider,
                resolved_model_config=state.resolved_model_config,
            )
            graph = graph_builder.compile(checkpointer=checkpointer, store=store)
            latest_state = state
            config = _build_graph_config(state)
            snapshot = await _load_graph_snapshot(graph, config, checkpointer)
            has_pending = bool(snapshot and getattr(snapshot, "next", None))
            input_payload = _resolve_graph_input(state, snapshot, checkpointer)

            if has_pending:
                logger.info(
                    "agent_auto_resume session_id=%s trace_id=%s",
                    state.session_id,
                    trace_id,
                )

            if input_payload is None:
                if not snapshot or not getattr(snapshot, "values", None):
                    input_payload = state.__dict__
            if isinstance(input_payload, dict):
                input_payload = _initialize_graph_state(input_payload)

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

            yield {"event": "thinking", "data": {"status": "start"}}

            async for updated in _stream_graph():
                if await request.is_disconnected():
                    return
                if await redis_client.get(cancel_key):
                    yield {"event": "final", "data": {"stopped": True}}
                    return
                latest_state = updated
                events = updated.events if hasattr(updated, "events") else updated.get("events", [])
                for item in events:
                    yield item
                if hasattr(updated, "events"):
                    updated.events.clear()
                else:
                    updated["events"] = []

        runtime_state = _coerce_runtime_state(latest_state, state)
        final_json = runtime_state.final_json
        if not final_json:
            final_json = fallback_final()
            runtime_state.final_json = final_json
        final_json = _with_agent_metadata(final_json, runtime_state)
        runtime_state.final_json = final_json

        session_id = runtime_state.session_id or state.session_id
        if _is_fallback_payload(final_json):
            record_agent_metric(session_id, "fallback_final")
        else:
            record_agent_metric(session_id, "non_fallback_final")

        yield {"event": "thinking", "data": {"status": "done"}}

        answer_text = _render_final_text(final_json)
        if await request.is_disconnected():
            return
        if await redis_client.get(cancel_key):
            yield {"event": "final", "data": {"stopped": True}}
            return

        yield {"event": "delta", "data": {"token": answer_text}}
        await asyncio.sleep(0)

        await conversation.save_assistant_message(
            db,
            redis_client,
            runtime_state.session_id,
            answer_text,
            runtime_state.final_json,
        )
        await _apply_turn_preference_extraction(
            db,
            user_id=runtime_state.user_id,
            user_message=runtime_state.message,
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
        message = _normalize_llm_upstream_error_message(exc)
        yield {
            "event": "error",
            "data": envelope(None, trace_id, code=LLM_UPSTREAM_ERROR, message=message),
        }
        return
    finally:
        if conversation_cache:
            conversation_cache.close()
        conversation.clear_current_cache()
