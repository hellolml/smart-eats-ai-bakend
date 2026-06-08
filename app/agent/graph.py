from __future__ import annotations

import asyncio
import inspect
import logging
import re
import time
from datetime import datetime, timezone
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
from app.agent.runtime.finalization import final_json_from_text
from app.agent.contracts import build_agent_run_result, final_json_for_failure, is_fallback_final
from app.agent.metrics import record_agent_metric
from app.agent.realtime_eval import (
    build_trace_from_events,
    evaluate_realtime,
    should_sample,
)
from app.agent.state import ChatState
from app.common.config import settings
from app.common.errors import LLM_UPSTREAM_ERROR, envelope
from app.agent import conversation
from app.domain.preferences.service import apply_extracted_preferences, extract_preferences_from_text

logger = logging.getLogger("agent")


# ---------------------------------------------------------------------------
# 实时评测调度
# ---------------------------------------------------------------------------

def _schedule_realtime_eval(
    *,
    session_id: str,
    user_id: str | None,
    trace_id: str | None,
    user_message: str | None,
    events: list[dict[str, Any]],
    final_json: dict[str, Any] | None,
    model_provider: str | None,
    model_name: str | None,
    started_at: datetime | None,
    ended_at: datetime | None,
    total_duration_ms: float,
) -> None:
    """在后台异步执行实时评测，不阻塞当前 SSE 流."""
    if not settings.REALTIME_EVAL_ENABLED:
        return
    if not should_sample():
        return
    if not events and not final_json:
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # 没有运行中的事件循环（不太可能在 FastAPI 中发生），放弃评测
        logger.warning("realtime_eval no_event_loop session_id=%s", session_id)
        return

    async def _do_eval() -> None:
        try:
            from app.agent.realtime_eval import evaluate_realtime

            trace = build_trace_from_events(
                session_id=session_id,
                events=events,
                final_json=final_json,
                user_message=user_message,
                total_duration_ms=total_duration_ms,
            )
            result = evaluate_realtime(trace)

            # 持久化到 DB
            await _persist_realtime_eval(
                result,
                events=events,
                final_json=final_json,
                user_id=user_id,
                trace_id=trace_id,
                model_provider=model_provider,
                model_name=model_name,
                started_at=started_at,
                ended_at=ended_at,
            )

            logger.info(
                "realtime_eval_done session_id=%s scene=%s quality=%.2f fallback=%s duration=%.0fms",
                session_id,
                result.scene,
                result.overall_quality,
                result.is_fallback,
                result.total_duration_ms,
            )
        except Exception:
            logger.warning("realtime_eval_failed session_id=%s", session_id, exc_info=True)

    loop.create_task(_do_eval())


async def _persist_realtime_eval(
    result: "RealtimeEvalResult",  # type: ignore[name-defined]
    *,
    events: list[dict[str, Any]],
    final_json: dict[str, Any] | None,
    user_id: str | None,
    trace_id: str | None,
    model_provider: str | None,
    model_name: str | None,
    started_at: datetime | None,
    ended_at: datetime | None,
) -> None:
    """将实时评测结果写入 DB."""
    try:
        from app.infra.eval_db import eval_session
        from app.infra.eval_db import init_eval_db
        from app.agent.monitoring import persist_realtime_conversation

        await init_eval_db()
        async with eval_session() as session:
            await persist_realtime_conversation(
                session,
                result=result,
                events=events,
                final_json=final_json,
                user_id=user_id,
                trace_id=trace_id,
                model_provider=model_provider,
                model_name=model_name,
                started_at=started_at,
                ended_at=ended_at,
            )
            await session.commit()
    except Exception:
        logger.warning("realtime_eval_persist_failed id=%s", result.id, exc_info=True)


def _extract_llm_error_parts(exc: Exception) -> dict[str, str | int | None]:
    body = getattr(exc, "body", None)
    error_type = ""
    error_message = ""
    error_code = ""

    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            error_type = str(error.get("type") or error.get("code") or "").strip()
            error_code = str(error.get("code") or error.get("type") or "").strip()
            error_message = str(error.get("message") or "").strip()

    raw_message = str(exc).strip()
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if not isinstance(status_code, int):
        status_code = getattr(exc, "status_code", None)
    if not isinstance(status_code, int):
        match = re.search(r"(?:error code|status code|http status)\D+(\d{3})", raw_message, flags=re.IGNORECASE)
        if match:
            status_code = int(match.group(1))
    return {
        "error_type": error_type,
        "error_code": error_code,
        "error_message": error_message,
        "raw_message": raw_message,
        "status_code": status_code if isinstance(status_code, int) else None,
    }


