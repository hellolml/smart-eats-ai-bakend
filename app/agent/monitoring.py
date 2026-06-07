from __future__ import annotations

import math
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, desc, false, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.eval_pricing import calculate_model_cost, calculate_tool_cost, normalize_usage
from app.infra.models.eval import (
    AgentVersion,
    ConversationCost,
    ConversationEvalJob,
    ConversationHumanReview,
    ConversationMetric,
    ConversationRun,
    ConversationToolCall,
    ConversationTraceEvent,
    EvaluatorDefinition,
    EvalCaseLineage,
    EvalDataset,
    EvalDatasetCase,
    EvalProject,
    EvaluationAlert,
    EvalRun,
    Experiment,
    ExperimentRun,
    PlaygroundRun,
    PromptVersion,
    SimulationRun,
    SimulationScenario,
    ToolVersion,
    TraceSpan,
)


WINDOW_HOURS = {
    "5m": 5 / 60,
    "1h": 1,
    "24h": 24,
    "7d": 24 * 7,
}

logger = logging.getLogger("app.agent.monitoring")


def parse_window_start(window: str = "24h") -> datetime:
    hours = WINDOW_HOURS.get(window, WINDOW_HOURS["24h"])
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def percentile(values: list[float], pct: float) -> float:
    clean = sorted(v for v in values if math.isfinite(v))
    if not clean:
        return 0.0
    if len(clean) == 1:
        return clean[0]
    rank = (len(clean) - 1) * pct
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return clean[low]
    return clean[low] + (clean[high] - clean[low]) * (rank - low)


def _event_data(event: dict[str, Any]) -> dict[str, Any]:
    data = event.get("data")
    return data if isinstance(data, dict) else {}


def _tool_success_from_result(data: dict[str, Any]) -> bool:
    if data.get("has_error") is True:
        return False
    if data.get("error") or data.get("error_reason"):
        return False
    return True


def classify_failure(
    *,
    error: str | None = None,
    error_reason: str | None = None,
    event_type: str | None = None,
    tool_name: str | None = None,
    metrics: dict[str, float] | None = None,
) -> str:
    """Classify failures into the long-term monitoring taxonomy."""
    text = " ".join(str(part) for part in (error_reason, error, event_type, tool_name) if part).lower()
    metric_values = metrics or {}
    if any(token in text for token in ("unauthorized", "invalid api key", "api key", "401", "403", "auth")):
        return "provider_auth"
    if any(token in text for token in ("rate limit", "429", "quota")):
        return "provider_rate_limit"
    if any(token in text for token in ("provider timeout", "model timeout", "llm timeout", "read timeout")):
        return "provider_timeout"
    if any(token in text for token in ("provider", "model", "openai", "qwen", "anthropic", "luciferai", "connection", "connect")):
        return "provider_model_error"
    if any(token in text for token in ("tool timeout", "timeout")) and tool_name:
        return "tool_timeout"
    if any(token in text for token in ("empty_result", "empty result", "no result", "not found")):
        return "tool_empty_result"
    if any(token in text for token in ("bad args", "invalid args", "validation", "schema")) and tool_name:
        return "tool_bad_args"
    if any(token in text for token in ("tool", "amap", "map", "http 4", "http 5", "api")):
        return "tool_api_error"
    if any(token in text for token in ("route", "routing", "worker")):
        return "agent_routing_error"
    if any(token in text for token in ("schema", "json", "parse")):
        return "agent_schema_error"
    if any(token in text for token in ("safety", "policy", "secret", "leak", "unsafe")):
        return "safety_policy_violation"
    if any(token in text for token in ("evaluator", "eval", "metric", "missing weighted metrics")):
        return "eval_framework_error"
    if _safe_float(metric_values.get("schema_compliance"), 1.0) < 0.8:
        return "agent_schema_error"
    if _safe_float(metric_values.get("no_leak"), 1.0) < 1.0:
        return "safety_policy_violation"
    if _safe_float(metric_values.get("task_success_proxy"), 1.0) < 1.0 or _safe_float(metric_values.get("overall_quality"), 1.0) < 0.7:
        return "agent_low_quality"
    return "none"


