from __future__ import annotations

import contextvars
from typing import Any
from uuid import uuid4

import redis.asyncio as redis
from langchain_core.messages import AIMessage
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.llm_adapters import OpenAIPlanner
from app.agent.schemas import FinalAction, FinalAnswer
from app.agent.tools_registry import list_tools, to_langchain_tools
from app.common.config import settings

_OFFICIAL_TOOL_RUNTIME_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "official_tool_runtime_context",
    default={},
)


def build_legacy_official_non_smart_eats_graph(
    db: AsyncSession,
    redis_client: redis.Redis,
    provider: str | None,
    agent_config: Any,
) -> StateGraph:
    # Import legacy helper functions from dedicated helper module.
    from app.agent.legacy_builder_helpers import (
        _apply_official_tool_postprocess,
        _fallback_final,
        _finalize_official_after_tools,
        _official_is_final,
        _refresh_observation_context,
        _state_from_dict,
        _state_to_dict,
        _build_submit_final_answer_tool,
        _ensure_chat_session,
    )

    planner = OpenAIPlanner(provider=provider)
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
                output.update(_state_to_dict(chat_state))
                output["next_action"] = "final"
                return output

        final_action = planner.final_action_from_text(content)
        final = final_action.answer
        chat_state.final_json = final.model_dump() if isinstance(final, FinalAnswer) else final
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
    return graph
