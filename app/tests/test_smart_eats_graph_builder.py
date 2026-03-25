import inspect
import json
from types import SimpleNamespace

import pytest

from app.agent import tools_registry
from app.agent.agents import smart_eats as smart_eats_module
from app.agent.agents.smart_eats import (
    _best_effort_final_from_observations,
    _refresh_observation_context,
    build_smart_eats_graph,
    get_smart_eats_agent_config,
)
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

    monkeypatch.setattr("app.agent.llm_adapters.OpenAIPlanner.plan_tool_calls", _fake_plan_tool_calls)
    monkeypatch.setattr(smart_eats_module, "_ensure_chat_session", _noop_ensure_chat_session)
    monkeypatch.setattr(smart_eats_module, "_refresh_observation_context", _noop_refresh_observation_context)

    graph = build_smart_eats_graph(
        db=None,
        redis_client=override_redis,
        provider=None,
    ).compile()

    result = await graph.ainvoke(ChatState(session_id="s-smart-eats", message="附近吃什么").__dict__)
    assert result["final_json"]["recommendations"][0]["title"] == "smart_eats独立链路"


@pytest.mark.asyncio
async def test_build_smart_eats_graph_route_result_is_used_in_submit_final_answer(monkeypatch, override_redis):
    tools_registry.load_tools()
    plan_route_spec = tools_registry.TOOLS["plan_route"]

    async def _fake_plan_route(args):
        return {
            "mode": "walking",
            "distance_m": 465,
            "duration_s": 372,
            "steps": [
                "向南步行190米左转",
                "沿丰顺路向东步行16米向右前方行走",
                "沿丰顺路向东步行226米右转",
                "向南步行33米到达目的地",
            ],
            "origin": "112.933349,28.147883",
            "destination": "112.935793,28.145665",
        }

    monkeypatch.setitem(
        tools_registry.TOOLS,
        "plan_route",
        tools_registry.ToolSpec(
            name=plan_route_spec.name,
            description=plan_route_spec.description,
            input_schema=plan_route_spec.input_schema,
            output_schema=plan_route_spec.output_schema,
            func=_fake_plan_route,
        ),
    )

    call_counter = {"count": 0}

    async def _fake_plan_tool_calls(self, system, user, available_tools):
        call_counter["count"] += 1
        if call_counter["count"] == 1:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "name": "plan_route",
                        "args": {
                            "origin_lat": 28.147883,
                            "origin_lng": 112.933349,
                            "destination_lat": 28.145665,
                            "destination_lng": 112.935793,
                            "mode": "walking",
                        },
                        "id": "call_route_1",
                        "type": "tool_call",
                    }
                ],
            }

        marker = "- context: "
        context_payload = {}
        if marker in system:
            context_payload = json.loads(system.split(marker, 1)[1].strip())
        latest_route = None
        if isinstance(context_payload, dict):
            nested_context = context_payload.get("context")
            if isinstance(nested_context, dict):
                latest_route = nested_context.get("latest_route")
            else:
                latest_route = context_payload.get("latest_route")
        assert isinstance(latest_route, dict)
        assert latest_route.get("distance_m") == 465
        assert latest_route.get("duration_s") == 372
        assert latest_route.get("steps")

        return {
            "content": "",
            "tool_calls": [
                {
                    "name": "submit_final_answer",
                    "args": {
                        "recommendations": [
                            {
                                "type": "note",
                                "title": f"步行约{latest_route.get('distance_m')}米，预计{latest_route.get('duration_s')}秒可达",
                                "reason": "route_context_used",
                            }
                        ],
                        "followups": [latest_route.get("steps")[0]],
                        "warnings": [],
                    },
                    "id": "call_route_final",
                    "type": "tool_call",
                }
            ],
        }

    async def _noop_ensure_chat_session(db, state):
        return None

    async def _test_refresh_observation_context(db, redis_client, state, agent_config, emit_context_event=True):
        state.intent = smart_eats_module.smart_intent_resolver(state) or "unknown"
        context = {"ui_scene": state.scene or "chat"}
        context["user_message"] = state.message or ""
        context["history"] = []
        context["observations"] = list(state.observations)

        extra = smart_eats_module.smart_context_extender(state)
        if extra:
            context = smart_eats_module._merge_context(context, extra)
        if isinstance(state.context_overrides, dict) and state.context_overrides:
            context = smart_eats_module._merge_context(context, state.context_overrides)

        context["system_prompt"] = agent_config.system_prompt_builder({"context": context})
        state.context = context

    async def _noop_save_tool_message(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.agent.llm_adapters.OpenAIPlanner.plan_tool_calls", _fake_plan_tool_calls)
    monkeypatch.setattr(smart_eats_module, "_ensure_chat_session", _noop_ensure_chat_session)
    monkeypatch.setattr(smart_eats_module, "_refresh_observation_context", _test_refresh_observation_context)
    monkeypatch.setattr("app.agent.agents.smart_eats.history.save_tool_message", _noop_save_tool_message)

    graph = build_smart_eats_graph(
        db=None,
        redis_client=override_redis,
        provider=None,
    ).compile()

    result = await graph.ainvoke(ChatState(session_id="s-smart-route", message="去新疆阿布烤羊肉店").__dict__)

    assert result["final_json"]["recommendations"][0]["title"] == "步行约465米，预计372秒可达"
    assert result["final_json"]["recommendations"][0]["reason"] == "route_context_used"
    assert result["final_json"]["followups"][0] == "向南步行190米左转"
    assert call_counter["count"] >= 2


@pytest.mark.asyncio
async def test_build_smart_eats_graph_restaurant_confirm_injects_route_context_and_directive(monkeypatch, override_redis):
    async def _noop_ensure_chat_session(db, state):
        return None

    async def _noop_save_user_message(*_args, **_kwargs):
        return None

    async def _noop_maybe_compress_history(_redis_client, _provider, _session_id, history):
        return history, None

    async def _fake_load_history(db, redis_client, session_id, limit, current_message):
        return [{"role": "assistant", "content": "你可以试试：新疆阿布烤羊肉店、巴依老爷火锅。"}]

    async def _fake_search_memories(db, user_id, message, redis_client=None):
        return []

    async def _fake_load_cached_location(redis_client, session_id):
        return {"lat": 28.147883, "lng": 112.933349, "city": "长沙"}

    async def _fake_load_cached_restaurants(redis_client, session_id):
        return [
            {
                "provider_id": "poi_1",
                "name": "新疆阿布烤羊肉店",
                "geo": {"lat": 28.145665, "lng": 112.935793},
            }
        ]

    captured = {"system": ""}

    async def _fake_plan_tool_calls(self, system, user, available_tools):
        captured["system"] = system
        return {
            "content": "",
            "tool_calls": [
                {
                    "name": "submit_final_answer",
                    "args": {
                        "recommendations": [
                            {"type": "note", "title": "收到", "reason": "test_probe"}
                        ],
                        "followups": [],
                        "warnings": [],
                    },
                    "id": "call_probe",
                    "type": "tool_call",
                }
            ],
        }

    monkeypatch.setattr("app.agent.llm_adapters.OpenAIPlanner.plan_tool_calls", _fake_plan_tool_calls)
    monkeypatch.setattr(smart_eats_module, "_ensure_chat_session", _noop_ensure_chat_session)
    monkeypatch.setattr("app.agent.agents.smart_eats.history.save_user_message", _noop_save_user_message)
    monkeypatch.setattr("app.agent.agents.smart_eats.history.load_history", _fake_load_history)
    monkeypatch.setattr("app.agent.agents.smart_eats.history.maybe_compress_history", _noop_maybe_compress_history)
    monkeypatch.setattr("app.agent.agents.smart_eats.memory.search_memories", _fake_search_memories)
    monkeypatch.setattr("app.agent.agents.smart_eats.load_cached_location", _fake_load_cached_location)
    monkeypatch.setattr("app.agent.agents.smart_eats.load_cached_restaurants", _fake_load_cached_restaurants)

    async def _noop_save_tool_message(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.agent.agents.smart_eats.history.save_tool_message", _noop_save_tool_message)

    graph = build_smart_eats_graph(
        db=SimpleNamespace(),
        redis_client=override_redis,
        provider=None,
    ).compile()

    await graph.ainvoke(ChatState(session_id="s-smart-confirm", message="就去新疆阿布烤羊肉店").__dict__)

    marker = "- context: "
    assert marker in captured["system"]
    context_payload = json.loads(captured["system"].split(marker, 1)[1].strip())
    runtime_context = context_payload.get("context") if isinstance(context_payload, dict) else {}

    assert isinstance(runtime_context, dict)
    assert runtime_context.get("cached_location", {}).get("lat") == 28.147883
    assert runtime_context.get("cached_location", {}).get("lng") == 112.933349
    assert isinstance(runtime_context.get("last_restaurants"), list)
    assert runtime_context.get("last_restaurants")[0].get("name") == "新疆阿布烤羊肉店"

    system_directive = runtime_context.get("system_directive")
    assert isinstance(system_directive, str) and system_directive
    assert "优先为该门店规划路线" in system_directive


@pytest.mark.asyncio
async def test_build_smart_eats_graph_confirm_restaurant_routes_before_final(monkeypatch, override_redis):
    tools_registry.load_tools()
    search_spec = tools_registry.TOOLS["search_restaurants"]
    plan_route_spec = tools_registry.TOOLS["plan_route"]

    cached_location_store = {"lat": 28.147883, "lng": 112.933349, "city": "长沙"}
    cached_restaurants_store: list[dict] = []
    planner_trace: list[tuple[str, int, str]] = []
    user_call_count = {"我想吃火锅": 0, "就去新疆阿布烤羊肉店": 0}

    async def _fake_search_restaurants(args):
        result = [
            {
                "provider_id": "poi_1",
                "name": "新疆阿布烤羊肉店",
                "geo": {"lat": 28.145665, "lng": 112.935793},
                "rating": 4.7,
            },
            {
                "provider_id": "poi_2",
                "name": "巴依老爷火锅",
                "geo": {"lat": 28.146901, "lng": 112.936101},
                "rating": 4.6,
            },
        ]
        cached_restaurants_store.clear()
        cached_restaurants_store.extend(result)
        return result

    async def _fake_plan_route(args):
        return {
            "mode": "walking",
            "distance_m": 465,
            "duration_s": 372,
            "steps": [
                "向南步行190米左转",
                "沿丰顺路向东步行16米向右前方行走",
                "沿丰顺路向东步行226米右转",
                "向南步行33米到达目的地",
            ],
            "origin": f"{args.get('origin_lng')},{args.get('origin_lat')}",
            "destination": f"{args.get('destination_lng')},{args.get('destination_lat')}",
        }

    monkeypatch.setitem(
        tools_registry.TOOLS,
        "search_restaurants",
        tools_registry.ToolSpec(
            name=search_spec.name,
            description=search_spec.description,
            input_schema=search_spec.input_schema,
            output_schema=search_spec.output_schema,
            func=_fake_search_restaurants,
        ),
    )
    monkeypatch.setitem(
        tools_registry.TOOLS,
        "plan_route",
        tools_registry.ToolSpec(
            name=plan_route_spec.name,
            description=plan_route_spec.description,
            input_schema=plan_route_spec.input_schema,
            output_schema=plan_route_spec.output_schema,
            func=_fake_plan_route,
        ),
    )

    async def _fake_load_history(db, redis_client, session_id, limit, current_message):
        return []

    async def _noop_maybe_compress_history(redis_client, provider, session_id, history):
        return history, None

    async def _fake_search_memories(db, user_id, message, redis_client=None):
        return []

    async def _fake_load_cached_location(redis_client, session_id):
        return dict(cached_location_store)

    async def _fake_load_cached_restaurants(redis_client, session_id):
        return [dict(item) for item in cached_restaurants_store] if cached_restaurants_store else None

    async def _noop_save_user_message(*_args, **_kwargs):
        return None

    async def _noop_save_tool_message(*_args, **_kwargs):
        return None

    async def _noop_ensure_chat_session(db, state):
        return None

    def _extract_runtime_context(system: str) -> dict:
        marker = "- context: "
        if marker not in system:
            return {}
        payload = json.loads(system.split(marker, 1)[1].strip())
        if not isinstance(payload, dict):
            return {}
        nested = payload.get("context")
        return nested if isinstance(nested, dict) else payload

    async def _fake_plan_tool_calls(self, system, user, available_tools):
        runtime_context = _extract_runtime_context(system)
        user_call_count[user] = user_call_count.get(user, 0) + 1
        current_call = user_call_count[user]

        if user == "我想吃火锅":
            if current_call == 1:
                planner_trace.append(("turn1", current_call, "search_restaurants"))
                return {
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "search_restaurants",
                            "args": {
                                "query": "火锅",
                                "lat": cached_location_store["lat"],
                                "lng": cached_location_store["lng"],
                            },
                            "id": "call_turn1_search",
                            "type": "tool_call",
                        }
                    ],
                }

            planner_trace.append(("turn1", current_call, "submit_final_answer"))
            return {
                "content": "",
                "tool_calls": [
                    {
                        "name": "submit_final_answer",
                        "args": {
                            "recommendations": [
                                {"type": "note", "title": "给你找了几家火锅店", "reason": "restaurant_results"}
                            ],
                            "followups": ["你想去哪家？"],
                            "warnings": [],
                        },
                        "id": "call_turn1_final",
                        "type": "tool_call",
                    }
                ],
            }

        if user == "就去新疆阿布烤羊肉店":
            if current_call == 1:
                cached_location = runtime_context.get("cached_location")
                last_restaurants = runtime_context.get("last_restaurants")
                route_target = runtime_context.get("route_target_candidate")
                directive = runtime_context.get("system_directive")

                has_route_inputs = (
                    isinstance(cached_location, dict)
                    and cached_location.get("lat") is not None
                    and cached_location.get("lng") is not None
                    and isinstance(last_restaurants, list)
                    and bool(last_restaurants)
                    and isinstance(route_target, dict)
                    and isinstance(route_target.get("geo"), dict)
                    and isinstance(directive, str)
                    and "优先为该门店规划路线" in directive
                )

                if has_route_inputs:
                    planner_trace.append(("turn2", current_call, "plan_route"))
                    return {
                        "content": "",
                        "tool_calls": [
                            {
                                "name": "plan_route",
                                "args": {
                                    "origin_lat": cached_location.get("lat"),
                                    "origin_lng": cached_location.get("lng"),
                                    "destination_lat": route_target.get("geo", {}).get("lat"),
                                    "destination_lng": route_target.get("geo", {}).get("lng"),
                                    "mode": "walking",
                                },
                                "id": "call_turn2_route",
                                "type": "tool_call",
                            }
                        ],
                    }

                planner_trace.append(("turn2", current_call, "submit_final_answer"))
                return {
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "submit_final_answer",
                            "args": {
                                "recommendations": [
                                    {"type": "note", "title": "好的。", "reason": "fallback_direct_final"}
                                ],
                                "followups": [],
                                "warnings": [],
                            },
                            "id": "call_turn2_wrong_final",
                            "type": "tool_call",
                        }
                    ],
                }

            latest_route = runtime_context.get("latest_route")
            assert isinstance(latest_route, dict)
            assert latest_route.get("distance_m") == 465
            assert latest_route.get("duration_s") == 372
            assert latest_route.get("steps")

            planner_trace.append(("turn2", current_call, "submit_final_answer"))
            return {
                "content": "",
                "tool_calls": [
                    {
                        "name": "submit_final_answer",
                        "args": {
                            "recommendations": [
                                {
                                    "type": "note",
                                    "title": "步行约465米，预计372秒可达",
                                    "reason": "route_context_used",
                                }
                            ],
                            "followups": [latest_route.get("steps")[0]],
                            "warnings": [],
                        },
                        "id": "call_turn2_final",
                        "type": "tool_call",
                    }
                ],
            }

        return {
            "content": "",
            "tool_calls": [
                {
                    "name": "submit_final_answer",
                    "args": {
                        "recommendations": [
                            {"type": "note", "title": "收到", "reason": "default"}
                        ],
                        "followups": [],
                        "warnings": [],
                    },
                    "id": "call_default",
                    "type": "tool_call",
                }
            ],
        }

    monkeypatch.setattr("app.agent.llm_adapters.OpenAIPlanner.plan_tool_calls", _fake_plan_tool_calls)
    monkeypatch.setattr(smart_eats_module, "_ensure_chat_session", _noop_ensure_chat_session)
    monkeypatch.setattr("app.agent.agents.smart_eats.history.load_history", _fake_load_history)
    monkeypatch.setattr("app.agent.agents.smart_eats.history.maybe_compress_history", _noop_maybe_compress_history)
    monkeypatch.setattr("app.agent.agents.smart_eats.history.save_user_message", _noop_save_user_message)
    monkeypatch.setattr("app.agent.agents.smart_eats.history.save_tool_message", _noop_save_tool_message)
    monkeypatch.setattr("app.agent.agents.smart_eats.memory.search_memories", _fake_search_memories)
    monkeypatch.setattr("app.agent.agents.smart_eats.load_cached_location", _fake_load_cached_location)
    monkeypatch.setattr("app.agent.agents.smart_eats.load_cached_restaurants", _fake_load_cached_restaurants)

    graph = build_smart_eats_graph(
        db=SimpleNamespace(),
        redis_client=override_redis,
        provider=None,
    ).compile()

    turn1_result = await graph.ainvoke(ChatState(session_id="s-smart-e2e-route", message="我想吃火锅").__dict__)
    assert turn1_result["final_json"]["recommendations"][0]["reason"] == "restaurant_results"

    turn2_result = await graph.ainvoke(ChatState(session_id="s-smart-e2e-route", message="就去新疆阿布烤羊肉店").__dict__)

    assert turn2_result["final_json"]["recommendations"][0]["reason"] == "route_context_used"
    assert turn2_result["final_json"]["followups"][0] == "向南步行190米左转"

    assert ("turn2", 1, "plan_route") in planner_trace
    assert ("turn2", 1, "submit_final_answer") not in planner_trace


