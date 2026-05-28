from __future__ import annotations

import inspect

from app.agent.runtime import graph as runtime_module
from app.agent.runtime.graph import AgentRuntimeState, build_agent_runtime_graph, get_agent_runtime_config


def test_build_agent_runtime_graph_returns_graph(override_redis):
    graph = build_agent_runtime_graph(db=None, redis_client=override_redis)

    assert graph is not None
    assert "prepare" in graph.nodes
    assert "summarize" in graph.nodes
    assert "agent" in graph.nodes
    assert "tools" in graph.nodes


def test_agent_runtime_graph_uses_typed_state_and_message_accumulator():
    assert runtime_module.AgentRuntimeGraphState.__annotations__["messages"] is not None
    source = inspect.getsource(runtime_module.AgentRuntimeGraphState)
    assert "add_messages" in source


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
    assert config.name == "generic_runtime"


def test_runtime_graph_has_no_business_specific_runtime_code():
    source = inspect.getsource(runtime_module)

    assert "app.agent.agents.base" not in source
    assert "load_cached_location" not in source
    assert "load_cached_restaurants" not in source
    assert "TravelPlannerWorkflow" not in source


def test_build_agent_runtime_graph_source_uses_toolnode_postprocess_boundary():
    source = inspect.getsource(build_agent_runtime_graph)

    assert "ToolNode" in source
    assert "_invoke_tool_node_with_runtime" in source
    assert "_apply_official_tool_postprocess" in source
