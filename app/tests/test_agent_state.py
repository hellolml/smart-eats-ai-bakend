from __future__ import annotations

from app.agent.agent_state import AgentContext, AgentState, AgentStateGraphSchema, agent_context_from_mapping
from app.agent.runtime.graph import AgentRuntimeState
from app.agent.state import ChatState


def test_agent_context_serializes_known_and_extra_fields():
    context = AgentContext.model_validate(
        {
            "intent": "eat_out",
            "agent_id": "food_decision",
            "forced_skill_ids": ["food_decision"],
            "location_text": "恒伟星中心",
        }
    )

    payload = context.model_dump()

    assert payload["intent"] == "eat_out"
    assert payload["agent_id"] == "food_decision"
    assert payload["forced_skill_ids"] == ["food_decision"]
    assert payload["location_text"] == "恒伟星中心"


def test_agent_state_graph_schema_is_derived_from_agent_state_fields():
    assert AgentStateGraphSchema.__annotations__.keys() == AgentState.model_fields.keys()
    assert AgentStateGraphSchema.__total__ is False


def test_agent_state_preserves_context_object_and_extra_checkpoint_fields():
    state = AgentState.model_validate(
        {
            "session_id": "s1",
            "message": "继续",
            "context_overrides": {"plan_type": "travel"},
            "checkpoint_ref": "cp_1",
            "checkpoint_ns": "legacy",
        }
    )

    dumped = state.model_dump()

    assert dumped["session_id"] == "s1"
    assert dumped["context_overrides"]["plan_type"] == "travel"
    assert dumped["checkpoint_ref"] == "cp_1"
    assert dumped["checkpoint_ns"] == "legacy"


def test_agent_context_from_mapping_returns_none_for_unknown_input():
    assert agent_context_from_mapping({"intent": "route"}).intent == "route"
    assert agent_context_from_mapping(None) is None


def test_chat_and_runtime_state_share_pydantic_agent_state_base():
    chat_state = ChatState(session_id="s-chat")
    runtime_state = AgentRuntimeState(session_id="s-runtime")

    assert isinstance(chat_state, AgentState)
    assert isinstance(runtime_state, AgentState)
    assert chat_state.model_dump()["session_id"] == "s-chat"
    assert runtime_state.model_dump()["session_id"] == "s-runtime"
