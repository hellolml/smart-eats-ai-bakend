from __future__ import annotations

from types import SimpleNamespace
from typing import Any
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel, Field

from app.agent.llm_adapters import AnthropicPlanner, OpenAIPlanner
from app.agent.eval_pricing import calculate_model_cost
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


def test_planner_extracts_cached_tokens_from_dict_usage_details():
    planner = OpenAIPlanner.__new__(OpenAIPlanner)
    planner.config = SimpleNamespace(name="openai", model_planner="gpt-test")
    response = SimpleNamespace(
        usage={
            "prompt_tokens": 1200,
            "completion_tokens": 80,
            "total_tokens": 1280,
            "prompt_tokens_details": {"cached_tokens": 900},
            "completion_tokens_details": {"reasoning_tokens": 12},
        }
    )

    usage = planner._extract_usage_from_response(response, "gpt-test")

    assert usage["input_tokens"] == 1200
    assert usage["cached_tokens"] == 900
    assert usage["cache_miss_tokens"] == 300
    assert usage["reasoning_tokens"] == 12


def test_model_cost_prices_cached_input_once():
    cost = calculate_model_cost(
        provider="openai",
        model="gpt-5.5",
        input_tokens=1000,
        output_tokens=0,
        cached_tokens=800,
    )

    assert cost["token_cost"] == 0.00035


def test_anthropic_system_content_marks_only_stable_prefix_for_cache(monkeypatch):
    from app.common.config import settings

    monkeypatch.setattr(settings, "LLM_PROMPT_CACHE_ENABLED", True)
    planner = AnthropicPlanner.__new__(AnthropicPlanner)

    system_content = planner._build_anthropic_system_content(
        [
            SystemMessage(content="stable rules"),
            SystemMessage(content='## Runtime Context\n- context: {"step": 1}'),
            HumanMessage(content="hello"),
        ]
    )

    assert isinstance(system_content, list)
    assert system_content[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in system_content[1]
    assert system_content[0]["text"] == "stable rules"
    assert "Runtime Context" in system_content[1]["text"]


def test_openai_payload_does_not_emit_provider_specific_cache_control():
    planner = OpenAIPlanner.__new__(OpenAIPlanner)

    payload = planner._messages_to_openai_payload(
        [
            SystemMessage(content="stable rules"),
            SystemMessage(content="## Runtime Context\n- context: {}"),
            HumanMessage(content="hello"),
        ],
        [],
    )

    assert "cache_control" not in json.dumps(payload)


def test_planner_builds_openai_tools_in_stable_name_order():
    planner = OpenAIPlanner(provider="openai")

    tools = planner._build_openai_tools(
        [
            {"name": "z_tool", "input_schema": {"type": "object", "properties": {}}},
            {"name": "a_tool", "input_schema": {"type": "object", "properties": {}}},
        ]
    )

    assert [item["function"]["name"] for item in tools[:2]] == ["a_tool", "z_tool"]
    assert tools[-1]["function"]["name"] == "submit_final_answer"


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
