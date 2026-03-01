from __future__ import annotations

from typing import Any

import redis.asyncio as redis
from langgraph.graph import END, StateGraph
try:
    from langgraph.types import interrupt
except ImportError:
    interrupt = None
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.llm_adapters import OpenAIPlanner, reset_llm_log_context, set_llm_log_context
from app.agent.schemas import FinalAction, FinalAnswer, ToolAction, ToolCallsAction
from app.agent.tools_registry import get_tool, list_tools
from app.agent.tool_executor import ToolExecutor
from app.common.config import settings

MAX_SAME_TOOL_CALLS_PER_TURN = 2


def build_legacy_monolith_graph(
    db: AsyncSession,
    redis_client: redis.Redis,
    provider: str | None,
    agent_config: Any,
) -> StateGraph:
    """Legacy monolith graph builder extracted from graph.py.

    Keep behavior stable while shrinking graph.py into orchestration layer.
    """
    from app.agent.legacy_builder_helpers import (
        _best_effort_final_from_observations,
        _build_result_preview,
        _ensure_chat_session,
        _fallback_final,
        _log_plan_event,
        _observe_recovery,
        _record_metric,
        _refresh_observation_context,
        logger,
    )

    planner = OpenAIPlanner(provider=provider)
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

    async def observe_node(state: Any) -> Any:
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

    async def think_node(state: Any) -> Any:
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
            import asyncio

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

        async def _retry_invalid_tool_calls(errors: list[dict[str, Any]]) -> Any:
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

    async def validate_node(state: Any) -> Any:
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

    async def act_node(state: Any) -> Any:
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
                from app.agent import history

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
        from app.agent import history

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

    async def merge_node(state: Any) -> Any:
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

    from app.agent.state import LegacyChatState

    graph = StateGraph(LegacyChatState)
    graph.add_node("observe", observe_node)
    graph.add_node("think", think_node)
    graph.add_node("validate", validate_node)
    graph.add_node("act", act_node)
    graph.add_node("merge", merge_node)

    def _think_route(state: Any) -> str:
        if state.action_type in {"tool", "tool_calls"}:
            return "validate"
        if state.action_type == "retry":
            return "think"
        return "observe"

    graph.add_conditional_edges("think", _think_route)

    def _validate_route(state: Any) -> str:
        if state.action_type in {"tool", "tool_calls"}:
            return "act"
        if state.action_type == "retry":
            return "think"
        return "observe"

    graph.add_conditional_edges("validate", _validate_route)

    def _act_route(state: Any) -> str:
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
