from __future__ import annotations

from typing import Any, TypedDict

from pydantic import BaseModel, ConfigDict, Field


class AgentContext(BaseModel):
    model_config = ConfigDict(extra="allow")

    intent: str | None = None
    agent_id: str | None = None
    plan_type: str | None = None
    forced_skill_ids: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    ui_scene: str | None = None
    user_preference_md: dict[str, Any] | None = None
    food_profile: dict[str, Any] = Field(default_factory=dict)
    travel_food_preferences: dict[str, Any] = Field(default_factory=dict)
    travel_food_preference_summary: str | None = None
    latest_travel_final_json: dict[str, Any] | None = None
    plan_agent: dict[str, Any] | None = None
    action: str | None = None
    payload: dict[str, Any] | None = None
    travel_payload: dict[str, Any] | None = None
    travel_refresh_sources: bool = False


class AgentState(BaseModel):
    model_config = ConfigDict(extra="allow")

    session_id: str
    user_id: str | None = None
    message: str | None = None
    trace_id: str | None = None
    scene: str = "chat"
    agent_id: str | None = None
    plan_type: str | None = None
    context_overrides: dict[str, Any] | None = None
    snapshot: dict[str, Any] | None = None
    context: dict[str, Any] | None = None
    steps_left: int = 0
    turn_index: int = 0
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    pending_tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    observations: list[dict[str, Any]] = Field(default_factory=list)
    final_json: dict[str, Any] | None = None
    provider: str | None = None
    resolved_model_config: dict[str, Any] | None = None
    client_ip: str | None = None
    resume_from_checkpoint: bool = False
    checkpoint_ref: str | None = None
    replay_from_checkpoint: bool = False
    resume_payload: dict[str, Any] | None = None
    last_user_message: str | None = None
    user_message_logged: bool = False
    persist_user_message: bool = True
    history: list[dict[str, Any]] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    skill_state: dict[str, Any] = Field(default_factory=dict)
    summary: str | None = None
    context_budget: dict[str, Any] = Field(default_factory=dict)
    retrieved_memories: list[dict[str, Any]] = Field(default_factory=list)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    route_decision: dict[str, Any] | None = None
    agent_result: dict[str, Any] | None = None


AgentStateGraphSchema = TypedDict(
    "AgentStateGraphSchema",
    {name: field.annotation for name, field in AgentState.model_fields.items()},
    total=False,
)


def agent_context_from_mapping(value: Any) -> AgentContext | None:
    if isinstance(value, AgentContext):
        return value
    if isinstance(value, dict):
        return AgentContext.model_validate(value)
    return None


def empty_agent_context() -> AgentContext:
    return AgentContext()


def dump_agent_context(context: AgentContext) -> dict[str, Any]:
    return context.model_dump(exclude_none=True, exclude_defaults=True)


def merge_agent_context(base: Any, *overrides: Any) -> AgentContext:
    merged = dump_agent_context(agent_context_from_mapping(base) or empty_agent_context())
    for override in overrides:
        context = agent_context_from_mapping(override)
        if context is None:
            continue
        merged = _deep_merge_context(merged, dump_agent_context(context))
    return AgentContext.model_validate(merged)


def _deep_merge_context(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_context(merged[key], value)
        else:
            merged[key] = value
    return merged
