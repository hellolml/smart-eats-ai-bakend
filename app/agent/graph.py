from __future__ import annotations

import asyncio
import logging
import time
import inspect
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
from app.agent.llm_adapters import OpenAIPlanner, OpenAIWriter
from app.agent.factory import build_agent_graph
from app.agent.agent_registry import AgentConfig, get_agent_config
from app.agent.schemas import FinalAction, FinalAnswer, ToolAction
from app.agent.state import ChatState
from app.agent.tools_registry import get_tool, list_tools, preview_result
from app.common.config import settings
from app.common.errors import LLM_UPSTREAM_ERROR, envelope
from app.domain.context.service import ContextService
from app.infra.models.chat import ChatMessage, ChatSession

logger = logging.getLogger("agent")


def _build_context(state: ChatState) -> dict[str, Any]:
    if state.snapshot:
        return state.snapshot
    return {
        "user": {"nickname": None, "goal": None, "current_state": None},
        "preferences": {"tastes": [], "avoid": [], "allergens": []},
        "fridge": {"top_items": []},
        "environment": {"location": None},
        "ui_scene": state.scene or "chat",
    }


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


def _validate_args(args_schema: dict[str, Any], args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    if args_schema.get("type") != "object":
        return False, {"planner_error": "invalid_schema"}

    properties = args_schema.get("properties", {})
    required = args_schema.get("required", [])
    missing = [key for key in required if key not in args]
    if missing:
        return False, {
            "planner_error": "invalid_args",
            "missing": missing,
            "allowed_properties": list(properties.keys()),
            "received_args": args,
            "expected_schema": args_schema,
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
                "expected_schema": args_schema,
            }
        if expected == "number" and not isinstance(value, (int, float)):
            return False, {
                "planner_error": "invalid_args",
                "field": key,
                "expected": expected,
                "received": type(value).__name__,
                "allowed_properties": list(properties.keys()),
                "expected_schema": args_schema,
            }
        if expected == "integer" and not isinstance(value, int):
            return False, {
                "planner_error": "invalid_args",
                "field": key,
                "expected": expected,
                "received": type(value).__name__,
                "allowed_properties": list(properties.keys()),
                "expected_schema": args_schema,
            }
        if expected == "boolean" and not isinstance(value, bool):
            return False, {
                "planner_error": "invalid_args",
                "field": key,
                "expected": expected,
                "received": type(value).__name__,
                "allowed_properties": list(properties.keys()),
                "expected_schema": args_schema,
            }

    return True, {}


def build_langgraph(
    db: AsyncSession,
    redis_client: redis.Redis,
    provider: str | None = None,
    agent_config: AgentConfig | None = None,
) -> StateGraph:
    planner = OpenAIPlanner(provider=provider)
    agent_config = agent_config or get_agent_config(None)
    allowed_tools = agent_config.tool_names

    async def observe_node(state: ChatState) -> ChatState:
        if state.snapshot is None:
            state.snapshot = await ContextService.build(
                db=db,
                redis_client=redis_client,
                user_id=state.user_id,
                scene=state.scene,
                session_id=state.session_id,
                overrides=state.context_overrides,
                force_refresh=bool(state.context_overrides),
            )
            state.context = _build_context(state)
            state.steps_left = agent_config.max_steps
            await _ensure_chat_session(db, state)
            await _log_user_message(db, state)
            state.events.append({"event": "context", "data": state.context})

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
        system = agent_config.system_prompt_builder(
            {
                "context": state.context,
                "tools": list_tools(allowed_tools),
                "observations": state.observations,
            }
        )
        user = state.message or ""
        if user.strip() in {"hi", "hello", "你好", "您好", "嗨", "hey"}:
            state.final_json = FinalAnswer(
                recommendations=[
                    {
                        "type": "note",
                        "title": "你好！想吃点什么？",
                        "reason": "你可以说说口味、预算或就餐方式。",
                    }
                ],
                followups=["更想在家做还是出去吃？", "有没有忌口或过敏？"],
                warnings=[],
            ).model_dump()
            state.action_type = "final"
            return state

        try:
            action = await asyncio.wait_for(
                planner.plan(system, user, action_normalizer=agent_config.action_normalizer),
                timeout=20,
            )
        except Exception as exc:
            state.observations.append({"planner_error": "planner_exception", "detail": str(exc)})
            state.final_json = _fallback_final()
            state.action_type = "final"
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

        if isinstance(action, FinalAction) or getattr(action, "type", None) == "final":
            final = action.answer
            state.final_json = final.model_dump() if isinstance(final, FinalAnswer) else final
            state.action_type = "final"
            await _log_plan_event(
                db,
                state,
                "plan_final",
                {"summary": "planner returned final"},
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
            return state

        tool_name = action.name
        args = action.args
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
            if state.planner_retry_count >= 2:
                state.final_json = _fallback_final()
                state.action_type = "final"
            else:
                state.action_type = "retry"
            return state

        valid, error_obs = _validate_args(tool.args_schema, args)
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
            if state.planner_retry_count >= 2:
                state.final_json = _fallback_final()
                state.action_type = "final"
            else:
                state.action_type = "retry"
            return state

        state.planner_retry_count = 0
        state.action_type = "tool"
        state.action = action
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
        return state

    async def act_node(state: ChatState) -> ChatState:
        action = state.action
        if not isinstance(action, ToolAction):
            state.final_json = _fallback_final()
            state.action_type = "final"
            return state

        tool_name = action.name
        args = action.args
        tool = get_tool(tool_name, allowed_tools)
        if not tool:
            state.final_json = _fallback_final()
            state.action_type = "final"
            return state

        tool_args = dict(args)
        tool_args["redis_client"] = redis_client
        tool_args["db"] = db
        tool_args["user_id"] = state.user_id
        start = time.perf_counter()
        try:
            result = await asyncio.wait_for(tool.func(tool_args), timeout=15)
        except Exception as exc:
            result = {"error": str(exc)}
        latency_ms = int((time.perf_counter() - start) * 1000)

        state.tool_calls.append({"name": tool_name, "args": args, "latency_ms": latency_ms})
        state.observations.append({"tool": tool_name, "result": result})
        state.steps_left -= 1
        logger.info(
            "tool_call session_id=%s trace_id=%s tool=%s latency_ms=%s",
            state.session_id,
            state.trace_id,
            tool_name,
            latency_ms,
        )
        await _log_tool_call(db, state, tool_name, args, latency_ms, result)

        state.events.append(
            {
                "event": "tool_call",
                "data": {
                    "name": tool_name,
                    "args": args,
                    "latency_ms": latency_ms,
                    "result_preview": preview_result(result),
                },
            }
        )

        if agent_config.tool_result_handler:
            handled = agent_config.tool_result_handler(state, tool_name, result)
            if handled:
                state.final_json = handled
                state.action_type = "final"
                return state

        if state.steps_left <= 0:
            state.final_json = _fallback_final()
            state.action_type = "final"
        else:
            state.action_type = "plan"
        return state

    graph = StateGraph(ChatState)
    graph.add_node("observe", observe_node)
    graph.add_node("think", think_node)
    graph.add_node("act", act_node)

    def _think_route(state: ChatState) -> str:
        if state.action_type == "tool":
            return "act"
        if state.action_type == "retry":
            return "think"
        return "observe"

    graph.add_conditional_edges("think", _think_route)

    def _act_route(state: ChatState) -> str:
        if state.action_type == "final":
            return "observe"
        return "observe"

    graph.add_conditional_edges("act", _act_route)
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
    try:
        async with checkpointer_context() as checkpointer:
            graph = build_agent_graph(
                db=db,
                redis_client=redis_client,
                agent_config=agent_config,
                provider=provider,
            ).compile(checkpointer=checkpointer)
            latest_state = state
            config = {"configurable": {"thread_id": state.session_id}}
            if state.checkpoint_ref:
                config["configurable"]["checkpoint_id"] = state.checkpoint_ref

            if state.resume_from_checkpoint and checkpointer and Command:
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
                snapshot = None
                if hasattr(graph, "aget_state"):
                    snapshot = await graph.aget_state(config)
                else:
                    snapshot = graph.get_state(config)
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

        final_json = (
            latest_state.final_json
            if hasattr(latest_state, "final_json")
            else latest_state.get("final_json")
        )
        if not final_json:
            final_json = _fallback_final()
            if hasattr(latest_state, "final_json"):
                latest_state.final_json = final_json
            else:
                latest_state["final_json"] = final_json

        writer_prompt = agent_config.writer_prompt_builder(final_json)
        assistant_chunks: list[str] = []
        async for delta in writer.stream("You are a helpful assistant.", writer_prompt):
            if await request.is_disconnected():
                return
            if await redis_client.get(cancel_key):
                yield {"event": "final", "data": {"stopped": True}}
                return
            assistant_chunks.append(delta)
            yield {"event": "delta", "data": {"token": delta}}

        if isinstance(latest_state, ChatState):
            await _log_assistant_message(db, latest_state, "".join(assistant_chunks))
        else:
            await _log_assistant_message(db, ChatState(**latest_state), "".join(assistant_chunks))
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


async def _log_user_message(db: AsyncSession, state: ChatState) -> None:
    if not state.message:
        return
    result = await db.execute(select(ChatSession).where(ChatSession.id == state.session_id))
    session = result.scalar_one_or_none()
    if session and (not session.title or session.title == "新会话"):
        title = state.message.strip().replace("\n", " ")
        session.title = title[:24] if len(title) > 24 else title
    msg = ChatMessage(
        id=str(uuid4()),
        session_id=state.session_id,
        role="user",
        content=state.message,
    )
    db.add(msg)
    await db.commit()


async def _log_tool_call(
    db: AsyncSession,
    state: ChatState,
    tool_name: str,
    args: dict[str, Any],
    latency_ms: int,
    result: Any,
) -> None:
    payload = {
        "args": args,
        "latency_ms": latency_ms,
        "result_preview": preview_result(result),
    }
    msg = ChatMessage(
        id=str(uuid4()),
        session_id=state.session_id,
        role="tool",
        tool_name=tool_name,
        tool_payload_json=payload,
    )
    db.add(msg)
    await db.commit()


async def _log_assistant_message(db: AsyncSession, state: ChatState, content: str) -> None:
    payload = {"answer": state.final_json}
    msg = ChatMessage(
        id=str(uuid4()),
        session_id=state.session_id,
        role="assistant",
        content=content,
        tool_payload_json=payload,
    )
    db.add(msg)
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