def _extract_model_usage(
    *,
    events: list[dict[str, Any]],
    raw: dict[str, Any],
    provider: str | None,
    model: str | None,
) -> dict[str, Any]:
    usage_totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
    }
    usage_events = 0
    usage_provider = provider
    usage_model = model
    for event in events:
        if event.get("event") not in {"model_usage", "usage"}:
            continue
        data = _event_data(event)
        raw_usage = data.get("usage") if isinstance(data.get("usage"), dict) else data
        normalized_event = normalize_usage(raw_usage)
        for key in usage_totals:
            usage_totals[key] += int(normalized_event.get(key) or 0)
        usage_events += 1
        usage_provider = data.get("provider") or usage_provider
        usage_model = data.get("model") or data.get("model_name") or usage_model
    if usage_events == 0:
        raw_usage = raw.get("token_usage") if isinstance(raw.get("token_usage"), dict) else raw.get("usage")
        usage_totals.update(normalize_usage(raw_usage if isinstance(raw_usage, dict) else {}))
    pricing = calculate_model_cost(
        provider=usage_provider,
        model=usage_model,
        input_tokens=usage_totals["input_tokens"],
        output_tokens=usage_totals["output_tokens"],
        cached_tokens=usage_totals["cached_tokens"],
        reasoning_tokens=usage_totals["reasoning_tokens"],
    )
    return {
        **usage_totals,
        "model_usage_event_count": usage_events,
        "provider": usage_provider,
        "model_name": usage_model,
        "token_cost": pricing["token_cost"],
        "pricing": pricing["pricing"],
        "cost_estimated": pricing["cost_estimated"],
    }


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _avg(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def _clean_title(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    title = value.strip()
    return title or None


def _extract_runtime_model_config(raw: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = {
        "source",
        "provider",
        "provider_value",
        "config_id",
        "display_name",
        "base_url",
        "model_planner",
        "model_writer",
        "model_vision_planner",
    }
    candidates: list[Any] = [raw.get("model_config")]
    agent_result = raw.get("agent_result")
    if isinstance(agent_result, dict):
        diagnostics = agent_result.get("diagnostics")
        if isinstance(diagnostics, dict):
            candidates.append(diagnostics.get("model_config"))
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        cleaned = {
            key: value
            for key, value in candidate.items()
            if key in allowed_keys and value not in (None, "")
        }
        if cleaned:
            return cleaned
    provider = raw.get("model_provider")
    model = raw.get("model_name")
    fallback: dict[str, Any] = {}
    if isinstance(provider, str) and provider:
        fallback["provider"] = provider
    if isinstance(model, str) and model:
        fallback["model_planner"] = model
    return fallback


async def load_chat_session_titles(session_ids: list[str]) -> dict[str, str]:
    ids = sorted({item for item in session_ids if isinstance(item, str) and item})
    if not ids:
        return {}
    try:
        from app.infra.db import AsyncSessionLocal
        from app.infra.models.chat import ChatSession

        async with AsyncSessionLocal() as app_session:
            rows = (await app_session.execute(
                select(ChatSession.id, ChatSession.title).where(ChatSession.id.in_(ids))
            )).all()
        titles: dict[str, str] = {}
        for session_id, raw_title in rows:
            title = _clean_title(raw_title)
            if title:
                titles[str(session_id)] = title
        return titles
    except Exception:
        logger.debug("chat_session_title_lookup_failed", exc_info=True)
        return {}


def _conversation_session_title(run: ConversationRun, fallback: str | None = None) -> str | None:
    raw = run.raw_json if isinstance(run.raw_json, dict) else {}
    return (
        _clean_title(fallback)
        or _clean_title(raw.get("session_title"))
        or _clean_title(raw.get("title"))
    )


def _metric_rows(run_id: str, metrics: dict[str, float], source: str = "realtime") -> list[ConversationMetric]:
    return [
        ConversationMetric(
            id=str(uuid4()),
            run_id=run_id,
            metric_name=name,
            metric_value=float(value),
            source=source,
        )
        for name, value in metrics.items()
    ]


def _span_type_for_event(event_type: str) -> str:
    mapping = {
        "model_usage": "llm_call",
        "usage": "llm_call",
        "tool_call": "tool_call",
        "tool_result": "tool_call",
        "context": "router",
        "recovery": "executor",
        "final": "executor",
        "error": "guardrail",
        "vision_error": "guardrail",
    }
    return mapping.get(event_type, event_type or "event")


def _span_status_for_event(event_type: str, data: dict[str, Any]) -> str:
    if event_type == "error" or data.get("has_error") or data.get("error") or data.get("error_reason"):
        return "error"
    return "ok"


def _span_name_for_event(event_type: str, data: dict[str, Any], index: int) -> str:
    if event_type in {"tool_call", "tool_result"} and data.get("name"):
        return str(data["name"])
    if event_type in {"model_usage", "usage"}:
        return str(data.get("model") or data.get("model_name") or "llm_call")
    if data.get("label"):
        return str(data["label"])
    return f"{event_type or 'event'} #{index}"


def _span_input_for_event(event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    if event_type == "tool_call":
        return data.get("args") if isinstance(data.get("args"), dict) else {}
    if event_type in {"model_usage", "usage"}:
        return {"provider": data.get("provider"), "model": data.get("model") or data.get("model_name")}
    return {}


def _span_output_for_event(event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    if event_type == "tool_result":
        return {
            "output_preview": data.get("output_preview"),
            "has_error": data.get("has_error"),
            "error_type": data.get("error_type"),
        }
    if event_type in {"model_usage", "usage"}:
        return {"usage": data.get("usage") if isinstance(data.get("usage"), dict) else data}
    if event_type == "final":
        return data
    return {}


def _trace_span_from_event(
    *,
    run_id: str,
    trace_id: str | None,
    session_id: str,
    index: int,
    event: dict[str, Any],
    fallback_started_at: datetime | None,
) -> TraceSpan:
    data = _event_data(event)
    event_type = str(event.get("event") or "")
    duration_ms = data.get("latency_ms") or data.get("duration_ms")
    timestamp = data.get("timestamp")
    started_at = fallback_started_at
    if isinstance(timestamp, (int, float)):
        try:
            started_at = datetime.fromtimestamp(float(timestamp), tz=timezone.utc)
        except (OSError, ValueError):
            started_at = fallback_started_at
    status = _span_status_for_event(event_type, data)
    return TraceSpan(
        id=str(uuid4()),
        run_id=run_id,
        trace_id=trace_id,
        session_id=session_id,
        span_index=index,
        span_type=_span_type_for_event(event_type),
        name=_span_name_for_event(event_type, data, index),
        status=status,
        started_at=started_at,
        ended_at=None,
        duration_ms=float(duration_ms) if isinstance(duration_ms, (int, float)) else None,
        input_json=_span_input_for_event(event_type, data),
        output_json=_span_output_for_event(event_type, data),
        metadata_json={"event_type": event_type, "data": data},
        score_json=None,
        error=str(data.get("error") or data.get("error_reason") or data.get("message") or "") if status == "error" else None,
    )


async def persist_realtime_conversation(
    session: AsyncSession,
    *,
    result: Any,
    events: list[dict[str, Any]],
    final_json: dict[str, Any] | None,
    user_id: str | None,
    trace_id: str | None,
    model_provider: str | None,
    model_name: str | None,
    started_at: datetime | None,
    ended_at: datetime | None,
) -> str:
    """Persist a single production conversation evaluation.

    The normalized conversation tables are the long-term source. A compact
    EvalRun row is also written for compatibility with the existing realtime
    eval dashboard/API.
    """
    run_id = result.id
    session_title = _clean_title(getattr(result, "session_title", None))
    if not session_title:
        session_title = (await load_chat_session_titles([result.session_id])).get(result.session_id)
    await session.execute(delete(ConversationMetric).where(ConversationMetric.run_id == run_id))
    await session.execute(delete(ConversationCost).where(ConversationCost.run_id == run_id))
    await session.execute(delete(ConversationEvalJob).where(ConversationEvalJob.run_id == run_id))
    await session.execute(delete(ConversationToolCall).where(ConversationToolCall.run_id == run_id))
    await session.execute(delete(ConversationTraceEvent).where(ConversationTraceEvent.run_id == run_id))
    await session.execute(delete(TraceSpan).where(TraceSpan.run_id == run_id))
    await session.execute(delete(ConversationRun).where(ConversationRun.id == run_id))

    latency_ms = float(result.total_duration_ms or 0.0)
    final_state = final_json.get("state") if isinstance(final_json, dict) else None
    final_event_payload = next(
        (
            _event_data(event)
            for event in reversed(events)
            if event.get("event") == "final" and isinstance(_event_data(event), dict)
        ),
        {},
    )
    agent_result = final_event_payload.get("agent_result") if isinstance(final_event_payload.get("agent_result"), dict) else {}
    agent_failure_class = agent_result.get("failure_class") or final_event_payload.get("failure_class")
    status = "error" if result.error else "completed"
    raw = result.to_dict()
    model_config = _extract_runtime_model_config({"agent_result": agent_result, "model_provider": model_provider, "model_name": model_name})
    raw.update({
        "trace_id": trace_id,
        "user_id": user_id,
        "session_title": session_title,
        "model_provider": model_provider,
        "model_name": model_name,
        "model_config": model_config,
        "final_state": final_state,
        "agent_result": agent_result,
        "failure_class": agent_failure_class,
    })
    run = ConversationRun(
        id=run_id,
        session_id=result.session_id,
        user_id=user_id,
        trace_id=trace_id,
        scene=result.scene,
        worker=agent_result.get("worker") or result.agent_id,
        model_provider=model_provider,
        model_name=model_name,
        status=status,
        started_at=started_at,
        ended_at=ended_at,
        latency_ms=latency_ms,
        final_state=str(final_state) if final_state is not None else None,
        is_fallback=bool(result.is_fallback or agent_failure_class),
        raw_json=raw,
    )
    session.add(run)
    # Ensure the parent row exists before child rows without ORM relationships
    # are autoflushed by later queries/inserts.
    await session.flush()

    tool_calls: list[ConversationToolCall] = []
    result_by_name: dict[str, dict[str, Any]] = {}
    tool_cost_total = 0.0
    tool_cost_known = True
    for event in events:
        data = _event_data(event)
        if event.get("event") == "tool_result" and data.get("name"):
            result_by_name[str(data.get("name"))] = data

    for index, event in enumerate(events):
        data = _event_data(event)
        event_type = str(event.get("event") or "")
        tool_name = data.get("name")
        duration_ms = data.get("latency_ms") or data.get("duration_ms")
        session.add(ConversationTraceEvent(
            id=str(uuid4()),
            run_id=run_id,
            event_index=index,
            event_type=event_type,
            timestamp=data.get("timestamp") if isinstance(data.get("timestamp"), (int, float)) else None,
            tool_name=str(tool_name) if tool_name else None,
            duration_ms=float(duration_ms) if isinstance(duration_ms, (int, float)) else None,
            data_json=data,
        ))
        session.add(_trace_span_from_event(
            run_id=run_id,
            trace_id=trace_id,
            session_id=result.session_id,
            index=index,
            event=event,
            fallback_started_at=started_at,
        ))
        if event_type == "tool_call" and tool_name:
            result_data = result_by_name.get(str(tool_name), {})
            tool_cost = calculate_tool_cost(str(tool_name))
            tool_cost_total += _safe_float(tool_cost.get("tool_cost"))
            tool_cost_known = tool_cost_known and bool(tool_cost.get("cost_estimated"))
            call = ConversationToolCall(
                id=str(uuid4()),
                run_id=run_id,
                tool_name=str(tool_name),
                args_json=data.get("args") if isinstance(data.get("args"), dict) else {},
                success=_tool_success_from_result(result_data),
                error_reason=result_data.get("error_reason") or result_data.get("error"),
                latency_ms=float(duration_ms) if isinstance(duration_ms, (int, float)) else None,
                cost=_safe_float(tool_cost.get("tool_cost")),
            )
            session.add(call)
            tool_calls.append(call)

    tool_error_rate = 0.0
    if tool_calls:
        failures = sum(1 for item in tool_calls if not item.success)
        tool_error_rate = failures / len(tool_calls)
    recovery_events = [event for event in events if event.get("event") == "recovery"]
    recovery_rate = 1.0 if recovery_events and not result.error else 0.0
    provider_failure_class = classify_failure(error=result.error, error_reason=result.error_reason)
    provider_error_rate = 1.0 if provider_failure_class.startswith("provider_") else 0.0
    tool_timeout_rate = 0.0
    if tool_calls:
        timeout_failures = sum(1 for item in tool_calls if classify_failure(error_reason=item.error_reason, tool_name=item.tool_name) == "tool_timeout")
        tool_timeout_rate = timeout_failures / len(tool_calls)
    tool_call_accuracy_proxy = 1.0 - tool_error_rate
    metrics = {
        "overall_quality": float(result.overall_quality or 0.0),
        "task_success_proxy": 1.0 if result.has_content and not result.is_fallback and not result.error else 0.0,
        "partial_success_proxy": 1.0 if result.has_content else 0.0,
        "schema_compliance": float(result.schema_compliance or 0.0),
        "constraint_satisfaction_rule": 1.0,
        "no_leak": float(result.no_leak or 0.0),
        "repeated_action_rate": float(result.repeated_action_rate or 0.0),
        "recovery_rate": recovery_rate,
        "tool_error_rate": tool_error_rate,
        "avg_steps": float(len(events)),
        "fallback_rate": 1.0 if result.is_fallback else 0.0,
        "secret_leak_rate": 1.0 if float(result.no_leak or 1.0) < 1.0 else 0.0,
        "policy_violation_rate": 0.0,
        "human_escalation_rate": 0.0,
        "cache_hit_rate": 0.0,
        "unsafe_action_block_rate": 0.0,
        "provider_error_rate": provider_error_rate,
        "tool_call_accuracy_proxy": tool_call_accuracy_proxy,
        "tool_timeout_rate": tool_timeout_rate,
    }
    model_usage = _extract_model_usage(
        events=events,
        raw=raw,
        provider=model_provider,
        model=model_name,
    )
    metrics["model_call_count"] = float(model_usage.get("model_usage_event_count") or 0)
    metrics["token_total"] = float(model_usage.get("total_tokens") or 0)
    session.add_all(_metric_rows(run_id, metrics))
    session.add(ConversationEvalJob(
        id=str(uuid4()),
        run_id=run_id,
        job_type="lightweight",
        status="succeeded",
        finished_at=ended_at,
    ))
    total_cost = _safe_float(model_usage.get("token_cost")) + tool_cost_total
    session.add(ConversationCost(
        id=str(uuid4()),
        run_id=run_id,
        token_input=int(model_usage.get("input_tokens") or 0),
        token_output=int(model_usage.get("output_tokens") or 0),
        cached_tokens=int(model_usage.get("cached_tokens") or 0),
        reasoning_tokens=int(model_usage.get("reasoning_tokens") or 0),
        total_tokens=int(model_usage.get("total_tokens") or 0),
        provider=model_usage.get("provider"),
        model_name=model_usage.get("model_name"),
        token_cost=_safe_float(model_usage.get("token_cost")),
        tool_cost=round(tool_cost_total, 8),
        total_cost=round(total_cost, 8),
        cost_estimated=bool(model_usage.get("cost_estimated")) and tool_cost_known,
        pricing_json={"model": model_usage.get("pricing"), "tools_estimated": tool_cost_known},
    ))

    existing_eval = await session.scalar(select(EvalRun).where(EvalRun.report_name == f"realtime/{result.session_id}/{run_id}"))
    if existing_eval:
        existing_eval.timestamp = ended_at
        existing_eval.overall_success_rate = float(result.overall_quality or 0.0)
        existing_eval.duration_seconds = latency_ms / 1000.0
        existing_eval.raw_report_json = raw
    else:
        session.add(EvalRun(
            id=run_id,
            report_name=f"realtime/{result.session_id}/{run_id}",
            timestamp=ended_at,
            suite="realtime",
            runner="auto",
            model_provider=model_provider,
            model_name=model_name,
            overall_success_rate=float(result.overall_quality or 0.0),
            total_cases=1,
            total_trials=1,
            duration_seconds=latency_ms / 1000.0,
            raw_report_json=raw,
        ))
    return run_id


async def list_conversation_runs(
    session: AsyncSession,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
    scene: str | None = None,
    worker: str | None = None,
    status: str | None = None,
    tool: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[int, list[ConversationRun]]:
    base = select(ConversationRun)
    count = select(func.count()).select_from(ConversationRun)
    filters = []
    if since:
        filters.append(ConversationRun.started_at >= since)
    if until:
        filters.append(ConversationRun.started_at <= until)
    if session_id:
        filters.append(ConversationRun.session_id == session_id)
    if user_id:
        filters.append(ConversationRun.user_id == user_id)
    if scene:
        filters.append(ConversationRun.scene == scene)
    if worker:
        filters.append(ConversationRun.worker == worker)
    if status:
        filters.append(ConversationRun.status == status)
    if filters:
        base = base.where(*filters)
        count = count.where(*filters)
    if tool:
        matching = select(ConversationToolCall.run_id).where(ConversationToolCall.tool_name == tool)
        base = base.where(ConversationRun.id.in_(matching))
        count = count.where(ConversationRun.id.in_(matching))
    total = (await session.execute(count)).scalar() or 0
    rows = (await session.execute(base.order_by(desc(ConversationRun.started_at)).offset(offset).limit(limit))).scalars().all()
    return int(total), list(rows)


async def list_reviews(
    session: AsyncSession,
    *,
    decision: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[int, list[dict[str, Any]]]:
    query = select(ConversationRun, ConversationHumanReview).join(
        ConversationHumanReview,
        ConversationHumanReview.run_id == ConversationRun.id,
        isouter=True,
    )
    count = select(func.count()).select_from(ConversationRun).join(
        ConversationHumanReview,
        ConversationHumanReview.run_id == ConversationRun.id,
        isouter=True,
    )
    if decision:
        if decision == "pending":
            query = query.where((ConversationHumanReview.decision == "pending") | (ConversationHumanReview.id.is_(None)))
            count = count.where((ConversationHumanReview.decision == "pending") | (ConversationHumanReview.id.is_(None)))
        else:
            query = query.where(ConversationHumanReview.decision == decision)
            count = count.where(ConversationHumanReview.decision == decision)
    total = (await session.execute(count)).scalar() or 0
    rows = (await session.execute(
        query.order_by(desc(ConversationRun.started_at)).offset(offset).limit(limit)
    )).all()
    title_map = await load_chat_session_titles([run.session_id for run, _review in rows])
    return int(total), [
        {
            "run": conversation_run_summary(run, session_title=title_map.get(run.session_id)),
            "review": human_review_summary(review) if review else {
                "run_id": run.id,
                "decision": "pending",
                "reason": None,
                "notes": None,
                "reviewer_id": None,
                "created_at": None,
                "updated_at": None,
            },
        }
        for run, review in rows
    ]


async def upsert_review(
    session: AsyncSession,
    *,
    run_id: str,
    reviewer_id: str | None,
    decision: str,
    reason: str | None = None,
    notes: str | None = None,
    failure_reason: str | None = None,
    failure_tags: list[str] | None = None,
    corrected_answer: str | None = None,
    expected_behavior: str | None = None,
    review_confidence: float | None = None,
    dataset_candidate: bool = False,
) -> dict[str, Any] | None:
    run = await session.scalar(select(ConversationRun).where(ConversationRun.id == run_id))
    if not run:
        return None
    now = datetime.now(timezone.utc)
    review = await session.scalar(select(ConversationHumanReview).where(ConversationHumanReview.run_id == run_id))
    if review:
        review.reviewer_id = reviewer_id
        review.decision = decision
        review.reason = reason
        review.failure_reason = failure_reason
        review.failure_tags_json = failure_tags or []
        review.corrected_answer = corrected_answer
        review.expected_behavior = expected_behavior
        review.review_confidence = review_confidence
        review.dataset_candidate = dataset_candidate
        review.notes = notes
        review.updated_at = now
    else:
        review = ConversationHumanReview(
            id=str(uuid4()),
            run_id=run_id,
            reviewer_id=reviewer_id,
            decision=decision,
            reason=reason,
            failure_reason=failure_reason,
            failure_tags_json=failure_tags or [],
            corrected_answer=corrected_answer,
            expected_behavior=expected_behavior,
            review_confidence=review_confidence,
            dataset_candidate=dataset_candidate,
            notes=notes,
            updated_at=now,
        )
        session.add(review)
    return human_review_summary(review)


async def ensure_eval_dataset(
    session: AsyncSession,
    *,
    name: str,
    version: str = "draft",
    suite: str | None = None,
    status: str = "draft",
    created_by: str | None = None,
) -> EvalDataset:
    dataset = await session.scalar(
        select(EvalDataset).where(EvalDataset.name == name, EvalDataset.version == version)
    )
    if dataset:
        return dataset
    dataset = EvalDataset(
        id=str(uuid4()),
        name=name,
        version=version,
        suite=suite or name,
        status=status,
        created_by=created_by,
    )
    session.add(dataset)
    await session.flush()
    return dataset


def _case_from_conversation_run(
    run: ConversationRun,
    *,
    priority: str,
    category: str,
    owner: str | None,
    review: ConversationHumanReview | None = None,
) -> dict[str, Any]:
    raw = run.raw_json if isinstance(run.raw_json, dict) else {}
    task = raw.get("user_message") or raw.get("task") or f"Production trace {run.id}"
    expected_behavior = review.expected_behavior if review else None
    corrected_answer = review.corrected_answer if review else None
    expectations: dict[str, Any] = {
        "output": {
            "schema_compliant": True,
            "state_not": "fallback",
        },
        "source_trace": {
            "run_id": run.id,
            "trace_id": run.trace_id,
            "session_id": run.session_id,
        },
    }
    if expected_behavior:
        expectations["expected_behavior"] = expected_behavior
    if corrected_answer:
        expectations["corrected_answer"] = corrected_answer
    scene = run.scene or "chat"
    return {
        "id": f"prod-{run.id}",
        "category": category,
        "scene": scene,
        "task": str(task),
        "initial_context": {},
        "expectations": expectations,
        "scoring": {},
        "tags": ["production_trace", *(review.failure_tags_json or [] if review else [])],
        "priority": priority,
        "difficulty": "medium",
        "owner": owner,
    }


async def create_dataset_case_from_trace(
    session: AsyncSession,
    *,
    run_id: str,
    dataset_name: str,
    version: str = "draft",
    priority: str = "p1",
    category: str = "regression",
    owner: str | None = None,
    review_status: str = "draft",
) -> dict[str, Any] | None:
    run = await session.scalar(select(ConversationRun).where(ConversationRun.id == run_id))
    if not run:
        return None
    review = await session.scalar(select(ConversationHumanReview).where(ConversationHumanReview.run_id == run_id))
    dataset = await ensure_eval_dataset(
        session,
        name=dataset_name,
        version=version,
        suite=dataset_name,
        status="draft" if version == "draft" else "active",
        created_by=owner,
    )
    case_json = _case_from_conversation_run(
        run,
        priority=priority,
        category=category,
        owner=owner,
        review=review,
    )
    existing = await session.scalar(
        select(EvalDatasetCase).where(EvalDatasetCase.dataset_id == dataset.id, EvalDatasetCase.case_id == case_json["id"])
    )
    if existing:
        existing.case_json = case_json
        existing.source = "production_trace"
        existing.scene = case_json.get("scene")
        existing.category = case_json.get("category")
        existing.priority = case_json.get("priority")
        existing.owner = owner
        existing.review_status = review_status
        dataset_case = existing
    else:
        dataset_case = EvalDatasetCase(
            id=str(uuid4()),
            dataset_id=dataset.id,
            case_id=case_json["id"],
            source="production_trace",
            case_json=case_json,
            scene=case_json.get("scene"),
            category=case_json.get("category"),
            priority=case_json.get("priority"),
            owner=owner,
            review_status=review_status,
        )
        session.add(dataset_case)
        await session.flush()
    session.add(EvalCaseLineage(
        id=str(uuid4()),
        source_run_id=run.id,
        source_trace_id=run.trace_id,
        target_case_id=case_json["id"],
        dataset_case_id=dataset_case.id,
    ))
    if review:
        review.dataset_candidate = True
        if review.decision != "converted_to_case":
            review.decision = "converted_to_case"
        review.updated_at = datetime.now(timezone.utc)
    return dataset_case_summary(dataset, dataset_case)


def dataset_summary(dataset: EvalDataset, cases: list[EvalDatasetCase] | None = None) -> dict[str, Any]:
    rows = cases or []
    by_scene: dict[str, int] = {}
    by_category: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    by_source: dict[str, int] = {}
    by_owner: dict[str, int] = {}
    for case in rows:
        for value, target in (
            (case.scene, by_scene),
            (case.category, by_category),
            (case.priority, by_priority),
            (case.source, by_source),
            (case.owner, by_owner),
        ):
            key = str(value or "unknown")
            target[key] = target.get(key, 0) + 1
    return {
        "id": dataset.id,
        "name": dataset.name,
        "version": dataset.version,
        "suite": dataset.suite,
        "status": dataset.status,
        "created_by": dataset.created_by,
        "created_at": dataset.created_at.isoformat() if dataset.created_at else None,
        "total_cases": len(rows),
        "by_scene": by_scene,
        "by_category": by_category,
        "by_priority": by_priority,
        "by_source": by_source,
        "by_owner": by_owner,
    }


def dataset_case_summary(dataset: EvalDataset, case: EvalDatasetCase) -> dict[str, Any]:
    return {
        "dataset": dataset.name,
        "version": dataset.version,
        "case_id": case.case_id,
        "source": case.source,
        "scene": case.scene,
        "category": case.category,
        "priority": case.priority,
        "owner": case.owner,
        "review_status": case.review_status,
        "last_failed_at": case.last_failed_at.isoformat() if case.last_failed_at else None,
        "created_at": case.created_at.isoformat() if case.created_at else None,
        "case": case.case_json,
    }


async def list_persisted_datasets(session: AsyncSession) -> list[dict[str, Any]]:
    datasets = (await session.execute(select(EvalDataset).order_by(EvalDataset.name, EvalDataset.version))).scalars().all()
    result = []
    for dataset in datasets:
        cases = (await session.execute(
            select(EvalDatasetCase).where(EvalDatasetCase.dataset_id == dataset.id)
        )).scalars().all()
        result.append(dataset_summary(dataset, list(cases)))
    return result


async def list_dataset_versions(session: AsyncSession, dataset_name: str) -> list[dict[str, Any]]:
    datasets = (await session.execute(
        select(EvalDataset).where(EvalDataset.name == dataset_name).order_by(desc(EvalDataset.created_at))
    )).scalars().all()
    result = []
    for dataset in datasets:
        cases = (await session.execute(
            select(EvalDatasetCase).where(EvalDatasetCase.dataset_id == dataset.id)
        )).scalars().all()
        result.append(dataset_summary(dataset, list(cases)))
    return result


async def list_persisted_dataset_cases(session: AsyncSession, dataset_name: str, version: str | None = None) -> list[dict[str, Any]]:
    query = select(EvalDataset).where(EvalDataset.name == dataset_name)
    if version:
        query = query.where(EvalDataset.version == version)
    else:
        query = query.order_by(desc(EvalDataset.created_at))
    dataset = await session.scalar(query)
    if not dataset:
        return []
    cases = (await session.execute(
        select(EvalDatasetCase).where(EvalDatasetCase.dataset_id == dataset.id).order_by(desc(EvalDatasetCase.created_at))
    )).scalars().all()
    return [dataset_case_summary(dataset, case) for case in cases]


async def create_dataset_version(
    session: AsyncSession,
    *,
    dataset_name: str,
    version: str,
    status: str = "draft",
    created_by: str | None = None,
) -> dict[str, Any]:
    dataset = await ensure_eval_dataset(
        session,
        name=dataset_name,
        version=version,
        suite=dataset_name,
        status=status,
        created_by=created_by,
    )
    return dataset_summary(dataset, [])


def alert_summary(alert: EvaluationAlert) -> dict[str, Any]:
    return {
        "id": alert.id,
        "alert_type": alert.alert_type,
        "severity": alert.severity,
        "status": alert.status,
        "payload": alert.payload_json or {},
        "created_at": alert.created_at.isoformat() if alert.created_at else None,
        "acknowledged_at": alert.acknowledged_at.isoformat() if alert.acknowledged_at else None,
        "acknowledged_by": alert.acknowledged_by,
        "resolved_at": alert.resolved_at.isoformat() if alert.resolved_at else None,
        "resolved_by": alert.resolved_by,
        "notification_sent": alert.notification_sent,
        "notification_sent_at": alert.notification_sent_at.isoformat() if alert.notification_sent_at else None,
    }


async def list_alerts(
    session: AsyncSession,
    *,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[int, list[dict[str, Any]]]:
    query = select(EvaluationAlert)
    count = select(func.count()).select_from(EvaluationAlert)
    if status:
        query = query.where(EvaluationAlert.status == status)
        count = count.where(EvaluationAlert.status == status)
    total = (await session.execute(count)).scalar() or 0
    rows = (await session.execute(
        query.order_by(desc(EvaluationAlert.created_at)).offset(offset).limit(limit)
    )).scalars().all()
    return int(total), [alert_summary(row) for row in rows]


async def update_alert_status(
    session: AsyncSession,
    *,
    alert_id: str,
    status: str,
    actor: str | None = None,
) -> dict[str, Any] | None:
    alert = await session.scalar(select(EvaluationAlert).where(EvaluationAlert.id == alert_id))
    if not alert:
        return None
    now = datetime.now(timezone.utc)
    alert.status = status
    if status == "acknowledged":
        alert.acknowledged_at = now
        alert.acknowledged_by = actor
    if status == "resolved":
        alert.resolved_at = now
        alert.resolved_by = actor
    return alert_summary(alert)


async def create_or_update_alert(
    session: AsyncSession,
    *,
    alert_type: str,
    severity: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    existing = await session.scalar(
        select(EvaluationAlert).where(
            EvaluationAlert.alert_type == alert_type,
            EvaluationAlert.status == "open",
        )
    )
    if existing:
        existing.severity = severity
        existing.payload_json = payload
        return alert_summary(existing)
    alert = EvaluationAlert(
        id=str(uuid4()),
        alert_type=alert_type,
        severity=severity,
        status="open",
        payload_json=payload,
    )
    session.add(alert)
    return alert_summary(alert)


async def evaluate_alert_rules(session: AsyncSession, *, since: datetime, notify: bool = True) -> list[dict[str, Any]]:
    overview = await aggregate_monitoring_overview(session, since=since)
    failures = await aggregate_failures(session, since=since)
    cost = await aggregate_cost_latency(session, since=since)
    safety = await aggregate_safety(session, since=since)
    alerts: list[dict[str, Any]] = []
    rules = [
        ("provider_error_rate", overview.get("provider_error_rate", 0.0), 0.05, "critical"),
        ("tool_error_rate", overview.get("tool_error_rate", 0.0), 0.05, "warning"),
        ("latency_p95", cost.get("latency_p95_ms", 0.0), 10_000, "warning"),
        ("safety_policy_violation_rate", safety.get("policy_violation_rate", 0.0), 0.0, "critical"),
        ("secret_leak_rate", safety.get("secret_leak_rate", 0.0), 0.0, "critical"),
    ]
    for alert_type, actual, threshold, severity in rules:
        breached = actual > threshold if threshold == 0.0 else actual >= threshold
        if breached:
            alerts.append(await create_or_update_alert(
                session,
                alert_type=alert_type,
                severity=severity,
                payload={
                    "actual": actual,
                    "threshold": threshold,
                    "window_start": since.isoformat(),
                    "overview": overview,
                    "failures": failures,
                },
            ))
    if cost.get("total_cost", 0.0) > 100:
        alerts.append(await create_or_update_alert(
            session,
            alert_type="token_cost_budget",
            severity="warning",
            payload={"actual": cost.get("total_cost"), "threshold": 100, "window_start": since.isoformat()},
        ))

    # 发送告警通知
    if notify and alerts:
        try:
            from app.agent.alerting import send_alert_notifications
            new_alerts = [
                a for a in alerts
                if a.get("status") == "open" and not a.get("notification_sent")
            ]
            if new_alerts:
                results = await send_alert_notifications(new_alerts)
                # 更新通知状态
                for alert_data in new_alerts:
                    alert_id = alert_data.get("id")
                    if not alert_id:
                        continue
                    alert_obj = await session.scalar(select(EvaluationAlert).where(EvaluationAlert.id == alert_id))
                    if alert_obj:
                        alert_type_key = alert_data.get("alert_type", "unknown")
                        alert_obj.notification_sent = results.get(alert_type_key, False)
                        if alert_obj.notification_sent:
                            alert_obj.notification_sent_at = datetime.now(timezone.utc)
        except Exception as exc:
            logger.warning("Failed to send alert notifications: %s", exc)

    return alerts


async def load_conversation_trace(session: AsyncSession, run_id: str) -> dict[str, Any] | None:
    run = await session.scalar(select(ConversationRun).where(ConversationRun.id == run_id))
    if not run:
        return None
    title_map = await load_chat_session_titles([run.session_id]) if not _conversation_session_title(run) else {}
    events = (await session.execute(
        select(ConversationTraceEvent).where(ConversationTraceEvent.run_id == run_id).order_by(ConversationTraceEvent.event_index)
    )).scalars().all()
    tools = (await session.execute(
        select(ConversationToolCall).where(ConversationToolCall.run_id == run_id)
    )).scalars().all()
    metrics = (await session.execute(
        select(ConversationMetric).where(ConversationMetric.run_id == run_id)
    )).scalars().all()
    review = await session.scalar(select(ConversationHumanReview).where(ConversationHumanReview.run_id == run_id))
    cost = await session.scalar(select(ConversationCost).where(ConversationCost.run_id == run_id))
    return {
        "run": conversation_run_summary(run, session_title=title_map.get(run.session_id)),
        "events": [trace_event_summary(item) for item in events],
        "spans": [trace_span_summary(item) for item in await list_trace_spans_for_run(session, run_id)],
        "tool_calls": [tool_call_summary(item) for item in tools],
        "metrics": {item.metric_name: item.metric_value for item in metrics},
        "cost": cost_summary(cost) if cost else None,
        "review": human_review_summary(review) if review else None,
    }


async def list_trace_spans_for_run(session: AsyncSession, run_id: str) -> list[TraceSpan]:
    return list((await session.execute(
        select(TraceSpan).where(TraceSpan.run_id == run_id).order_by(TraceSpan.span_index)
    )).scalars().all())


def trace_span_summary(span: TraceSpan) -> dict[str, Any]:
    return {
        "id": span.id,
        "run_id": span.run_id,
        "trace_id": span.trace_id,
        "session_id": span.session_id,
        "parent_span_id": span.parent_span_id,
        "index": span.span_index,
        "span_type": span.span_type,
        "name": span.name,
        "status": span.status,
        "started_at": span.started_at.isoformat() if span.started_at else None,
        "ended_at": span.ended_at.isoformat() if span.ended_at else None,
        "duration_ms": span.duration_ms,
        "input": span.input_json or {},
        "output": span.output_json or {},
        "metadata": span.metadata_json or {},
        "scores": span.score_json or {},
        "error": span.error,
    }


async def load_trace_detail_by_trace_id(session: AsyncSession, trace_id: str) -> dict[str, Any] | None:
    run = await session.scalar(
        select(ConversationRun).where((ConversationRun.trace_id == trace_id) | (ConversationRun.id == trace_id))
    )
    if not run:
        return None
    detail = await load_conversation_trace(session, run.id)
    if not detail:
        return None
    spans = (await session.execute(
        select(TraceSpan).where(TraceSpan.run_id == run.id).order_by(TraceSpan.span_index)
    )).scalars().all()
    detail["spans"] = [trace_span_summary(span) for span in spans]
    detail["span_tree"] = build_span_tree(detail["spans"])
    return detail


def build_span_tree(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {span["id"]: {**span, "children": []} for span in spans}
    roots: list[dict[str, Any]] = []
    for span in by_id.values():
        parent_id = span.get("parent_span_id")
        if parent_id and parent_id in by_id:
            by_id[parent_id]["children"].append(span)
        else:
            roots.append(span)
    return roots


async def ensure_default_eval_hub_seed(session: AsyncSession) -> None:
    for environment in ("local", "staging", "prod"):
        existing = await session.scalar(
            select(EvalProject).where(EvalProject.name == "smart-eats", EvalProject.environment == environment)
        )
        if not existing:
            session.add(EvalProject(
                id=str(uuid4()),
                name="smart-eats",
                environment=environment,
                status="active",
                metadata_json={"source": "default_seed"},
            ))
    defaults = [
        ("task_success", "rule", "v1", 0.8),
        ("schema_compliance", "json_schema", "v1", 0.9),
        ("tool_correctness", "tool_trajectory", "v1", 0.8),
        ("safety", "safety", "v1", 0.95),
        ("answer_quality", "llm_judge", "v1", 0.8),
    ]
    for name, evaluator_type, version, threshold in defaults:
        existing = await session.scalar(
            select(EvaluatorDefinition).where(EvaluatorDefinition.name == name, EvaluatorDefinition.version == version)
        )
        if not existing:
            session.add(EvaluatorDefinition(
                id=str(uuid4()),
                name=name,
                evaluator_type=evaluator_type,
                version=version,
                status="active",
                threshold=threshold,
                owner="system",
                metadata_json={"source": "default_seed"},
            ))


async def eval_hub_overview(session: AsyncSession, *, since: datetime) -> dict[str, Any]:
    await ensure_default_eval_hub_seed(session)
    monitoring = await aggregate_monitoring_overview(session, since=since)
    failures = await aggregate_failures(session, since=since)
    cost_latency = await aggregate_cost_latency(session, since=since)
    safety = await aggregate_safety(session, since=since)
    review_total, review_items = await list_reviews(session, decision="pending", limit=5)
    alert_total, alert_items = await list_alerts(session, status="open", limit=5)
    eval_runs = (await session.execute(
        select(EvalRun).where(EvalRun.suite != "realtime").order_by(desc(EvalRun.timestamp)).limit(5)
    )).scalars().all()
    regressions = sum(1 for row in eval_runs if float(row.overall_success_rate or 0.0) < 1.0)
    return {
        "project": "smart-eats",
        "monitoring": monitoring,
        "failures": failures,
        "cost_latency": cost_latency,
        "safety": safety,
        "critical_alerts": sum(1 for item in alert_items if item.get("severity") == "critical"),
        "open_alerts": alert_total,
        "pending_reviews": review_total,
        "regression_count": regressions,
        "recent_eval_runs": [
            {
                "id": row.id,
                "report_name": row.report_name,
                "timestamp": row.timestamp.isoformat() if row.timestamp else None,
                "suite": row.suite,
                "runner": row.runner,
                "overall_success_rate": row.overall_success_rate,
                "duration_seconds": row.duration_seconds,
                "release_marker": row.release_marker,
            }
            for row in eval_runs
        ],
        "review_queue": review_items,
        "alerts": alert_items,
    }


async def list_eval_hub_live_sessions(
    session: AsyncSession,
    *,
    since: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    query = select(ConversationRun)
    if since:
        query = query.where(ConversationRun.started_at >= since)
    rows = (await session.execute(
        query.order_by(desc(ConversationRun.started_at)).offset(offset).limit(limit * 5)
    )).scalars().all()
    grouped: dict[str, list[ConversationRun]] = {}
    for run in rows:
        grouped.setdefault(run.session_id, []).append(run)
    title_map = await load_chat_session_titles(list(grouped.keys()))
    records = []
    for session_id, runs in list(grouped.items())[offset:offset + limit]:
        latest = runs[0]
        latest_summary = conversation_run_summary(latest, session_title=title_map.get(session_id))
        records.append({
            "session_id": session_id,
            "session_title": latest_summary.get("session_title"),
            "title": latest_summary.get("session_title"),
            "user_id": latest.user_id,
            "status": latest.status,
            "scene": latest.scene,
            "worker": latest.worker,
            "latest_score": latest_summary.get("overall_quality"),
            "risk_level": "high" if latest.status == "error" or latest.is_fallback else "normal",
            "turn_count": len(runs),
            "latency_ms": latest.latency_ms,
            "model": latest.model_name,
            "trace_id": latest.trace_id,
            "created_at": latest.started_at.isoformat() if latest.started_at else None,
        })
    return {"total": len(grouped), "limit": limit, "offset": offset, "records": records}


async def load_eval_hub_live_session(session: AsyncSession, session_id: str) -> dict[str, Any] | None:
    runs = (await session.execute(
        select(ConversationRun).where(ConversationRun.session_id == session_id).order_by(ConversationRun.started_at)
    )).scalars().all()
    if not runs:
        return None
    title_map = await load_chat_session_titles([session_id])
    session_title = _conversation_session_title(runs[-1], title_map.get(session_id))
    turns = []
    for run in runs:
        detail = await load_conversation_trace(session, run.id)
        if detail:
            turns.append(detail)
    return {
        "session_id": session_id,
        "session_title": session_title,
        "title": session_title,
        "user_id": runs[-1].user_id,
        "turn_count": len(runs),
        "latest": conversation_run_summary(runs[-1], session_title=session_title),
        "turns": turns,
    }


async def list_eval_hub_traces(
    session: AsyncSession,
    *,
    since: datetime | None = None,
    scene: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    total, rows = await list_conversation_runs(
        session,
        since=since,
        scene=scene,
        status=status,
        limit=limit,
        offset=offset,
    )
    records = []
    title_map = await load_chat_session_titles([run.session_id for run in rows])
    for run in rows:
        summary = conversation_run_summary(run, session_title=title_map.get(run.session_id))
        span_count = (await session.execute(
            select(func.count()).select_from(TraceSpan).where(TraceSpan.run_id == run.id)
        )).scalar() or 0
        records.append({
            **summary,
            "input_preview": (run.raw_json or {}).get("user_message") if isinstance(run.raw_json, dict) else None,
            "output_preview": (run.raw_json or {}).get("final_answer_preview") if isinstance(run.raw_json, dict) else None,
            "span_count": int(span_count),
            "score": summary.get("overall_quality"),
            "cost": None,
        })
    return {"total": total, "limit": limit, "offset": offset, "records": records}


def evaluator_summary(row: EvaluatorDefinition) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "type": row.evaluator_type,
        "version": row.version,
        "status": row.status,
        "rubric": row.rubric,
        "prompt": row.prompt,
        "code": row.code,
        "threshold": row.threshold,
        "owner": row.owner,
        "metadata": row.metadata_json or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def list_evaluator_definitions(session: AsyncSession) -> list[dict[str, Any]]:
    await ensure_default_eval_hub_seed(session)
    rows = (await session.execute(
        select(EvaluatorDefinition).order_by(EvaluatorDefinition.name, desc(EvaluatorDefinition.created_at))
    )).scalars().all()
    return [evaluator_summary(row) for row in rows]


async def create_evaluator_definition(session: AsyncSession, payload: dict[str, Any], owner: str | None) -> dict[str, Any]:
    item = EvaluatorDefinition(
        id=str(uuid4()),
        name=str(payload.get("name") or "custom_evaluator"),
        evaluator_type=str(payload.get("type") or payload.get("evaluator_type") or "rule"),
        version=str(payload.get("version") or "v1"),
        status=str(payload.get("status") or "active"),
        rubric=payload.get("rubric"),
        prompt=payload.get("prompt"),
        code=payload.get("code"),
        threshold=float(payload["threshold"]) if payload.get("threshold") is not None else None,
        owner=owner,
        metadata_json=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
    )
    session.add(item)
    await session.flush()
    return evaluator_summary(item)


def experiment_summary(row: Experiment) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "project": row.project,
        "dataset_name": row.dataset_name,
        "dataset_version": row.dataset_version,
        "evaluator_suite": row.evaluator_suite,
        "status": row.status,
        "owner": row.owner,
        "tags": row.tags_json or [],
        "metadata": row.metadata_json or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def list_experiments(session: AsyncSession, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    total = (await session.execute(select(func.count()).select_from(Experiment))).scalar() or 0
    rows = (await session.execute(
        select(Experiment).order_by(desc(Experiment.created_at)).offset(offset).limit(limit)
    )).scalars().all()
    return {"total": int(total), "records": [experiment_summary(row) for row in rows]}


async def create_experiment(session: AsyncSession, payload: dict[str, Any], owner: str | None) -> dict[str, Any]:
    item = Experiment(
        id=str(uuid4()),
        name=str(payload.get("name") or "Untitled experiment"),
        description=payload.get("description"),
        project=str(payload.get("project") or "smart-eats"),
        dataset_name=payload.get("dataset_name") or payload.get("dataset"),
        dataset_version=payload.get("dataset_version"),
        evaluator_suite=payload.get("evaluator_suite"),
        status=str(payload.get("status") or "draft"),
        owner=owner,
        tags_json=payload.get("tags") if isinstance(payload.get("tags"), list) else [],
        metadata_json=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
    )
    session.add(item)
    await session.flush()
    return experiment_summary(item)


async def load_experiment(session: AsyncSession, experiment_id: str) -> dict[str, Any] | None:
    exp = await session.scalar(select(Experiment).where(Experiment.id == experiment_id))
    if not exp:
        return None
    runs = (await session.execute(
        select(ExperimentRun).where(ExperimentRun.experiment_id == experiment_id).order_by(desc(ExperimentRun.created_at))
    )).scalars().all()
    return {
        "experiment": experiment_summary(exp),
        "runs": [experiment_run_summary(row) for row in runs],
    }


def experiment_run_summary(row: ExperimentRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "experiment_id": row.experiment_id,
        "eval_run_id": row.eval_run_id,
        "report_name": row.report_name,
        "role": row.role,
        "agent_version": row.agent_version,
        "prompt_version": row.prompt_version,
        "model_name": row.model_name,
        "tool_version": row.tool_version,
        "notes": row.notes,
        "metadata": row.metadata_json or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def add_experiment_run(session: AsyncSession, experiment_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    exp = await session.scalar(select(Experiment).where(Experiment.id == experiment_id))
    if not exp:
        return None
    item = ExperimentRun(
        id=str(uuid4()),
        experiment_id=experiment_id,
        eval_run_id=payload.get("eval_run_id"),
        report_name=payload.get("report_name"),
        role=str(payload.get("role") or "candidate"),
        agent_version=payload.get("agent_version"),
        prompt_version=payload.get("prompt_version"),
        model_name=payload.get("model_name"),
        tool_version=payload.get("tool_version"),
        notes=payload.get("notes"),
        metadata_json=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
    )
    session.add(item)
    await session.flush()
    return experiment_run_summary(item)


async def create_playground_run(session: AsyncSession, payload: dict[str, Any], owner: str | None) -> dict[str, Any]:
    input_text = str(payload.get("input") or payload.get("input_text") or "")
    outputs = payload.get("outputs") if isinstance(payload.get("outputs"), list) else []
    item = PlaygroundRun(
        id=str(uuid4()),
        project=str(payload.get("project") or "smart-eats"),
        input_text=input_text,
        config_json=payload.get("config") if isinstance(payload.get("config"), dict) else {},
        outputs_json=outputs,
        scores_json=payload.get("scores") if isinstance(payload.get("scores"), dict) else {},
        trace_json=payload.get("trace") if isinstance(payload.get("trace"), dict) else {},
        owner=owner,
    )
    session.add(item)
    await session.flush()
    return playground_run_summary(item)


def playground_run_summary(row: PlaygroundRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "project": row.project,
        "input": row.input_text,
        "config": row.config_json or {},
        "outputs": row.outputs_json or [],
        "scores": row.scores_json or {},
        "trace": row.trace_json or {},
        "owner": row.owner,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def load_playground_run(session: AsyncSession, run_id: str) -> dict[str, Any] | None:
    row = await session.scalar(select(PlaygroundRun).where(PlaygroundRun.id == run_id))
    return playground_run_summary(row) if row else None


def simulation_scenario_summary(row: SimulationScenario) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "project": row.project,
        "scenario": row.scenario_json,
        "status": row.status,
        "owner": row.owner,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def list_simulation_scenarios(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (await session.execute(
        select(SimulationScenario).order_by(desc(SimulationScenario.created_at))
    )).scalars().all()
    return [simulation_scenario_summary(row) for row in rows]


async def create_simulation_scenario(session: AsyncSession, payload: dict[str, Any], owner: str | None) -> dict[str, Any]:
    item = SimulationScenario(
        id=str(uuid4()),
        name=str(payload.get("name") or "Untitled simulation"),
        description=payload.get("description"),
        project=str(payload.get("project") or "smart-eats"),
        scenario_json=payload.get("scenario") if isinstance(payload.get("scenario"), dict) else {
            "simulated_user_profile": payload.get("simulated_user_profile"),
            "initial_state": payload.get("initial_state") or {},
            "success_criteria": payload.get("success_criteria") or [],
            "allowed_tools": payload.get("allowed_tools") or [],
            "max_turns": payload.get("max_turns") or 5,
        },
        status=str(payload.get("status") or "draft"),
        owner=owner,
    )
    session.add(item)
    await session.flush()
    return simulation_scenario_summary(item)


async def create_simulation_run(session: AsyncSession, scenario_id: str) -> dict[str, Any] | None:
    scenario = await session.scalar(select(SimulationScenario).where(SimulationScenario.id == scenario_id))
    if not scenario:
        return None
    item = SimulationRun(
        id=str(uuid4()),
        scenario_id=scenario_id,
        status="queued",
        result_json={"message": "Simulation runner is queued for future worker execution."},
        scores_json={},
    )
    session.add(item)
    await session.flush()
    return {
        "id": item.id,
        "scenario_id": item.scenario_id,
        "status": item.status,
        "result": item.result_json or {},
        "scores": item.scores_json or {},
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "finished_at": item.finished_at.isoformat() if item.finished_at else None,
    }


def conversation_run_summary(run: ConversationRun, *, session_title: str | None = None) -> dict[str, Any]:
    raw = run.raw_json if isinstance(run.raw_json, dict) else {}
    failure_class = raw.get("failure_class")
    if not isinstance(failure_class, str) or not failure_class:
        failure_class = classify_failure(error=raw.get("error"), error_reason=raw.get("error_reason"))
    title = _conversation_session_title(run, session_title)
    model_config = _extract_runtime_model_config(raw)
    return {
        "id": run.id,
        "session_id": run.session_id,
        "session_title": title,
        "title": title,
        "user_id": run.user_id,
        "trace_id": run.trace_id,
        "scene": run.scene,
        "worker": run.worker,
        "agent_id": run.worker,
        "model_provider": run.model_provider,
        "model_name": run.model_name,
        "model_config": model_config,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "ended_at": run.ended_at.isoformat() if run.ended_at else None,
        "timestamp": run.ended_at.isoformat() if run.ended_at else (run.started_at.isoformat() if run.started_at else None),
        "latency_ms": run.latency_ms,
        "total_duration_ms": run.latency_ms,
        "final_state": run.final_state,
        "is_fallback": run.is_fallback,
        "overall_quality": float(raw.get("overall_quality") or 0.0),
        "efficiency": float(raw.get("efficiency") or 0.0),
        "schema_compliance": float(raw.get("schema_compliance") or 0.0),
        "no_fallback": float(raw.get("no_fallback") or (0.0 if run.is_fallback else 1.0)),
        "has_content_score": float(raw.get("has_content_score") or (1.0 if raw.get("has_content") else 0.0)),
        "no_leak": float(raw.get("no_leak") or 1.0),
        "has_content": bool(raw.get("has_content")),
        "tool_call_count": int(raw.get("tool_call_count") or 0),
        "tool_names": raw.get("tool_names") if isinstance(raw.get("tool_names"), list) else [],
        "repeated_action_rate": float(raw.get("repeated_action_rate") or 0.0),
        "error": raw.get("error"),
        "error_reason": raw.get("error_reason"),
        "failure_class": failure_class,
    }


def trace_event_summary(event: ConversationTraceEvent) -> dict[str, Any]:
    return {
        "index": event.event_index,
        "event_type": event.event_type,
        "timestamp": event.timestamp,
        "tool_name": event.tool_name,
        "duration_ms": event.duration_ms,
        "data": event.data_json or {},
    }


def tool_call_summary(tool: ConversationToolCall) -> dict[str, Any]:
    return {
        "tool_name": tool.tool_name,
        "args": tool.args_json or {},
        "success": tool.success,
        "error_reason": tool.error_reason,
        "latency_ms": tool.latency_ms,
        "cost": tool.cost,
        "failure_class": classify_failure(error_reason=tool.error_reason, tool_name=tool.tool_name) if not tool.success else "none",
    }


def human_review_summary(review: ConversationHumanReview) -> dict[str, Any]:
    return {
        "run_id": review.run_id,
        "reviewer_id": review.reviewer_id,
        "decision": review.decision,
        "reason": review.reason,
        "failure_reason": review.failure_reason,
        "failure_tags": review.failure_tags_json or [],
        "corrected_answer": review.corrected_answer,
        "expected_behavior": review.expected_behavior,
        "review_confidence": review.review_confidence,
        "dataset_candidate": review.dataset_candidate,
        "notes": review.notes,
        "created_at": review.created_at.isoformat() if review.created_at else None,
        "updated_at": review.updated_at.isoformat() if review.updated_at else None,
    }


def cost_summary(cost: ConversationCost) -> dict[str, Any]:
    return {
        "token_input": int(cost.token_input or 0),
        "token_output": int(cost.token_output or 0),
        "cached_tokens": int(cost.cached_tokens or 0),
        "reasoning_tokens": int(cost.reasoning_tokens or 0),
        "total_tokens": int(cost.total_tokens or 0),
        "provider": cost.provider,
        "model_name": cost.model_name,
        "token_cost": float(cost.token_cost or 0.0),
        "tool_cost": float(cost.tool_cost or 0.0),
        "total_cost": float(cost.total_cost or 0.0),
        "cost_estimated": bool(cost.cost_estimated),
        "pricing": cost.pricing_json or {},
    }


async def aggregate_monitoring_overview(session: AsyncSession, *, since: datetime) -> dict[str, Any]:
    runs = (await session.execute(select(ConversationRun).where(ConversationRun.started_at >= since))).scalars().all()
    metrics = (await session.execute(
        select(ConversationMetric).where(ConversationMetric.run_id.in_([run.id for run in runs])) if runs else select(ConversationMetric).where(false())
    )).scalars().all()
    costs = (await session.execute(
        select(ConversationCost).where(ConversationCost.run_id.in_([run.id for run in runs])) if runs else select(ConversationCost).where(false())
    )).scalars().all()
    by_metric: dict[str, list[float]] = {}
    for metric in metrics:
        by_metric.setdefault(metric.metric_name, []).append(float(metric.metric_value or 0.0))
    latencies = [float(run.latency_ms or 0.0) for run in runs]
    total_token_cost = sum(float(cost.token_cost or 0.0) for cost in costs)
    total_tool_cost = sum(float(cost.tool_cost or 0.0) for cost in costs)
    token_input = sum(int(cost.token_input or 0) for cost in costs)
    token_output = sum(int(cost.token_output or 0) for cost in costs)
    return {
        "total_runs": len(runs),
        "task_success_proxy": _avg(by_metric.get("task_success_proxy", [])),
        "fallback_rate": _avg(by_metric.get("fallback_rate", [])),
        "tool_error_rate": _avg(by_metric.get("tool_error_rate", [])),
        "provider_error_rate": _avg(by_metric.get("provider_error_rate", [])),
        "tool_timeout_rate": _avg(by_metric.get("tool_timeout_rate", [])),
        "tool_call_accuracy_proxy": _avg(by_metric.get("tool_call_accuracy_proxy", [])),
        "latency_p50_ms": round(percentile(latencies, 0.50), 0),
        "latency_p95_ms": round(percentile(latencies, 0.95), 0),
        "latency_p99_ms": round(percentile(latencies, 0.99), 0),
        "token_input": token_input,
        "token_output": token_output,
        "token_cost": round(total_token_cost, 4),
        "tool_cost": round(total_tool_cost, 4),
        "total_cost": round(total_token_cost + total_tool_cost, 4),
        "secret_leak_rate": _avg(by_metric.get("secret_leak_rate", [])),
        "policy_violation_rate": _avg(by_metric.get("policy_violation_rate", [])),
        "human_escalation_rate": _avg(by_metric.get("human_escalation_rate", [])),
        "cache_hit_rate": _avg(by_metric.get("cache_hit_rate", [])),
        "unsafe_action_block_rate": _avg(by_metric.get("unsafe_action_block_rate", [])),
        "avg_steps": _avg(by_metric.get("avg_steps", [])),
        "repeated_action_rate": _avg(by_metric.get("repeated_action_rate", [])),
        "recovery_rate": _avg(by_metric.get("recovery_rate", [])),
    }


async def aggregate_failures(session: AsyncSession, *, since: datetime) -> dict[str, Any]:
    runs = (await session.execute(select(ConversationRun).where(ConversationRun.started_at >= since))).scalars().all()
    run_ids = [run.id for run in runs]
    tools = (await session.execute(
        select(ConversationToolCall).where(ConversationToolCall.run_id.in_(run_ids)) if run_ids else select(ConversationToolCall).where(false())
    )).scalars().all()
    metrics = (await session.execute(
        select(ConversationMetric).where(ConversationMetric.run_id.in_(run_ids)) if run_ids else select(ConversationMetric).where(false())
    )).scalars().all()

    by_failure_class: dict[str, int] = {
        "provider_auth": 0,
        "provider_timeout": 0,
        "provider_rate_limit": 0,
        "provider_model_error": 0,
        "tool_api_error": 0,
        "tool_timeout": 0,
        "tool_empty_result": 0,
        "tool_bad_args": 0,
        "agent_routing_error": 0,
        "agent_low_quality": 0,
        "agent_schema_error": 0,
        "safety_policy_violation": 0,
        "eval_framework_error": 0,
    }
    by_scene: dict[str, int] = {}
    by_worker: dict[str, int] = {}
    by_tool: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_metric: dict[str, int] = {}

    metric_by_run: dict[str, dict[str, float]] = {}
    for metric in metrics:
        metric_by_run.setdefault(metric.run_id or "", {})[metric.metric_name] = _safe_float(metric.metric_value)

    for run in runs:
        raw = run.raw_json if isinstance(run.raw_json, dict) else {}
        metrics_for_run = metric_by_run.get(run.id, {})
        failure_class = raw.get("failure_class")
        if not isinstance(failure_class, str) or not failure_class:
            failure_class = classify_failure(
                error=raw.get("error"),
                error_reason=raw.get("error_reason"),
                metrics=metrics_for_run,
            )
        if failure_class != "none":
            by_failure_class[failure_class] = by_failure_class.get(failure_class, 0) + 1
            if run.scene:
                by_scene[run.scene] = by_scene.get(run.scene, 0) + 1
            if run.worker:
                by_worker[run.worker] = by_worker.get(run.worker, 0) + 1
        by_status[run.status] = by_status.get(run.status, 0) + 1
        for name, value in metrics_for_run.items():
            if value < 0.8 and name in {"task_success_proxy", "schema_compliance", "no_leak", "overall_quality", "recovery_rate"}:
                by_metric[name] = by_metric.get(name, 0) + 1

    for tool in tools:
        if not tool.success:
            by_tool[tool.tool_name] = by_tool.get(tool.tool_name, 0) + 1
            failure_class = classify_failure(error_reason=tool.error_reason, tool_name=tool.tool_name)
            by_failure_class[failure_class] = by_failure_class.get(failure_class, 0) + 1

    return {
        "total_runs": len(runs),
        "by_failure_class": by_failure_class,
        "by_scene": by_scene,
        "by_worker": by_worker,
        "by_tool": by_tool,
        "by_status": by_status,
        "by_metric": by_metric,
    }


async def aggregate_cost_latency(session: AsyncSession, *, since: datetime) -> dict[str, Any]:
    runs = (await session.execute(select(ConversationRun).where(ConversationRun.started_at >= since))).scalars().all()
    run_ids = [run.id for run in runs]
    costs = (await session.execute(
        select(ConversationCost).where(ConversationCost.run_id.in_(run_ids)) if run_ids else select(ConversationCost).where(false())
    )).scalars().all()
    metrics = (await session.execute(
        select(ConversationMetric).where(ConversationMetric.run_id.in_(run_ids)) if run_ids else select(ConversationMetric).where(false())
    )).scalars().all()
    by_metric: dict[str, list[float]] = {}
    for metric in metrics:
        by_metric.setdefault(metric.metric_name, []).append(_safe_float(metric.metric_value))
    latencies = [_safe_float(run.latency_ms) for run in runs]
    return {
        "total_runs": len(runs),
        "latency_p50_ms": round(percentile(latencies, 0.50), 0),
        "latency_p95_ms": round(percentile(latencies, 0.95), 0),
        "latency_p99_ms": round(percentile(latencies, 0.99), 0),
        "latency_avg_ms": round(_avg(latencies), 0),
        "token_input": sum(int(cost.token_input or 0) for cost in costs),
        "token_output": sum(int(cost.token_output or 0) for cost in costs),
        "cached_tokens": sum(int(cost.cached_tokens or 0) for cost in costs),
        "reasoning_tokens": sum(int(cost.reasoning_tokens or 0) for cost in costs),
        "total_tokens": sum(int(cost.total_tokens or 0) for cost in costs),
        "token_cost": round(sum(_safe_float(cost.token_cost) for cost in costs), 4),
        "tool_cost": round(sum(_safe_float(cost.tool_cost) for cost in costs), 4),
        "total_cost": round(sum(_safe_float(cost.total_cost) for cost in costs), 4),
        "cache_hit_rate": _avg(by_metric.get("cache_hit_rate", [])),
        "by_provider": _cost_group(costs, key=lambda item: item.provider or "unknown"),
        "by_model": _cost_group(costs, key=lambda item: item.model_name or "unknown"),
    }


def _cost_group(costs: list[ConversationCost], *, key) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for cost in costs:
        name = str(key(cost) or "unknown")
        item = grouped.setdefault(
            name,
            {
                "runs": 0,
                "token_input": 0,
                "token_output": 0,
                "total_tokens": 0,
                "token_cost": 0.0,
                "tool_cost": 0.0,
                "total_cost": 0.0,
            },
        )
        item["runs"] += 1
        item["token_input"] += int(cost.token_input or 0)
        item["token_output"] += int(cost.token_output or 0)
        item["total_tokens"] += int(cost.total_tokens or 0)
        item["token_cost"] = round(float(item["token_cost"]) + _safe_float(cost.token_cost), 6)
        item["tool_cost"] = round(float(item["tool_cost"]) + _safe_float(cost.tool_cost), 6)
        item["total_cost"] = round(float(item["total_cost"]) + _safe_float(cost.total_cost), 6)
    return grouped


async def aggregate_safety(session: AsyncSession, *, since: datetime) -> dict[str, Any]:
    runs = (await session.execute(select(ConversationRun).where(ConversationRun.started_at >= since))).scalars().all()
    run_ids = [run.id for run in runs]
    metrics = (await session.execute(
        select(ConversationMetric).where(ConversationMetric.run_id.in_(run_ids)) if run_ids else select(ConversationMetric).where(false())
    )).scalars().all()
    by_metric: dict[str, list[float]] = {}
    for metric in metrics:
        by_metric.setdefault(metric.metric_name, []).append(_safe_float(metric.metric_value))
    return {
        "total_runs": len(runs),
        "unsafe_action_block_rate": _avg(by_metric.get("unsafe_action_block_rate", [])),
        "secret_leak_rate": _avg(by_metric.get("secret_leak_rate", [])),
        "policy_violation_rate": _avg(by_metric.get("policy_violation_rate", [])),
        "human_escalation_rate": _avg(by_metric.get("human_escalation_rate", [])),
        "no_leak": _avg(by_metric.get("no_leak", [])),
    }


async def calculate_judge_human_agreement(
    session: AsyncSession,
    *,
    since: datetime | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """计算 LLM Judge 与 Human Review 的一致性指标.

    比较逻辑：
    - Human decision=accepted 对应 Judge score >= 0.5 (positive)
    - Human decision=rejected 对应 Judge score < 0.5 (negative)
    - 仅比较同时有 judge score 和 human review 的 case

    返回指标：
    - agreement_rate: 总一致率
    - false_positive_rate: Judge 认为 good 但 Human 认为差
    - false_negative_rate: Judge 认为差 但 Human 认为 good
    - total_compared: 比较总数
    - by_dimension: 按评分维度分组的一致性
    """
    # 查找同时有 judge score 和 human review 的 EvalRun
    from app.infra.models.eval import EvalScore, EvalTrial, EvalCase as EvalCaseModel

    # 如果指定了 run_id，查该 run 的 cases
    if run_id:
        eval_run = await session.scalar(select(EvalRun).where(EvalRun.id == run_id))
        if not eval_run:
            return {"error": "run not found", "total_compared": 0}
        since = eval_run.created_at - timedelta(days=1) if eval_run.created_at else parse_window_start("30d")

    if since is None:
        since = parse_window_start("30d")

    # 查询有 human review 的 conversation runs
    reviewed_runs = (await session.execute(
        select(ConversationRun, ConversationHumanReview)
        .join(ConversationHumanReview, ConversationHumanReview.run_id == ConversationRun.id)
        .where(ConversationRun.started_at >= since)
        .where(ConversationHumanReview.decision.in_(["accepted", "rejected"]))
    )).all()

    if not reviewed_runs:
        return {
            "agreement_rate": None,
            "false_positive_rate": None,
            "false_negative_rate": None,
            "total_compared": 0,
            "by_dimension": {},
            "window_start": since.isoformat(),
        }

    # 获取对应的 realtime eval scores（存储在 EvalRun.raw_report_json 中）
    # 对于在线对话，judge 分数在 ConversationMetric 中
    run_ids = [run.id for run, _ in reviewed_runs]
    metrics = (await session.execute(
        select(ConversationMetric).where(ConversationMetric.run_id.in_(run_ids))
    )).scalars().all()

    # 构建 run_id -> metrics 映射
    metrics_by_run: dict[str, dict[str, float]] = {}
    for m in metrics:
        metrics_by_run.setdefault(m.run_id, {})[m.metric_name] = _safe_float(m.metric_value)

    # Judge 维度映射到 metric name
    judge_dims = [
        "answer_relevance", "actionability", "hallucination_control",
        "constraint_adherence", "tool_call_reasonableness", "safety_compliance",
    ]

    # 比较每个 run
    total = 0
    agreements = 0
    false_positives = 0
    false_negatives = 0
    by_dim: dict[str, dict[str, int]] = {dim: {"agreement": 0, "total": 0} for dim in judge_dims}

    for run, review in reviewed_runs:
        run_metrics = metrics_by_run.get(run.id, {})
        # 检查是否有 judge 分数
        has_judge = any(run_metrics.get(dim) is not None for dim in judge_dims)
        if not has_judge:
            continue

        total += 1
        human_positive = review.decision == "accepted"

        # 使用各维度平均分作为总体 judge 判断
        judge_scores = [run_metrics.get(dim, 0.5) for dim in judge_dims if run_metrics.get(dim) is not None]
        avg_judge = sum(judge_scores) / len(judge_scores) if judge_scores else 0.5
        judge_positive = avg_judge >= 0.5

        if human_positive == judge_positive:
            agreements += 1
        elif judge_positive and not human_positive:
            false_positives += 1
        else:
            false_negatives += 1

        # 按维度统计
        for dim in judge_dims:
            dim_score = run_metrics.get(dim)
            if dim_score is not None:
                by_dim[dim]["total"] += 1
                dim_positive = dim_score >= 0.5
                if human_positive == dim_positive:
                    by_dim[dim]["agreement"] += 1

    if total == 0:
        return {
            "agreement_rate": None,
            "false_positive_rate": None,
            "false_negative_rate": None,
            "total_compared": 0,
            "by_dimension": {},
            "window_start": since.isoformat(),
        }

    # 计算各维度 agreement rate
    by_dim_rates: dict[str, Any] = {}
    for dim, stats in by_dim.items():
        if stats["total"] > 0:
            by_dim_rates[dim] = {
                "agreement_rate": round(stats["agreement"] / stats["total"], 4),
                "total": stats["total"],
            }
        else:
            by_dim_rates[dim] = {"agreement_rate": None, "total": 0}

    return {
        "agreement_rate": round(agreements / total, 4),
        "false_positive_rate": round(false_positives / total, 4),
        "false_negative_rate": round(false_negatives / total, 4),
        "total_compared": total,
        "by_dimension": by_dim_rates,
        "window_start": since.isoformat(),
    }