def _classify_llm_upstream_issue(exc: Exception) -> dict[str, Any]:
    parts = _extract_llm_error_parts(exc)
    error_type = str(parts.get("error_type") or "")
    error_code = str(parts.get("error_code") or "")
    error_message = str(parts.get("error_message") or "")
    raw_message = str(parts.get("raw_message") or "")
    status_code = parts.get("status_code")
    combined = " ".join(part for part in (error_type, error_message, raw_message) if part)
    combined_lower = combined.lower()

    if error_type == "AllocationQuota.FreeTierOnly" or "free tier" in combined_lower:
        return {
            "category": "provider_quota",
            "code": "free_tier_quota_exhausted",
            "http_status": status_code,
            "provider_error_code": error_code or error_type,
            "user_message": "当前模型免费额度已用尽，请在模型管理控制台关闭“仅使用免费额度”模式，或切换到可用模型后重试。",
            "action": "disable_free_tier_only_or_switch_model",
        }

    if "coding_plan_subscription_expired" in combined_lower or "subscription is expired" in combined_lower:
        return {
            "category": "provider_auth",
            "code": "subscription_expired",
            "http_status": status_code,
            "provider_error_code": error_code or error_type,
            "user_message": "当前模型订阅已过期，请在模型管理中切换到可用模型，或更新后端 LLM_PROVIDER / 模型配置后重试。",
            "action": "switch_model_or_refresh_provider_subscription",
        }

    if status_code == 402 or any(token in combined_lower for token in ("insufficient balance", "payment required", "billing", "余额不足")):
        return {
            "category": "provider_billing_unavailable",
            "code": "provider_billing_unavailable",
            "http_status": status_code,
            "provider_error_code": error_code or error_type,
            "user_message": "模型服务余额不足或账单不可用，请充值、更新套餐，或切换到可用模型后重试。",
            "action": "recharge_provider_or_switch_model",
        }

    if "request timed out" in combined_lower or "timed out" in combined_lower:
        return {
            "category": "provider_timeout",
            "code": "model_timeout",
            "http_status": status_code,
            "provider_error_code": error_code or error_type,
            "user_message": "模型响应超时，请稍后重试；如果旅行规划较复杂，请减少一次输入的信息量，或在后端调大 LLM_PLANNER_REQUEST_TIMEOUT_SECONDS。",
            "action": "retry_reduce_context_or_increase_timeout",
        }

    if "unexpected item type in content" in combined_lower or "messages input is invalid" in combined_lower:
        return {
            "category": "provider_schema",
            "code": "invalid_multimodal_payload",
            "http_status": status_code,
            "provider_error_code": error_code or error_type,
            "user_message": "模型未接受本次图片输入，请确认当前模型支持多模态图片，或重新上传图片后再试。",
            "action": "switch_to_vision_model_or_fix_image_payload",
        }

    if status_code in {401, 403} or any(token in combined_lower for token in ("api key", "unauthorized", "permissiondenied")):
        return {
            "category": "provider_auth",
            "code": "provider_auth_failed",
            "http_status": status_code,
            "provider_error_code": error_code or error_type,
            "user_message": error_message or raw_message or "模型服务认证失败，请检查 API Key、模型权限或 provider 配置。",
            "action": "check_api_key_model_permission_or_provider_config",
        }
    if status_code == 429 or "rate limit" in combined_lower:
        return {
            "category": "provider_rate_limit",
            "code": "provider_rate_limited",
            "http_status": status_code,
            "provider_error_code": error_code or error_type,
            "user_message": error_message or "模型服务限流，请稍后重试或切换可用模型。",
            "action": "retry_later_or_switch_model",
        }
    if error_message:
        user_message = error_message
    else:
        user_message = raw_message or "LLM 上游服务暂时不可用，请稍后重试。"
    return {
        "category": "provider_model_error",
        "code": "provider_upstream_error",
        "http_status": status_code,
        "provider_error_code": error_code or error_type,
        "user_message": user_message,
        "action": "inspect_provider_error_and_model_config",
    }


def _normalize_llm_upstream_error_message(exc: Exception) -> str:
    return str(_classify_llm_upstream_issue(exc).get("user_message") or "LLM 上游服务暂时不可用，请稍后重试。")


def _is_fallback_payload(final_json: dict[str, Any]) -> bool:
    return is_fallback_final(final_json)


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
    if not agent_id and not plan_type and not scene:
        return final_json
    enriched = dict(final_json)
    if scene:
        enriched.setdefault("scene", scene)
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


def _final_json_from_latest_ai_message(value: Any) -> dict[str, Any] | None:
    messages = getattr(value, "messages", None)
    if messages is None and isinstance(value, dict):
        messages = value.get("messages")
    if not isinstance(messages, list):
        return None

    for message in reversed(messages):
        content = getattr(message, "content", None)
        message_type = getattr(message, "type", None)
        if message_type not in (None, "ai", "assistant"):
            continue
        if isinstance(content, str) and content.strip():
            return final_json_from_text(content)
    return None


