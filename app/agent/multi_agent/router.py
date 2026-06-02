from __future__ import annotations

from typing import Any

from app.agent.multi_agent.base import AgentTurnContext, BaseAgent, PreparedAgentTurn
from app.agent.multi_agent.food import FoodDecisionAgent
from app.agent.multi_agent.travel import TravelPlanAgent


class AgentRouter:
    def __init__(self, agents: list[BaseAgent] | None = None) -> None:
        self.agents: list[BaseAgent] = agents or [TravelPlanAgent(), FoodDecisionAgent()]

    async def prepare_turn(
        self,
        *,
        session_id: str,
        user_id: str | None,
        payload: dict[str, Any],
        latest_final_json: dict[str, Any] | None = None,
    ) -> PreparedAgentTurn:
        agent = self.resolve(payload)
        if agent is None:
            return PreparedAgentTurn(
                payload=dict(payload),
                agent_id="chat",
                plan_type=None,
                context_overrides=_context_overrides(payload),
            )
        return await agent.prepare_turn(
            AgentTurnContext(
                session_id=session_id,
                user_id=user_id,
                payload=payload,
                latest_final_json=latest_final_json,
            )
        )

    def resolve(self, payload: dict[str, Any]) -> BaseAgent | None:
        for agent in self.agents:
            if agent.matches(payload):
                return agent
        return None


def _context_overrides(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("client_context_overrides")
    return dict(value) if isinstance(value, dict) else {}
