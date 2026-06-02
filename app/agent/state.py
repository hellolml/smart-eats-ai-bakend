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
    agent_id: str | None = None
    plan_type: str | None = None
    context_overrides: dict[str, Any] | None = None
    snapshot: dict[str, Any] | None = None
    context: dict[str, Any] | None = None
    steps_left: int = 0
    turn_index: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    pending_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
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
    history: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    summary: str | None = None
    context_budget: dict[str, Any] = field(default_factory=dict)
    retrieved_memories: list[dict[str, Any]] = field(default_factory=list)
    source_refs: list[dict[str, Any]] = field(default_factory=list)