def _agent_result_from_state(value: Any) -> dict[str, Any] | None:
    result = getattr(value, "agent_result", None)
    if result is None and isinstance(value, dict):
        result = value.get("agent_result")
    return result if isinstance(result, dict) else None


def _route_decision_from_state(value: Any) -> dict[str, Any] | None:
    route = getattr(value, "route_decision", None)
    if route is None and isinstance(value, dict):
        route = value.get("route_decision")
    return route if isinstance(route, dict) else None


def _runtime_diagnostics_from_state(value: Any) -> dict[str, Any]:
    context = getattr(value, "context", None)
    if context is None and isinstance(value, dict):
        context = value.get("context")
    if not isinstance(context, dict):
        return {}

    diagnostics: dict[str, Any] = {}
    active_tools = context.get("allowed_tools")
    if isinstance(active_tools, list):
        diagnostics["active_tools"] = [item for item in active_tools if isinstance(item, str)]

    active_skills = context.get("active_skills")
    if isinstance(active_skills, list):
        diagnostics["active_skills"] = [item for item in active_skills if isinstance(item, dict)]

    skill_diagnostics = context.get("skill_diagnostics")
    if isinstance(skill_diagnostics, dict):
        diagnostics["skill_diagnostics"] = skill_diagnostics

    provider = getattr(value, "provider", None)
    if provider is None and isinstance(value, dict):
        provider = value.get("provider")
    resolved_model_config = getattr(value, "resolved_model_config", None)
    if resolved_model_config is None and isinstance(value, dict):
        resolved_model_config = value.get("resolved_model_config")
    diagnostics.update(_model_diagnostics(provider, resolved_model_config))

    return diagnostics


def _model_diagnostics(provider: Any, resolved_model_config: Any) -> dict[str, Any]:
    model_config: dict[str, Any] = {}
    if isinstance(provider, str) and provider:
        model_config["provider_value"] = provider
    if isinstance(resolved_model_config, dict):
        for key in (
            "source",
            "provider",
            "provider_value",
            "config_id",
            "display_name",
            "base_url",
            "model_planner",
            "model_writer",
            "model_vision_planner",
        ):
            value = resolved_model_config.get(key)
            if value not in (None, "", [], {}):
                model_config[key] = value
    return {"model_config": model_config} if model_config else {}


async def _load_graph_snapshot(graph: Any, config: dict[str, Any], checkpointer: Any) -> Any:
    if not checkpointer:
        return None
    if hasattr(graph, "aget_state"):
        return await graph.aget_state(config)
    return graph.get_state(config)


