from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages

from app.agent.agent_state import AgentState


class AgentRuntimeState(AgentState):
    pass


@dataclass(frozen=True)
class AgentRuntimeConfig:
    name: str
    core_tool_names: list[str]
    max_steps: int = 6
    system_prompt_builder: Any = None


@dataclass(frozen=True)
class AgentRuntimeContext:
    redis_client: Any
    db: Any
    user_id: str | None
    context: dict[str, Any] | None
    session_id: str
    client_ip: str | None
    last_user_message: str | None
    servers_path: str | None

    def as_tool_payload(self) -> dict[str, Any]:
        return dict(self.__dict__)


AgentRuntimeGraphState = TypedDict(
    "AgentRuntimeGraphState",
    {
        "messages": Annotated[list[Any], add_messages],
        **{name: field.annotation for name, field in AgentRuntimeState.model_fields.items()},
        "runtime_context": dict[str, Any],
        "remaining_steps": int,
    },
    total=False,
)


_AGENT_RUNTIME_STATE_FIELDS = set(AgentRuntimeState.model_fields.keys())


def _state_from_dict(payload: dict[str, Any]) -> AgentRuntimeState:
    filtered = {key: value for key, value in payload.items() if key in _AGENT_RUNTIME_STATE_FIELDS}
    filtered["events"] = []
    return AgentRuntimeState.model_validate(filtered)


def _state_to_dict(state: AgentRuntimeState) -> dict[str, Any]:
    return state.model_dump()


def _state_update(state: AgentRuntimeState) -> dict[str, Any]:
    return _state_to_dict(state)
