from __future__ import annotations

import inspect

from app.agent.runtime import graph as runtime_module
from app.agent.runtime import builder as builder_module
from app.agent.runtime.graph import (
    AgentRuntimeState,
    build_agent_runtime_graph,
    build_cached_agent_runtime_graph,
    get_agent_runtime_config,
    runtime_graph_configurable,
)


def test_build_agent_runtime_graph_returns_graph(override_redis):
    graph = build_agent_runtime_graph(db=None, redis_client=override_redis)

    assert graph is not None
    assert "prepare" in graph.nodes
    assert "summarize" in graph.nodes
    assert "agent" in graph.nodes
    assert "tools" in graph.nodes


def test_build_cached_agent_runtime_graph_reuses_state_graph():
    graph_a = build_cached_agent_runtime_graph(provider="openai", resolved_model_config={"model": "m1"})
    graph_b = build_cached_agent_runtime_graph(provider="openai", resolved_model_config={"model": "m1"})
    graph_c = build_cached_agent_runtime_graph(provider="openai", resolved_model_config={"model": "m2"})

    assert graph_a is graph_b
    assert graph_a is not graph_c


def test_runtime_graph_configurable_carries_request_scoped_dependencies():
    payload = runtime_graph_configurable(db="db", redis_client="redis")

    assert payload["agent_runtime_db"] == "db"
    assert payload["agent_runtime_redis_client"] == "redis"


def test_agent_runtime_graph_uses_typed_state_and_message_accumulator():
    assert runtime_module.AgentRuntimeGraphState.__annotations__["messages"] is not None
    assert "add_messages" in str(runtime_module.AgentRuntimeGraphState.__annotations__["messages"])
    for field_name in runtime_module.AgentRuntimeState.model_fields:
        assert field_name in runtime_module.AgentRuntimeGraphState.__annotations__


def test_initialize_graph_state_preserves_existing_human_message():
    from langchain_core.messages import HumanMessage

    state = AgentRuntimeState(session_id="s1", message="你好")
    initialized = runtime_module._initialize_graph_state(
        {**state.__dict__, "messages": [HumanMessage(content="你好")]}
    )

    assert initialized["messages"] == []
    assert initialized["session_id"] == "s1"


def test_build_official_runtime_context_includes_common_tool_payload():
    state = AgentRuntimeState(
        session_id="s1",
        user_id="u1",
        context={"foo": "bar"},
        client_ip="127.0.0.1",
        last_user_message="hello",
    )

    payload = runtime_module._build_official_runtime_context(
        state,
        db="db",
        redis_client="redis",
        servers_path="mcp.json",
    )

    assert payload["session_id"] == "s1"
    assert payload["user_id"] == "u1"
    assert payload["context"] == {"foo": "bar"}
    assert payload["servers_path"] == "mcp.json"


def test_runtime_config_contains_only_core_tools():
    config = get_agent_runtime_config()

    assert "memory_search" in config.core_tool_names
    assert "source_event_search" in config.core_tool_names
    assert config.name == "skill_runtime"


def test_runtime_graph_has_no_business_specific_runtime_code():
    source = inspect.getsource(builder_module)

    assert "app.agent.agents.base" not in source
    assert "load_cached_location" not in source
    assert "load_cached_restaurants" not in source
    for term in ("fridge", "restaurant", "route", "travel", "recipe", "food_decision"):
        assert term not in source


def test_build_agent_runtime_graph_source_uses_toolnode_postprocess_boundary():
    module_source = inspect.getsource(builder_module)

    assert "ToolNode" in module_source
    assert "_invoke_tool_node_with_runtime" in module_source
    assert "_apply_official_tool_postprocess" in module_source