def _resolve_graph_input(state: ChatState, snapshot: Any, checkpointer: Any) -> Any:
    has_pending = bool(snapshot and getattr(snapshot, "next", None))
    explicit_resume = bool(state.resume_from_checkpoint or state.checkpoint_ref or state.replay_from_checkpoint)
    if has_pending and checkpointer and explicit_resume:
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
    # 收集 SSE events 副本用于实时评测（events 会在流式过程中被清空）
    collected_events: list[dict[str, Any]] = []
    stream_start_time = time.monotonic()
    started_at = datetime.now(timezone.utc)
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

            if has_pending and (state.resume_from_checkpoint or state.checkpoint_ref or state.replay_from_checkpoint):
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
                    collected_events.append(item)
                    yield item
                if hasattr(updated, "events"):
                    updated.events.clear()
                else:
                    updated["events"] = []

        runtime_state = _coerce_runtime_state(latest_state, state)
        final_json = runtime_state.final_json
        if not final_json:
            final_json = _final_json_from_latest_ai_message(latest_state) or final_json_for_failure("worker_no_final")
            runtime_state.final_json = final_json
        final_json = _with_agent_metadata(final_json, runtime_state)
        runtime_state.final_json = final_json
        agent_result = (
            runtime_state.agent_result
            if isinstance(runtime_state.agent_result, dict)
            else _agent_result_from_state(latest_state)
        )
        if not isinstance(agent_result, dict):
            agent_result = build_agent_run_result(
                final_json=final_json,
                route_decision=_route_decision_from_state(latest_state) or runtime_state.route_decision,
                worker=runtime_state.agent_id,
                trace_id=trace_id,
            )
        else:
            agent_result = dict(agent_result)
            agent_result.setdefault("trace_id", trace_id)
            agent_result.setdefault("final", final_json)
        runtime_state.agent_result = agent_result

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
            agent_result=agent_result,
            trace_id=trace_id,
            failure_class=agent_result.get("failure_class"),
            business_payload=agent_result.get("business_payload") or {},
        )
        await _apply_turn_preference_extraction(
            db,
            user_id=runtime_state.user_id,
            user_message=runtime_state.message,
        )

        # ── 实时评测：异步评分，不阻塞 SSE 流 ──
        ended_at = datetime.now(timezone.utc)
        resolved_model_name = None
        if isinstance(runtime_state.resolved_model_config, dict):
            resolved_model_name = runtime_state.resolved_model_config.get("model_planner")
        final_event = {
            "event": "final",
            "data": {
                "stopped": False,
                "answer": final_json,
                "agent_result": agent_result,
                "trace_id": trace_id,
                "failure_class": agent_result.get("failure_class"),
                "business_payload": agent_result.get("business_payload") or {},
            },
        }
        _schedule_realtime_eval(
            session_id=runtime_state.session_id or state.session_id,
            user_id=runtime_state.user_id,
            trace_id=trace_id,
            user_message=runtime_state.message or state.message,
            events=[*collected_events, final_event],
            final_json=final_json,
            model_provider=provider,
            model_name=resolved_model_name,
            started_at=started_at,
            ended_at=ended_at,
            total_duration_ms=(time.monotonic() - stream_start_time) * 1000,
        )
        yield final_event
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
        provider_issue = _classify_llm_upstream_issue(exc)
        message = str(provider_issue.get("user_message") or _normalize_llm_upstream_error_message(exc))
        final_json = final_json_for_failure("upstream_error", message=message)
        final_json["provider_issue"] = {
            key: value
            for key, value in provider_issue.items()
            if key != "user_message" and value not in (None, "", [], {})
        }
        latest_value = locals().get("latest_state", state)
        worker_latest_value = getattr(exc, "agent_worker_latest_state", None)
        worker_events = getattr(exc, "agent_worker_events", None)
        if isinstance(worker_events, list):
            collected_events.extend(item for item in worker_events if isinstance(item, dict))
        diagnostics_source = worker_latest_value if worker_latest_value is not None else latest_value
        runtime_state = _coerce_runtime_state(diagnostics_source, state)
        route_decision = _route_decision_from_state(latest_value) or _route_decision_from_state(diagnostics_source) or runtime_state.route_decision
        runtime_diagnostics = _runtime_diagnostics_from_state(diagnostics_source)
        agent_result = build_agent_run_result(
            final_json=final_json,
            route_decision=route_decision,
            worker=runtime_state.agent_id,
            trace_id=trace_id,
            diagnostics={
                **runtime_diagnostics,
                "fallback_reason": "upstream_error",
                "provider_issue": provider_issue,
                "provider_issue_code": provider_issue.get("code"),
                "provider_issue_category": provider_issue.get("category"),
            },
            failure_class="upstream_error",
            status="failed",
        )
        answer_text = _render_final_text(final_json)
        error_event = {
            "event": "error",
            "data": {
                "message": message,
                "code": LLM_UPSTREAM_ERROR,
                "failure_class": "upstream_error",
                "provider_issue": provider_issue,
                "trace_id": trace_id,
            },
        }
        final_event = {
            "event": "final",
            "data": {
                "stopped": False,
                "answer": final_json,
                "agent_result": agent_result,
                "trace_id": trace_id,
                "failure_class": "upstream_error",
                "provider_issue": provider_issue,
                "business_payload": {},
            },
        }
        resolved_model_name = None
        if isinstance(state.resolved_model_config, dict):
            resolved_model_name = state.resolved_model_config.get("model_planner")
        _schedule_realtime_eval(
            session_id=state.session_id,
            user_id=state.user_id,
            trace_id=trace_id,
            user_message=state.message,
            events=[*collected_events, error_event, final_event],
            final_json=final_json,
            model_provider=provider,
            model_name=resolved_model_name,
            started_at=started_at,
            ended_at=datetime.now(timezone.utc),
            total_duration_ms=(time.monotonic() - stream_start_time) * 1000,
        )
        record_agent_metric(state.session_id, "fallback_final")
        try:
            await conversation.save_assistant_message(
                db,
                redis_client,
                state.session_id,
                answer_text,
                final_json,
                agent_result=agent_result,
                trace_id=trace_id,
                failure_class="upstream_error",
                business_payload={},
            )
        except Exception:
            logger.warning("failed_final_persist_failed session_id=%s trace_id=%s", state.session_id, trace_id, exc_info=True)
        error_payload = envelope(None, trace_id, code=LLM_UPSTREAM_ERROR, message=message)
        error_payload["failure_class"] = "upstream_error"
        error_payload["provider_issue"] = provider_issue
        yield {"event": "error", "data": error_payload}
        yield {"event": "delta", "data": {"token": answer_text}}
        yield final_event
        return
    finally:
        if conversation_cache:
            conversation_cache.close()
        conversation.clear_current_cache()
