from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel, Field

from app.agent.llm_adapters import OpenAIPlanner
from app.agent.tools import ALL_TOOLS, describe_tools, select_tools, tool_names
from app.agent.tools.native import RuntimeContext


def test_all_tools_are_native_basetools_with_unique_names():
    assert ALL_TOOLS
    assert all(isinstance(tool, BaseTool) for tool in ALL_TOOLS)
    names = tool_names()
    assert len(names) == len(set(names))


def test_select_tools_respects_allowlist():
    tools = select_tools(["travel_search_poi", "memory_search"])

    assert [tool.name for tool in tools] == ["memory_search", "travel_search_poi"]


def test_food_decision_is_registered_as_native_tool():
    tools = select_tools(["food_decision"])

    assert [tool.name for tool in tools] == ["food_decision"]
    assert "food_decision" in tool_names()


def test_describe_tools_hides_runtime_context_from_model_schema():
    descriptions = describe_tools(["search_restaurants", "memory_search", "food_decision"])

    assert {item["name"] for item in descriptions} == {"search_restaurants", "memory_search", "food_decision"}
    for item in descriptions:
        schema_text = str(item["input_schema"])
        assert "runtime_context" not in schema_text


def test_planner_tool_schema_extraction_hides_injected_state():
    planner = OpenAIPlanner(provider="openai")

    schemas = planner._langchain_tools_to_available_schemas(select_tools(["memory_search"]))

    assert schemas[0]["name"] == "memory_search"
    assert "runtime_context" not in str(schemas[0]["input_schema"])


def test_planner_tool_schema_extraction_accepts_openai_function_dicts():
    planner = OpenAIPlanner(provider="openai")

    schemas = planner._langchain_tools_to_available_schemas(
        [
            {
                "type": "function",
                "function": {
                    "name": "transfer_to_food_advisor",
                    "description": "Handoff to food advisor.",
                    "parameters": {
                        "type": "object",
                        "properties": {"task": {"type": "string"}},
                    },
                },
            }
        ]
    )

    assert schemas == [
        {
            "name": "transfer_to_food_advisor",
            "description": "Handoff to food advisor.",
            "input_schema": {
                "type": "object",
                "properties": {"task": {"type": "string"}},
            },
        }
    ]


@pytest.mark.asyncio
async def test_toolnode_injects_runtime_context_into_native_tool():
    class DemoArgs(BaseModel):
        query: str = Field(..., description="query")
        runtime_context: RuntimeContext = Field(default_factory=dict)

    async def _demo(query: str, runtime_context: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"query": query, "session_id": (runtime_context or {}).get("session_id")}

    tool = StructuredTool.from_function(
        coroutine=_demo,
        name="demo_tool",
        description="demo",
        args_schema=DemoArgs,
        infer_schema=False,
    )
    node = ToolNode([tool], messages_key="messages")
    ai_message = AIMessage(
        content="",
        tool_calls=[
            {"name": "demo_tool", "args": {"query": "火锅"}, "id": "call_1", "type": "tool_call"}
        ],
    )

    output = await node.ainvoke(
        {"messages": [ai_message], "runtime_context": {"session_id": "s1"}}
    )

    message = output["messages"][0]
    assert message.name == "demo_tool"
    assert "s1" in str(message.content)
