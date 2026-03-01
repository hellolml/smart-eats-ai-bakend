import inspect

import pytest

from app.agent.agents import smart_eats as smart_eats_module
from app.agent.agents.smart_eats import build_smart_eats_graph
from app.agent.state import ChatState


def test_build_smart_eats_graph_returns_graph(override_redis):
    graph = build_smart_eats_graph(
        db=None,
        redis_client=override_redis,
        provider=None,
    )

    assert graph is not None
    assert hasattr(graph, "add_node")


def test_build_smart_eats_graph_contains_dedicated_nodes(override_redis):
    graph = build_smart_eats_graph(
        db=None,
        redis_client=override_redis,
        provider=None,
    )

    node_names = set(getattr(graph, "nodes", {}).keys())
    assert {"observe", "think", "tools", "tool_postprocess", "finalize"}.issubset(node_names)


def test_build_smart_eats_graph_source_does_not_import_graph_helpers():
    source = inspect.getsource(build_smart_eats_graph)
    assert "from app.agent.graph import" not in source


@pytest.mark.asyncio
async def test_build_smart_eats_graph_roundtrip_without_graph_helpers(monkeypatch, override_redis):
    async def _fake_plan_tool_calls(self, system, user, available_tools):
        return {
            "content": "",
            "tool_calls": [
                {
                    "name": "submit_final_answer",
                    "args": {
                        "recommendations": [
                            {"type": "note", "title": "smart_eats独立链路", "reason": "dedicated_graph"}
                        ],
                        "followups": [],
                        "warnings": [],
                    },
                    "id": "call_smart_dedicated",
                    "type": "tool_call",
                }
            ],
        }

    async def _noop_ensure_chat_session(db, state):
        return None

    async def _noop_refresh_observation_context(db, redis_client, state, agent_config, emit_context_event=True):
        state.context = {"system_prompt": "test system"}

    async def _should_not_call_graph_helper(*_args, **_kwargs):
        raise AssertionError("smart_eats dedicated graph should not call app.agent.graph helpers")

    monkeypatch.setattr("app.agent.llm_adapters.OpenAIPlanner.plan_tool_calls", _fake_plan_tool_calls)
    monkeypatch.setattr(smart_eats_module, "_ensure_chat_session", _noop_ensure_chat_session)
    monkeypatch.setattr(smart_eats_module, "_refresh_observation_context", _noop_refresh_observation_context)
    monkeypatch.setattr("app.agent.legacy_builder_helpers._ensure_chat_session", _should_not_call_graph_helper)
    monkeypatch.setattr("app.agent.legacy_builder_helpers._refresh_observation_context", _should_not_call_graph_helper)

    graph = build_smart_eats_graph(
        db=None,
        redis_client=override_redis,
        provider=None,
    ).compile()

    result = await graph.ainvoke(ChatState(session_id="s-smart-eats", message="附近吃什么").__dict__)
    assert result["final_json"]["recommendations"][0]["title"] == "smart_eats独立链路"
