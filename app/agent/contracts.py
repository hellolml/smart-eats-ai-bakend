from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agent.runtime.finalization import fallback_final, normalize_final_answer


AgentRunStatus = Literal["completed", "needs_clarification", "failed", "blocked"]
AgentWorkerName = Literal[
    "travel_planner",
    "food_advisor",
    "route_planner",
    "home_chef",
    "general_chat",
]
AgentFailureClass = Literal[
    "route_no_worker",
    "model_no_tool_call",
    "worker_no_final",
    "tool_error",
    "schema_invalid",
    "context_missing",
    "execution_loop",
    "upstream_error",
]


BUSINESS_PAYLOAD_KEYS = (
    "state",
    "await_confirmation",
    "trip_meta",
    "sources",
    "places",
    "candidates",
    "failed_places",
    "itinerary",
    "map",
    "raw_text",
    "scene",
    "agent_id",
    "plan_type",
)


class AgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    session_id: str
    user_id: str | None = None
    message: str | None = None
    scene: str = "chat"
    intent: str | None = None
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    client_context: dict[str, Any] = Field(default_factory=dict)
    model_config_payload: dict[str, Any] = Field(default_factory=dict, alias="model_config")


class AgentRouteDecision(BaseModel):
    model_config = ConfigDict(extra="allow")

    worker: AgentWorkerName
    intent: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    reason: str
    required_context: list[str] = Field(default_factory=list)
    blocked_reason: str | None = None


class AgentDiagnostics(BaseModel):
    model_config = ConfigDict(extra="allow")

    route: dict[str, Any] | None = None
    worker: str | None = None
    tools: list[dict[str, Any]] = Field(default_factory=list)
    active_tools: list[str] = Field(default_factory=list)
    active_skills: list[dict[str, Any]] = Field(default_factory=list)
    schema_valid: bool = True
    fallback_reason: str | None = None
    failure_class: AgentFailureClass | None = None


class AgentRunResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: AgentRunStatus
    final: dict[str, Any] | None = None
    diagnostics: AgentDiagnostics = Field(default_factory=AgentDiagnostics)
    business_payload: dict[str, Any] = Field(default_factory=dict)
    failure_class: AgentFailureClass | None = None
    worker: AgentWorkerName | str | None = None
    trace_id: str | None = None


def final_json_for_failure(
    failure_class: AgentFailureClass,
    *,
    message: str | None = None,
) -> dict[str, Any]:
    final = fallback_final()
    if message:
        final["recommendations"][0]["title"] = message
    final["failure_class"] = failure_class
    final["status"] = "failed"
    return final


def is_fallback_final(final_json: dict[str, Any] | None) -> bool:
    recs = final_json.get("recommendations") if isinstance(final_json, dict) else None
    if not isinstance(recs, list):
        return False
    return any(isinstance(item, dict) and str(item.get("reason") or "") == "fallback" for item in recs)


def infer_failure_class(final_json: dict[str, Any] | None) -> AgentFailureClass | None:
    if not isinstance(final_json, dict):
        return "worker_no_final"
    value = final_json.get("failure_class")
    known = set(AgentFailureClass.__args__)  # type: ignore[attr-defined]
    if isinstance(value, str) and value in known:
        return value  # type: ignore[return-value]
    if is_fallback_final(final_json):
        return "worker_no_final"
    return None


def business_payload_from_final(final_json: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(final_json, dict):
        return {}
    return {
        key: final_json.get(key)
        for key in BUSINESS_PAYLOAD_KEYS
        if final_json.get(key) not in (None, [], {})
    }


def build_agent_run_result(
    *,
    final_json: dict[str, Any] | None,
    route_decision: dict[str, Any] | AgentRouteDecision | None = None,
    worker: str | None = None,
    trace_id: str | None = None,
    diagnostics: dict[str, Any] | AgentDiagnostics | None = None,
    failure_class: AgentFailureClass | None = None,
    status: AgentRunStatus | None = None,
    business_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_final = normalize_final_answer(final_json) if isinstance(final_json, dict) else None
    inferred_failure = failure_class or infer_failure_class(normalized_final)
    final_status = normalized_final.get("status") if isinstance(normalized_final, dict) else None
    resolved_status: AgentRunStatus = (
        status
        or ("failed" if inferred_failure else None)
        or (final_status if final_status in {"needs_clarification", "blocked"} else None)
        or "completed"
    )

    route_payload = (
        route_decision.model_dump()
        if isinstance(route_decision, AgentRouteDecision)
        else route_decision
        if isinstance(route_decision, dict)
        else None
    )
    routed_worker = None
    if isinstance(route_payload, dict):
        value = route_payload.get("worker")
        routed_worker = value if isinstance(value, str) and value else None

    diag_payload = (
        diagnostics.model_dump()
        if isinstance(diagnostics, AgentDiagnostics)
        else dict(diagnostics or {})
        if isinstance(diagnostics, dict)
        else {}
    )
    existing_diag_worker = diag_payload.get("worker")
    if routed_worker:
        if isinstance(existing_diag_worker, str) and existing_diag_worker and existing_diag_worker != routed_worker:
            diag_payload.setdefault("agent_id", existing_diag_worker)
        if worker and worker != routed_worker:
            diag_payload.setdefault("agent_id", worker)
        diag_payload["worker"] = routed_worker
    elif worker and not existing_diag_worker:
        diag_payload["worker"] = worker
    if route_payload and diag_payload.get("route") is None:
        diag_payload["route"] = route_payload
    diag = AgentDiagnostics.model_validate(diag_payload)
    if inferred_failure:
        diag.failure_class = inferred_failure
        diag.fallback_reason = diag.fallback_reason or inferred_failure

    resolved_worker = routed_worker or worker

    result = AgentRunResult(
        status=resolved_status,
        final=normalized_final,
        diagnostics=diag,
        business_payload=business_payload or business_payload_from_final(normalized_final),
        failure_class=inferred_failure,
        worker=resolved_worker,
        trace_id=trace_id,
    )
    return result.model_dump(exclude_none=True)
