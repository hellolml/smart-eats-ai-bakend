from __future__ import annotations

from app.agent.supervisor.graph import build_supervisor_runtime_graph, worker_names
from app.agent.supervisor.model import PlannerChatModel


class _NoopPlanner:
    async def ainvoke_with_tools(self, messages, tools, image_parts=None):
        from langchain_core.messages import AIMessage

        return AIMessage(content="好的。")


def test_supervisor_builder_uses_expected_workers():
    graph = build_supervisor_runtime_graph(
        db=None,
        redis_client=None,
        model=PlannerChatModel(planner=_NoopPlanner()),
    )

    names = set(worker_names())
    assert names == {"travel_planner", "food_advisor", "route_planner", "home_chef", "general_chat"}
    assert names.issubset(set(graph.nodes))
    assert "global_supervisor" in graph.nodes