@pytest.mark.asyncio
async def test_build_smart_eats_graph_confirm_restaurant_without_origin_guides_user(monkeypatch, override_redis):
    tools_registry.load_tools()
    search_spec = tools_registry.TOOLS["search_restaurants"]

    cached_restaurants_store: list[dict] = []
    planner_trace: list[tuple[str, int, str]] = []
    user_call_count = {"我想吃火锅": 0, "就去新疆阿布烤羊肉店": 0}

    async def _fake_search_restaurants(args):
        result = [
            {
                "provider_id": "poi_1",
                "name": "新疆阿布烤羊肉店",
                "geo": {"lat": 28.145665, "lng": 112.935793},
                "rating": 4.7,
            },
            {
                "provider_id": "poi_2",
                "name": "巴依老爷火锅",
                "geo": {"lat": 28.146901, "lng": 112.936101},
                "rating": 4.6,
            },
        ]
        cached_restaurants_store.clear()
        cached_restaurants_store.extend(result)
        return result

    monkeypatch.setitem(
        tools_registry.TOOLS,
        "search_restaurants",
        tools_registry.ToolSpec(
            name=search_spec.name,
            description=search_spec.description,
            input_schema=search_spec.input_schema,
            output_schema=search_spec.output_schema,
            func=_fake_search_restaurants,
        ),
    )

    async def _fake_load_history(db, redis_client, session_id, limit, current_message):
        return []

    async def _noop_maybe_compress_history(redis_client, provider, session_id, history):
        return history, None

    async def _fake_search_memories(db, user_id, message, redis_client=None):
        return []

    async def _fake_load_cached_location(redis_client, session_id):
        return None

    async def _fake_load_cached_restaurants(redis_client, session_id):
        return [dict(item) for item in cached_restaurants_store] if cached_restaurants_store else None

    async def _noop_save_user_message(*_args, **_kwargs):
        return None

    async def _noop_save_tool_message(*_args, **_kwargs):
        return None

    async def _noop_ensure_chat_session(db, state):
        return None

    def _extract_runtime_context(system: str) -> dict:
        marker = "- context: "
        if marker not in system:
            return {}
        payload = json.loads(system.split(marker, 1)[1].strip())
        if not isinstance(payload, dict):
            return {}
        nested = payload.get("context")
        return nested if isinstance(nested, dict) else payload

    async def _fake_plan_tool_calls(self, system, user, available_tools):
        runtime_context = _extract_runtime_context(system)
        user_call_count[user] = user_call_count.get(user, 0) + 1
        current_call = user_call_count[user]

        if user == "我想吃火锅":
            if current_call == 1:
                planner_trace.append(("turn1", current_call, "search_restaurants"))
                return {
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "search_restaurants",
                            "args": {
                                "query": "火锅",
                                "lat": 28.147883,
                                "lng": 112.933349,
                            },
                            "id": "call_turn1_search",
                            "type": "tool_call",
                        }
                    ],
                }

            planner_trace.append(("turn1", current_call, "submit_final_answer"))
            return {
                "content": "",
                "tool_calls": [
                    {
                        "name": "submit_final_answer",
                        "args": {
                            "recommendations": [
                                {"type": "note", "title": "给你找了几家火锅店", "reason": "restaurant_results"}
                            ],
                            "followups": ["你想去哪家？"],
                            "warnings": [],
                        },
                        "id": "call_turn1_final",
                        "type": "tool_call",
                    }
                ],
            }

        if user == "就去新疆阿布烤羊肉店":
            if current_call == 1:
                directive = runtime_context.get("system_directive")
                route_target = runtime_context.get("route_target_candidate")
                cached_location = runtime_context.get("cached_location")

                assert isinstance(route_target, dict)
                assert route_target.get("name") == "新疆阿布烤羊肉店"
                assert cached_location is None
                assert isinstance(directive, str) and "缺少可用起点坐标" in directive

                planner_trace.append(("turn2", current_call, "submit_final_answer"))
                return {
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "submit_final_answer",
                            "args": {
                                "recommendations": [
                                    {
                                        "type": "note",
                                        "title": "还需要你的出发位置，才能给你规划去新疆阿布烤羊肉店的路线。",
                                        "reason": "missing_origin_for_route",
                                    }
                                ],
                                "followups": ["告诉我你当前城市、地标或发送定位，我马上给你规划路线。"],
                                "warnings": [],
                            },
                            "id": "call_turn2_missing_origin_final",
                            "type": "tool_call",
                        }
                    ],
                }

        return {
            "content": "",
            "tool_calls": [
                {
                    "name": "submit_final_answer",
                    "args": {
                        "recommendations": [
                            {"type": "note", "title": "收到", "reason": "default"}
                        ],
                        "followups": [],
                        "warnings": [],
                    },
                    "id": "call_default",
                    "type": "tool_call",
                }
            ],
        }

    monkeypatch.setattr("app.agent.llm_adapters.OpenAIPlanner.plan_tool_calls", _fake_plan_tool_calls)
    monkeypatch.setattr(smart_eats_module, "_ensure_chat_session", _noop_ensure_chat_session)
    monkeypatch.setattr("app.agent.agents.smart_eats.history.load_history", _fake_load_history)
    monkeypatch.setattr("app.agent.agents.smart_eats.history.maybe_compress_history", _noop_maybe_compress_history)
    monkeypatch.setattr("app.agent.agents.smart_eats.history.save_user_message", _noop_save_user_message)
    monkeypatch.setattr("app.agent.agents.smart_eats.history.save_tool_message", _noop_save_tool_message)
    monkeypatch.setattr("app.agent.agents.smart_eats.memory.search_memories", _fake_search_memories)
    monkeypatch.setattr("app.agent.agents.smart_eats.load_cached_location", _fake_load_cached_location)
    monkeypatch.setattr("app.agent.agents.smart_eats.load_cached_restaurants", _fake_load_cached_restaurants)

    graph = build_smart_eats_graph(
        db=SimpleNamespace(),
        redis_client=override_redis,
        provider=None,
    ).compile()

    turn1_result = await graph.ainvoke(ChatState(session_id="s-smart-e2e-missing-origin", message="我想吃火锅").__dict__)
    assert turn1_result["final_json"]["recommendations"][0]["reason"] == "restaurant_results"

    turn2_result = await graph.ainvoke(ChatState(session_id="s-smart-e2e-missing-origin", message="就去新疆阿布烤羊肉店").__dict__)

    recommendation = turn2_result["final_json"]["recommendations"][0]
    assert recommendation["reason"] == "missing_origin_for_route"
    assert recommendation["title"] != "好的。"
    assert "出发位置" in recommendation["title"]
    assert turn2_result["final_json"]["followups"]
    assert "告诉我你当前城市、地标或发送定位" in turn2_result["final_json"]["followups"][0]

    assert ("turn2", 1, "submit_final_answer") in planner_trace
    assert ("turn2", 1, "plan_route") not in planner_trace
