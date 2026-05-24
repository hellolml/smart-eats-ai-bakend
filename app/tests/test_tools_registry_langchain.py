from __future__ import annotations

import pytest

from app.agent import tools_registry


@pytest.mark.asyncio
async def test_to_langchain_tools_respects_allowlist_and_executes_with_runtime_context(monkeypatch):
    original_tools = dict(tools_registry.TOOLS)
    try:
        tools_registry.TOOLS.clear()

        @tools_registry.register_tool(
            name="demo_tool",
            description="demo",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "count": {"type": "integer"},
                },
                "required": ["query"],
            },
            output_schema={"type": "object"},
        )
        async def _demo(args):
            return {
                "query": args.get("query"),
                "count": args.get("count"),
                "session_id": args.get("session_id"),
            }

        @tools_registry.register_tool(
            name="other_tool",
            description="other",
            input_schema={"type": "object", "properties": {}},
            output_schema={"type": "object"},
        )
        async def _other(args):
            return args

        tools = tools_registry.get_langchain_tools(
            allowlist=["demo_tool"],
            runtime_context_factory=lambda: {"session_id": "s1"},
        )
        alias_tools = tools_registry.to_langchain_tools(allowlist=["demo_tool"])

        assert len(tools) == 1
        assert len(alias_tools) == 1
        tool = tools[0]
        assert tool.name == "demo_tool"
        assert "query" in tool.args

        result = await tool.ainvoke({"query": "火锅", "count": 2})
        assert result["query"] == "火锅"
        assert result["count"] == 2
        assert result["session_id"] == "s1"
    finally:
        tools_registry.TOOLS.clear()
        tools_registry.TOOLS.update(original_tools)


def test_build_args_model_marks_required_fields():
    model = tools_registry._build_args_model(
        "search_restaurants",
        {
            "type": "object",
            "properties": {
                "lat": {"type": "number"},
                "lng": {"type": "number"},
                "query": {"type": "string"},
            },
            "required": ["lat", "lng"],
        },
    )

    fields = model.model_fields
    assert fields["lat"].is_required()
    assert fields["lng"].is_required()
    assert not fields["query"].is_required()
