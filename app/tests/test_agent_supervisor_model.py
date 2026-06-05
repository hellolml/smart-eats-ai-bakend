from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from app.agent.supervisor.model import PlannerChatModel


class _FakePlanner:
    def __init__(self):
        self.calls = []

    async def ainvoke_with_tools(self, messages, tools, image_parts=None):
        from langchain_core.messages import AIMessage

        self.calls.append({"messages": messages, "tools": tools})
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": tools[0].name,
                    "args": {},
                    "id": "call_1",
                    "type": "tool_call",
                }
            ],
        )


@tool
def sample_tool() -> str:
    """Sample tool."""
    return "ok"


@pytest.mark.asyncio
async def test_planner_chat_model_binds_tools_and_normalizes_tool_calls():
    planner = _FakePlanner()
    model = PlannerChatModel(planner=planner).bind_tools([sample_tool])

    message = await model.ainvoke([HumanMessage(content="call the tool")])

    assert message.tool_calls[0]["name"] == "sample_tool"
    assert planner.calls[0]["tools"][0].name == "sample_tool"


@pytest.mark.asyncio
async def test_planner_chat_model_sync_generation_raises_inside_event_loop():
    model = PlannerChatModel(planner=_FakePlanner()).bind_tools([sample_tool])

    with pytest.raises(NotImplementedError):
        model._generate([HumanMessage(content="call the tool")])
