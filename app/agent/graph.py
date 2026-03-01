from __future__ import annotations

import asyncio
import json
import logging
import re
import inspect
from typing import Any, AsyncGenerator

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
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.checkpoint import checkpointer_context
from app.agent.llm_adapters import OpenAIWriter, set_llm_log_context, reset_llm_log_context
from app.agent.agents.smart_eats import build_smart_eats_graph
from app.agent.factory import build_agent_graph
from app.agent.agent_registry import AgentConfig, get_agent_config
from app.agent.schemas import FinalAnswer
from app.agent.state import ChatState
from app.common.config import settings
from app.common.errors import LLM_UPSTREAM_ERROR, envelope
from app.agent import history
from app.agent.legacy_builder_helpers import (
    _ensure_chat_session,
    _fallback_final,
    _record_metric,
    _refresh_observation_context,
    _state_from_dict,
)

logger = logging.getLogger("agent")


def _iter_delta_chunks(text: str, chunk_size: int = 4) -> list[str]:
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


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


def _build_fast_path_system_prompt(state: ChatState, agent_config: AgentConfig) -> str:
    system_prompt: str | None = None
    if agent_config.fast_path_system_prompt_builder:
        system_prompt = agent_config.fast_path_system_prompt_builder(state)
    if not system_prompt and state.context and state.context.get("system_prompt"):
        system_prompt = state.context["system_prompt"]
    if not system_prompt:
        system_prompt = "You are a helpful assistant. Reply with plain natural language only."
    return system_prompt


def _build_fast_path_writer_prompt(state: ChatState, agent_config: AgentConfig) -> str:
    if agent_config.fast_path_writer_prompt_builder:
        return agent_config.fast_path_writer_prompt_builder(state)
    return state.message or ""


async def _stream_fast_path_answer(
    request: Request,
    db: AsyncSession,
    redis_client: redis.Redis,
    state: ChatState,
    writer: OpenAIWriter,
    cancel_key: str,
    system_prompt: str,
    writer_prompt: str,
) -> AsyncGenerator[dict[str, Any], None]:
    logger.info(
        "fast_path_enter session_id=%s message=%s",
        state.session_id, state.message,
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


# NOTE: legacy monolith builder.
# New/modern agent runtime should use dedicated per-agent graphs.
def build_langgraph(
    db: AsyncSession,
    redis_client: redis.Redis,
    provider: str | None = None,
    agent_config: AgentConfig | None = None,
) -> StateGraph:
    logger.info("agent_graph_runtime mode=legacy phase=monolith")
    from app.agent.legacy_monolith_builder import build_legacy_monolith_graph

    return build_legacy_monolith_graph(
        db=db,
        redis_client=redis_client,
        provider=provider,
        agent_config=agent_config or get_agent_config(None),
    )


# NOTE: legacy official ToolNode builder for non-smart_eats agents.
# smart_eats uses dedicated graph in app.agent.agents.smart_eats.
def build_langgraph_official(
    db: AsyncSession,
    redis_client: redis.Redis,
    provider: str | None = None,
    agent_config: AgentConfig | None = None,
) -> StateGraph:
    # legacy official builder: keep for non-smart_eats agents only.
    # smart_eats should run via dedicated graph and bypass registry/config.
    if agent_config is None or getattr(agent_config, "name", None) == "smart_eats":
        from app.agent.agents.smart_eats import build_smart_eats_graph

        logger.info("agent_graph_runtime mode=official phase=delegated agent=smart_eats")
        return build_smart_eats_graph(
            db=db,
            redis_client=redis_client,
            provider=provider,
        )

    logger.info("agent_graph_runtime mode=official phase=legacy_non_smart_eats")
    from app.agent.legacy_official_builder import build_legacy_official_non_smart_eats_graph

    return build_legacy_official_non_smart_eats_graph(
        db=db,
        redis_client=redis_client,
        provider=provider,
        agent_config=agent_config,
    )


def _should_use_dedicated_smart_eats_runtime(state: ChatState) -> bool:
    # smart_eats now defaults to dedicated graph-first runtime (independent of legacy/official toggle).
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
                system_prompt = _build_fast_path_system_prompt(state, agent_config)
                writer_prompt = _build_fast_path_writer_prompt(state, agent_config)
                async for event in _stream_fast_path_answer(
                    request=request,
                    db=db,
                    redis_client=redis_client,
                    state=state,
                    writer=writer,
                    cancel_key=cancel_key,
                    system_prompt=system_prompt,
                    writer_prompt=writer_prompt,
                ):
                    yield event
                return

        # ---- 完整 Agent 流程 ----
        async with checkpointer_context() as checkpointer:
            runtime_path = "dedicated_smart_eats" if use_dedicated_smart_eats else "legacy_dispatch"
            logger.info(
                "agent_runtime_dispatch session_id=%s trace_id=%s runtime_path=%s agent_type=%s",
                state.session_id,
                trace_id,
                runtime_path,
                state.agent_type or "smart_eats",
            )
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
