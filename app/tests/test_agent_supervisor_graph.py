from __future__ import annotations

from app.agent.supervisor.graph import _model_diagnostics, build_supervisor_runtime_graph, route_agent_request, worker_names
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
    assert {"route", "worker", "finalize"}.issubset(set(graph.nodes))


def test_supervisor_router_selects_worker_by_scene_and_intent():
    assert route_agent_request({"session_id": "s", "scene": "travel_planner", "message": "杭州攻略"}).worker == "travel_planner"
    assert route_agent_request({"session_id": "s", "scene": "eat", "message": "附近吃什么"}).worker == "food_advisor"
    assert route_agent_request({"session_id": "s", "scene": "chat", "message": "冰箱里有鸡蛋怎么做"}).worker == "home_chef"
    assert route_agent_request({"session_id": "s", "scene": "chat", "message": "怎么去西湖"}).worker == "route_planner"
    assert route_agent_request({"session_id": "s", "scene": "chat", "message": "你好"}).worker == "general_chat"


def test_supervisor_router_keeps_decide_food_intent_separate_from_eat_out():
    decision = route_agent_request({"session_id": "s", "scene": "eat", "message": "今天吃点啥"})

    assert decision.worker == "food_advisor"
    assert decision.intent == "decide_food"


def test_supervisor_router_treats_cuisine_budget_as_eat_out():
    decision = route_agent_request({"session_id": "s", "scene": "chat", "message": "在紫阳县城附近找烧烤，人均50以内"})

    assert decision.worker == "food_advisor"
    assert decision.intent == "eat_out"


def test_supervisor_router_allows_business_followups_inside_travel_scene():
    food = route_agent_request({"session_id": "s", "scene": "travel_planner", "message": "附近有什么好吃的"})
    route = route_agent_request({"session_id": "s", "scene": "travel_planner", "message": "从酒店怎么去浅草寺"})
    itinerary = route_agent_request({"session_id": "s", "scene": "travel_planner", "message": "第二天换成亲子路线"})

    assert food.worker == "food_advisor"
    assert route.worker == "route_planner"
    assert itinerary.worker == "travel_planner"


def test_supervisor_router_sends_eat_scene_navigation_followup_to_route_worker():
    decision = route_agent_request({"session_id": "s", "scene": "eat", "message": "怎么走呢"})

    assert decision.worker == "route_planner"
    assert decision.intent == "route"


def test_supervisor_model_diagnostics_exposes_non_secret_runtime_model():
    diagnostics = _model_diagnostics(
        "openai:kimi-k2.5",
        {
            "source": "env",
            "provider": "openai",
            "provider_value": "openai:kimi-k2.5",
            "model_planner": "kimi-k2.5",
            "model_writer": "kimi-k2.5",
            "api_key": "sk-secret",
        },
    )

    assert diagnostics == {
        "model_config": {
            "source": "env",
            "provider": "openai",
            "provider_value": "openai:kimi-k2.5",
            "model_planner": "kimi-k2.5",
            "model_writer": "kimi-k2.5",
        }
    }
    assert "sk-secret" not in str(diagnostics)
