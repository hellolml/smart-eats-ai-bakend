from __future__ import annotations

from app.agent.graph import build_langgraph_official


def test_build_langgraph_official_returns_graph_instance(override_redis):
    graph = build_langgraph_official(
        db=None,
        redis_client=override_redis,
        provider=None,
        agent_config=None,
    )

    assert graph is not None
    assert hasattr(graph, "add_node")


def test_build_langgraph_official_contains_postprocess_node(override_redis):
    graph = build_langgraph_official(
        db=None,
        redis_client=override_redis,
        provider=None,
        agent_config=None,
    )

    assert hasattr(graph, "nodes")
    assert "tool_postprocess" in graph.nodes
