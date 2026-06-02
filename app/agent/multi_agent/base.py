from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class AgentTurnContext:
    session_id: str
    user_id: str | None
    payload: dict[str, Any]
    latest_final_json: dict[str, Any] | None = None


@dataclass(frozen=True)
class PreparedAgentTurn:
    payload: dict[str, Any]
    agent_id: str
    plan_type: str | None = None
    context_overrides: dict[str, Any] = field(default_factory=dict)


class BaseAgent(Protocol):
    agent_id: str
    plan_type: str | None
    scenes: set[str]

    def matches(self, payload: dict[str, Any]) -> bool:
        ...

    async def prepare_turn(self, context: AgentTurnContext) -> PreparedAgentTurn:
        ...
