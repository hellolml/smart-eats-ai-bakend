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


def test_supervisor_router_uses_recent_restaurant_context_for_selection():
    decision = route_agent_request(
        {
            "session_id": "s",
            "scene": "chat",
            "message": "五星之家",
            "context_overrides": {
                "last_restaurants": [
                    {"name": "五星之家", "address": "洋湖附近"},
                    {"name": "屋门口土菜研究院(岳麓店)"},
                ]
            },
        }
    )

    assert decision.worker == "food_advisor"
    assert decision.intent == "eat_out"
    assert decision.reason == "recent_restaurant_selection"


def test_supervisor_router_uses_partial_recent_restaurant_hint_for_selection():
    decision = route_agent_request(
        {
            "session_id": "s",
            "scene": "chat",
            "message": "就选你上面推荐里名字带“五星”的那家。",
            "context_overrides": {"last_restaurants": [{"name": "五星之家"}, {"name": "屋门口土菜研究院"}]},
        }
    )

    assert decision.worker == "food_advisor"
    assert decision.intent == "eat_out"


def test_supervisor_router_keeps_structured_travel_request_in_travel_worker():
    decision = route_agent_request(
        {
            "session_id": "s",
            "scene": "travel_planner",
            "message": "目的地：成都\n出行天数：三天\n偏好：想吃火锅和小吃\n请输出候选行程",
        }
    )

    assert decision.worker == "travel_planner"
    assert decision.intent == "travel"


def test_supervisor_router_keeps_travel_plan_with_food_budget_in_travel_worker():
    decision = route_agent_request(
        {
            "session_id": "s",
            "scene": "travel_planner",
            "message": "帮我做广州 3 天旅行计划：陈家祠、沙面、永庆坊。两个人，一个不吃辣，预算每天人均 300，住体育西附近。",
        }
    )

    assert decision.worker == "travel_planner"
    assert decision.intent == "travel"


def test_supervisor_router_keeps_travel_revision_with_food_preference_in_travel_worker():
    decision = route_agent_request(
        {
            "session_id": "s",
            "scene": "travel_planner",
            "message": "改成 2 天，不去北京路，同行的人膝盖不好，少走路，晚餐想吃粤菜。",
        }
    )

    assert decision.worker == "travel_planner"
    assert decision.intent == "travel"


def test_supervisor_router_respects_explicit_general_chat_override():
    decision = route_agent_request(
        {
            "session_id": "s",
            "scene": "chat",
            "message": "先别管吃饭，陪我随便聊一句，今天有点累。",
            "context_overrides": {"last_restaurants": [{"name": "第一家"}]},
        }
    )

    assert decision.worker == "general_chat"
    assert decision.intent == "chat"


def test_supervisor_router_respects_explicit_general_chat_override_inside_travel_scene():
    decision = route_agent_request(
        {
            "session_id": "s",
            "scene": "travel_planner",
            "message": "先别管旅行，陪我吐槽一句，改行程好烦。",
            "context_overrides": {"latest_travel_final_json": {"state": "candidates_ready"}},
        }
    )

    assert decision.worker == "general_chat"
    assert decision.intent == "chat"


def test_supervisor_router_routes_at_home_eating_ingredient_question_to_home_chef():
    decision = route_agent_request(
        {
            "session_id": "s",
            "scene": "chat",
            "message": "明早如果在家吃，我有鸡蛋和青菜，10 分钟能做什么？",
        }
    )

    assert decision.worker == "home_chef"
    assert decision.intent == "cook_home"


def test_supervisor_router_keeps_decide_food_intent_separate_from_eat_out():
    decision = route_agent_request({"session_id": "s", "scene": "eat", "message": "今天吃点啥"})

    assert decision.worker == "food_advisor"
    assert decision.intent == "decide_food"


def test_supervisor_router_treats_cuisine_budget_as_eat_out():
    decision = route_agent_request({"session_id": "s", "scene": "chat", "message": "在紫阳县城附近找烧烤，人均50以内"})

    assert decision.worker == "food_advisor"
    assert decision.intent == "eat_out"


def test_supervisor_router_switches_from_home_cooking_to_eat_out():
    decision = route_agent_request(
        {
            "session_id": "s",
            "scene": "chat",
            "message": "但是我突然不想做饭了，在成都春熙路附近找不辣的牛肉类餐厅，人均 90。",
            "context_overrides": {"latest_home_chef_final_json": {"scene": "home_chef"}},
        }
    )

    assert decision.worker == "food_advisor"
    assert decision.intent == "eat_out"


def test_supervisor_router_does_not_route_when_route_is_negated():
    decision = route_agent_request(
        {
            "session_id": "s",
            "scene": "chat",
            "message": "如果做饭失败，再回春熙路选第一家餐厅，但先不要规划路线。",
            "context_overrides": {"last_restaurants": [{"name": "牛肉馆"}]},
        }
    )

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


def test_supervisor_router_allows_restaurant_selection_inside_travel_scene():
    decision = route_agent_request(
        {
            "session_id": "s",
            "scene": "travel_planner",
            "message": "选第二家，但如果它不适合一个人少走路，就换第一家，并给我一句路线提示。",
            "context_overrides": {
                "last_restaurants": [
                    {"name": "陶陶居", "lat": 23.13, "lng": 113.32},
                    {"name": "广州酒家", "lat": 23.12, "lng": 113.31},
                ]
            },
        }
    )

    assert decision.worker == "food_advisor"
    assert decision.intent == "eat_out"


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
