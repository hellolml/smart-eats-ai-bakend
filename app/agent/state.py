from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChatState:
    session_id: str
    user_id: str | None = None
    message: str | None = None
    trace_id: str | None = None
    scene: str = "chat"
    context_overrides: dict[str, Any] | None = None
    snapshot: dict[str, Any] | None = None
    context: dict[str, Any] | None = None
    thought: str | None = None
    steps_left: int = 0
    turn_index: int = 0
    step_index: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    pending_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results_batch: list[dict[str, Any]] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
    final_json: dict[str, Any] | None = None
    planner_retry_count: int = 0
    action: Any | None = None
    provider: str | None = None
    agent_type: str | None = None
    client_ip: str | None = None
    intent: str | None = None
    intent_confidence: float = 0.0
    intent_slots: dict[str, Any] = field(default_factory=dict)
    intent_need_clarify: bool = False
    intent_clarify_question: str | None = None
    location_source: str | None = None
    task_stage: str | None = None
    tool_plan: list[dict[str, Any]] = field(default_factory=list)
    recovery_path: list[str] = field(default_factory=list)
    resume_from_checkpoint: bool = False
    checkpoint_ref: str | None = None
    replay_from_checkpoint: bool = False
    resume_payload: dict[str, Any] | None = None
    last_user_message: str | None = None
    user_message_logged: bool = False
    history: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
