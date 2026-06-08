#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, ToolMessage

from app.agent.supervisor.graph import build_supervisor_runtime_graph
from scripts.replay_eval import (
    active_value_counts,
    evaluate_result,
    is_fallback,
    worker_tool_boundary_violations,
    worker_tool_call_counts,
    worker_tool_counts,
)


REQUIRED_COVERAGE = {
    "workers": {"food_advisor", "route_planner", "home_chef", "travel_planner", "general_chat"},
    "statuses": {"completed", "needs_clarification"},
    "intents": {"decide_food", "eat_out", "route", "cook_home", "travel", "chat"},
    "tool_calls": {
        "food_decision",
        "search_restaurants",
        "plan_route",
        "get_fridge_items",
        "rag_search_recipes",
        "travel_search_poi",
        "travel_create_personal_map",
    },
    "scenes": {"chat", "eat", "home_chef", "travel_planner"},
    "scenario_types": {"single_turn", "multi_turn"},
    "business_states": {"candidates_ready", "itinerary_generated", "map_generated"},
    "quality_issue_regressions": {
        "food_affirmation_mode_drift",
        "restaurant_selection_ack",
        "restaurant_selection_context_loss",
        "restaurant_route_context_loss",
        "route_memory_tool_leak",
        "travel_tool_explosion",
        "travel_trip_meta_missing",
        "travel_prompt_text_extracted_as_poi",
        "travel_itinerary_day_mismatch",
        "travel_revision_context_stale",
    },
}


DETERMINISTIC_CASES: list[dict[str, Any]] = [
    {
        "id": "deterministic-general-chat",
        "message": "你好",
        "scene": "chat",
        "expect": {
            "no_fallback": True,
            "no_tool_calls": True,
            "worker": "general_chat",
            "intent_in": ["chat", "unknown"],
            "status_in": ["completed"],
        },
    },
    {
        "id": "deterministic-decide-food",
        "message": "今天吃什么",
        "scene": "eat",
        "expect": {
            "no_fallback": True,
            "worker": "food_advisor",
            "intent_in": ["decide_food", "unknown"],
            "status_in": ["completed"],
            "tool_calls_include": ["food_decision"],
            "tool_calls_exclude": ["memory_search", "plan_route"],
        },
    },
    {
        "id": "deterministic-eat-out",
        "message": "我在人民广场附近想吃粤菜",
        "scene": "eat",
        "expect": {
            "no_fallback": True,
            "worker": "food_advisor",
            "intent_in": ["eat_out", "unknown"],
            "status_in": ["completed"],
            "tool_calls_include": ["search_restaurants"],
            "tool_calls_exclude": ["memory_search", "plan_route"],
        },
    },
    {
        "id": "deterministic-eat-out-affirm-refine-multiturn",
        "covers_quality_findings": ["food_affirmation_mode_drift"],
        "turns": [
            {
                "message": "我在人民广场附近想吃粤菜",
                "scene": "eat",
                "expect": {
                    "no_fallback": True,
                    "worker": "food_advisor",
                    "intent_in": ["eat_out", "unknown"],
                    "status_in": ["completed"],
                    "tool_calls_include": ["search_restaurants"],
                    "tool_calls_exclude": ["food_decision", "plan_route", "memory_search"],
                },
            },
            {
                "message": "可以啊",
                "scene": "eat",
                "expect": {
                    "no_fallback": True,
                    "worker": "food_advisor",
                    "intent_in": ["eat_out", "unknown"],
                    "status_in": ["completed"],
                    "tool_calls_include": ["search_restaurants"],
                    "tool_calls_exclude": ["food_decision", "plan_route", "memory_search"],
                },
            },
        ],
    },
    {
        "id": "deterministic-route-clarification",
        "message": "怎么走呢",
        "scene": "eat",
        "covers_quality_findings": ["route_memory_tool_leak"],
        "expect": {
            "no_fallback": True,
            "no_tool_calls": True,
            "worker": "route_planner",
            "intent_in": ["route", "unknown"],
            "status_in": ["needs_clarification"],
        },
    },
    {
        "id": "deterministic-restaurant-selection-route-multiturn",
        "covers_quality_findings": ["restaurant_selection_ack", "restaurant_route_context_loss", "route_memory_tool_leak"],
        "turns": [
            {
                "message": "我在人民广场附近想吃粤菜",
                "scene": "eat",
                "expect": {
                    "no_fallback": True,
                    "worker": "food_advisor",
                    "intent_in": ["eat_out", "unknown"],
                    "status_in": ["completed"],
                    "tool_calls_include": ["search_restaurants"],
                    "tool_calls_exclude": ["food_decision", "plan_route", "memory_search"],
                },
            },
            {
                "message": "人民广场粤味小馆吧",
                "scene": "eat",
                "expect": {
                    "no_fallback": True,
                    "no_tool_calls": True,
                    "worker": "food_advisor",
                    "intent_in": ["eat_out", "unknown"],
                    "status_in": ["completed"],
                    "tool_calls_exclude": ["food_decision", "plan_route", "memory_search"],
                    "recommendation_titles_include": ["人民广场粤味小馆"],
                },
            },
            {
                "message": "怎么走呢",
                "scene": "eat",
                "expect": {
                    "no_fallback": True,
                    "worker": "route_planner",
                    "intent_in": ["route", "unknown"],
                    "status_in": ["completed"],
                    "tool_calls_include": ["plan_route"],
                    "tool_calls_exclude": ["food_decision", "search_restaurants", "memory_search"],
                },
            },
        ],
    },
    {
        "id": "deterministic-restaurant-selection-from-persisted-context",
        "message": "五星之家",
        "scene": "chat",
        "covers_quality_findings": ["restaurant_selection_context_loss"],
        "context_overrides": {
            "last_restaurants": [
                {"name": "五星之家", "address": "洋湖附近", "rating": "4.5"},
                {"name": "屋门口土菜研究院(岳麓店)", "address": "岳麓区"},
            ]
        },
        "expect": {
            "no_fallback": True,
            "no_tool_calls": True,
            "worker": "food_advisor",
            "intent_in": ["eat_out", "unknown"],
            "status_in": ["completed"],
            "tool_calls_exclude": ["food_decision", "plan_route", "memory_search", "source_event_search"],
            "recommendation_titles_include": ["五星之家"],
        },
    },
    {
        "id": "deterministic-ordinal-restaurant-selection-from-context",
        "message": "就第二家吧",
        "scene": "chat",
        "covers_quality_findings": ["restaurant_selection_ack"],
        "context_overrides": {
            "last_restaurants": [
                {"name": "虹桥清淡小馆", "address": "虹桥火车站附近", "rating": "4.5"},
                {"name": "虹桥安静面馆", "address": "虹桥火车站步行 10 分钟", "rating": "4.6"},
            ]
        },
        "expect": {
            "no_fallback": True,
            "no_tool_calls": True,
            "worker": "food_advisor",
            "intent_in": ["eat_out", "unknown"],
            "status_in": ["completed"],
            "tool_calls_exclude": ["food_decision", "plan_route", "memory_search", "source_event_search"],
            "recommendation_titles_include": ["虹桥安静面馆"],
        },
    },
    {
        "id": "deterministic-route-from-restaurant",
        "message": "去阿娜尔麻辣干锅怎么走",
        "scene": "eat",
        "context_overrides": {
            "last_restaurants": [
                {
                    "name": "阿娜尔麻辣干锅",
                    "address": "人民广场东侧",
                    "lat": 31.2338,
                    "lng": 121.4821,
                    "rating": "4.6",
                }
            ],
            "cached_location": {"lat": 31.2304, "lng": 121.4737, "city": "上海"},
        },
        "expect": {
            "no_fallback": True,
            "worker": "route_planner",
            "intent_in": ["route", "unknown"],
            "status_in": ["completed"],
            "tool_calls_include": ["plan_route"],
            "tool_calls_exclude": ["memory_search", "search_restaurants"],
        },
    },
    {
        "id": "deterministic-restaurant-route-after-context-switch-multiturn",
        "covers_quality_findings": ["restaurant_selection_context_loss", "restaurant_route_context_loss", "route_memory_tool_leak"],
        "turns": [
            {
                "message": "我在南京新街口附近，今晚想吃清淡小馆，人均 70，找两三家。",
                "scene": "chat",
                "expect": {
                    "no_fallback": True,
                    "worker": "food_advisor",
                    "intent_in": ["eat_out", "unknown"],
                    "status_in": ["completed"],
                    "tool_calls_include": ["search_restaurants"],
                    "tool_calls_exclude": ["food_decision", "plan_route", "memory_search"],
                },
            },
            {
                "message": "先选第一家，理由短一点。",
                "scene": "chat",
                "expect": {
                    "no_fallback": True,
                    "no_tool_calls": True,
                    "worker": "food_advisor",
                    "intent_in": ["eat_out", "unknown"],
                    "status_in": ["completed"],
                    "tool_calls_exclude": ["food_decision", "plan_route", "memory_search", "source_event_search"],
                    "recommendation_titles_include": ["人民广场粤味小馆"],
                },
            },
            {
                "message": "先别管吃饭，陪我随便聊一句，今天有点累。",
                "scene": "chat",
                "expect": {
                    "no_fallback": True,
                    "no_tool_calls": True,
                    "worker": "general_chat",
                    "intent_in": ["chat", "unknown"],
                    "status_in": ["completed"],
                },
            },
            {
                "message": "明早如果在家吃，我有鸡蛋和青菜，10 分钟能做什么？",
                "scene": "chat",
                "expect": {
                    "no_fallback": True,
                    "worker": "home_chef",
                    "intent_in": ["cook_home", "unknown"],
                    "status_in": ["completed"],
                    "tool_calls_include_any": ["get_fridge_items", "rag_search_recipes", "search_recipes"],
                    "tool_calls_exclude": ["search_restaurants", "plan_route", "memory_search"],
                },
            },
            {
                "message": "还是回到刚才选的那家餐厅，从新街口过去怎么走？",
                "scene": "chat",
                "expect": {
                    "no_fallback": True,
                    "worker": "route_planner",
                    "intent_in": ["route", "unknown"],
                    "status_in": ["completed"],
                    "tool_calls_include": ["plan_route"],
                    "tool_calls_exclude": ["food_decision", "search_restaurants", "memory_search", "rag_search_recipes"],
                },
            },
        ],
    },
    {
        "id": "deterministic-home-chef",
        "message": "我冰箱里有鸡蛋和番茄，做什么快手菜",
        "scene": "home_chef",
        "expect": {
            "no_fallback": True,
            "worker": "home_chef",
            "intent_in": ["cook_home", "unknown"],
            "status_in": ["completed"],
            "tool_calls_include": ["get_fridge_items", "rag_search_recipes"],
            "tool_calls_exclude": ["search_restaurants", "memory_search"],
        },
    },
    {
        "id": "deterministic-travel-poi",
        "message": "帮我做杭州1天旅行攻略：西湖",
        "scene": "travel_planner",
        "covers_quality_findings": ["travel_tool_explosion"],
        "expect": {
            "no_fallback": True,
            "worker": "travel_planner",
            "intent_in": ["travel", "unknown"],
            "status_in": ["completed"],
            "tool_calls_include": ["travel_search_poi"],
            "tool_calls_exclude": ["food_decision", "search_restaurants"],
        },
    },
    {
        "id": "deterministic-travel-structured-chengdu-multiturn",
        "covers_quality_findings": [
            "travel_trip_meta_missing",
            "travel_prompt_text_extracted_as_poi",
            "travel_itinerary_day_mismatch",
        ],
        "turns": [
            {
                "message": (
                    "目的地：成都\n"
                    "出行时间：2026-06-10\n"
                    "出行天数：三天 2 晚\n"
                    "出行人数：1 人\n"
                    "我想去：宽窄巷子、武侯祠、杜甫草堂。"
                    "请先输出候选行程，等待我确认。"
                ),
                "scene": "travel_planner",
                "expect": {
                    "no_fallback": True,
                    "worker": "travel_planner",
                    "intent_in": ["travel", "unknown"],
                    "status_in": ["completed"],
                    "tool_calls_include": ["travel_search_poi"],
                    "tool_calls_exclude": ["food_decision", "search_restaurants"],
                    "trip_meta": {"destination": "成都", "days": 3},
                    "no_prompt_artifact_pois": True,
                },
            },
            {
                "message": "确认这些候选地点，请继续生成最终每日行程。",
                "scene": "travel_planner",
                "expect": {
                    "no_fallback": True,
                    "worker": "travel_planner",
                    "intent_in": ["travel", "unknown"],
                    "status_in": ["completed"],
                    "trip_meta": {"destination": "成都", "days": 3},
                    "min_itinerary_days": 3,
                    "no_prompt_artifact_pois": True,
                },
            },
        ],
    },
    {
        "id": "deterministic-travel-revision-rebuilds-context-multiturn",
        "covers_quality_findings": ["travel_revision_context_stale"],
        "turns": [
            {
                "message": "帮我做苏州 2 天旅行计划：拙政园、平江路、苏州博物馆、七里山塘。节奏慢一点。",
                "scene": "travel_planner",
                "expect": {
                    "no_fallback": True,
                    "worker": "travel_planner",
                    "intent_in": ["travel", "unknown"],
                    "status_in": ["completed"],
                    "tool_calls_include": ["travel_search_poi"],
                    "tool_calls_exclude": ["food_decision", "search_restaurants"],
                    "trip_meta": {"destination": "苏州", "days": 2},
                    "no_prompt_artifact_pois": True,
                },
            },
            {
                "message": "临时改成杭州 1 天，不去拙政园，只保留西湖和灵隐寺，别太赶。",
                "scene": "travel_planner",
                "expect": {
                    "no_fallback": True,
                    "worker": "travel_planner",
                    "intent_in": ["travel", "unknown"],
                    "status_in": ["completed"],
                    "tool_calls_include": ["travel_search_poi"],
                    "tool_calls_exclude": ["food_decision", "search_restaurants"],
                    "trip_meta": {"destination": "杭州", "days": 1},
                    "candidate_expected_any": ["西湖", "灵隐寺"],
                    "candidate_unexpected_any": ["拙政园", "平江路", "苏州博物馆", "七里山塘"],
                    "no_prompt_artifact_pois": True,
                },
            },
        ],
    },
    {
        "id": "deterministic-travel-food-route-multiturn",
        "turns": [
            {
                "message": "帮我做杭州1天旅行攻略：西湖",
                "scene": "travel_planner",
                "expect": {
                    "no_fallback": True,
                    "worker": "travel_planner",
                    "intent_in": ["travel", "unknown"],
                    "status_in": ["completed"],
                    "tool_calls_include": ["travel_search_poi"],
                    "tool_calls_exclude": ["food_decision", "search_restaurants"],
                },
            },
            {
                "message": "我在杭州旅行，附近有什么好吃的",
                "scene": "travel_planner",
                "expect": {
                    "no_fallback": True,
                    "worker": "food_advisor",
                    "intent_in": ["eat_out", "unknown"],
                    "status_in": ["completed"],
                    "tool_calls_include": ["search_restaurants"],
                    "tool_calls_exclude": ["travel_search_poi", "memory_search"],
                },
            },
            {
                "message": "去人民广场粤味小馆怎么走",
                "scene": "travel_planner",
                "expect": {
                    "no_fallback": True,
                    "worker": "route_planner",
                    "intent_in": ["route", "unknown"],
                    "status_in": ["completed"],
                    "tool_calls_include": ["plan_route"],
                    "tool_calls_exclude": ["search_restaurants", "travel_search_poi", "memory_search"],
                },
            },
        ],
    },
    {
        "id": "deterministic-travel-confirm-map-multiturn",
        "turns": [
            {
                "message": "帮我做杭州1天旅行攻略：西湖",
                "scene": "travel_planner",
                "expect": {
                    "no_fallback": True,
                    "worker": "travel_planner",
                    "intent_in": ["travel", "unknown"],
                    "status_in": ["completed"],
                    "tool_calls_include": ["travel_search_poi"],
                    "tool_calls_exclude": ["food_decision", "search_restaurants", "travel_create_personal_map"],
                },
            },
            {
                "message": "确认，继续生成每日行程",
                "scene": "travel_planner",
                "expect": {
                    "no_fallback": True,
                    "no_tool_calls": True,
                    "worker": "travel_planner",
                    "intent_in": ["travel", "unknown"],
                    "status_in": ["completed"],
                },
            },
            {
                "message": "确认，生成高德地图二维码",
                "scene": "travel_planner",
                "expect": {
                    "no_fallback": True,
                    "worker": "travel_planner",
                    "intent_in": ["travel", "unknown"],
                    "status_in": ["completed"],
                    "tool_calls_include": ["travel_create_personal_map"],
                    "tool_calls_exclude": ["food_decision", "search_restaurants"],
                },
            },
        ],
    },
]


class DeterministicPlanner:
    config = type("Config", (), {"name": "deterministic", "model_planner": "deterministic-replay"})()

    async def ainvoke_with_tools(
        self,
        messages: list[Any],
        tools: list[Any],
        image_parts: list[dict[str, Any]] | None = None,
    ) -> AIMessage:
        del image_parts
        tool_names = _tool_names(tools)
        latest_tool_name = _latest_tool_name(messages)
        text = _message_text(messages)

        if latest_tool_name == "get_fridge_items" and "rag_search_recipes" in tool_names:
            return _ai_tool_call("rag_search_recipes", {"query": "鸡蛋 番茄 快手菜"})

        if latest_tool_name and "submit_final_answer" in tool_names:
            return _ai_tool_call("submit_final_answer", _final_args_for_tool(latest_tool_name))

        if "travel_create_personal_map" in tool_names and "travel_search_poi" not in tool_names:
            return _ai_tool_call("travel_create_personal_map", {"title": "Smart Eats deterministic map", "line_list": []})

        if "travel_search_poi" in tool_names:
            return _ai_tool_call(
                "travel_search_poi",
                {"keywords": _travel_keyword(text), "city": "杭州", "category": "attraction", "page_size": 5},
            )

        if "search_restaurants" in tool_names and _looks_like_eat_out(text):
            return _ai_tool_call("search_restaurants", {"query": "粤菜", "lat": 31.2304, "lng": 121.4737, "city": "上海"})

        if "food_decision" in tool_names:
            return _ai_tool_call("food_decision", {"query": text or "今天吃什么", "scene": "food_decision"})

        if "get_fridge_items" in tool_names:
            return _ai_tool_call("get_fridge_items", {})

        if "plan_route" in tool_names and _looks_like_route(text):
            return _ai_tool_call(
                "plan_route",
                {
                    "origin_lat": 31.2304,
                    "origin_lng": 121.4737,
                    "destination_lat": 31.2338,
                    "destination_lng": 121.4821,
                    "mode": "walking",
                },
            )

        if "submit_final_answer" in tool_names:
            return _ai_tool_call(
                "submit_final_answer",
                {
                    "recommendations": [{"type": "note", "title": "已完成 deterministic replay", "reason": "deterministic_final"}],
                    "followups": [],
                    "warnings": [],
                },
            )

        return AIMessage(content="已完成 deterministic replay。", tool_calls=[])


class DeterministicToolNode:
    async def ainvoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        messages = payload.get("messages") if isinstance(payload, dict) else []
        latest_ai = next((item for item in reversed(messages or []) if isinstance(item, AIMessage)), None)
        tool_messages: list[ToolMessage] = []
        for call in (latest_ai.tool_calls if isinstance(latest_ai, AIMessage) else []) or []:
            if not isinstance(call, dict):
                continue
            name = call.get("name")
            args = call.get("args") if isinstance(call.get("args"), dict) else {}
            call_id = str(call.get("id") or f"call_{uuid4().hex[:8]}")
            result = _tool_result(str(name or ""), args)
            tool_messages.append(
                ToolMessage(
                    content=json.dumps(result, ensure_ascii=False),
                    name=str(name or ""),
                    tool_call_id=call_id,
                    artifact=result,
                )
            )
        return {"messages": tool_messages}


async def run_replay(
    cases: list[dict[str, Any]] | None = None,
    *,
    enforce_coverage: bool | None = None,
) -> dict[str, Any]:
    graph = build_supervisor_runtime_graph(
        db=None,
        redis_client=None,
        planner=DeterministicPlanner(),
        tool_node=DeterministicToolNode(),
    ).compile()
    selected_cases = cases or DETERMINISTIC_CASES
    results = []
    for case in selected_cases:
        if isinstance(case.get("turns"), list):
            results.append(await _run_multiturn_case(graph, case))
        else:
            output = await _run_graph_turn(
                graph,
                case,
                session_id=f"det-{case['id']}",
                context_overrides=case.get("context_overrides"),
            )
            results.append(_result_from_output(case, output))

    total = len(results)
    passed_count = sum(1 for item in results if (item.get("evaluation") or {}).get("passed"))
    coverage = coverage_report(results, selected_cases)
    failed = [
        {
            "id": item.get("id"),
            "message": item.get("message"),
            "violations": (item.get("evaluation") or {}).get("violations") or [],
        }
        for item in results
        if not (item.get("evaluation") or {}).get("passed")
    ]
    if (enforce_coverage if enforce_coverage is not None else cases is None) and not coverage.get("passed"):
        failed.append(_coverage_failure_entry(coverage))
    report = {
        "total": total,
        "passed_count": passed_count,
        "pass_rate": (passed_count / total) if total else 0.0,
        "fallback_count": sum(1 for item in results if item.get("fallback")),
        "active_tool_counts": active_value_counts(results, "active_tools"),
        "active_skill_counts": active_value_counts(results, "active_skills"),
        "tool_call_counts": active_value_counts(results, "tool_calls"),
        "worker_tool_counts": worker_tool_counts(results),
        "worker_tool_call_counts": worker_tool_call_counts(results),
        "worker_tool_boundary_violations": worker_tool_boundary_violations(results),
        "coverage": coverage,
        "failed": failed,
        "results": results,
    }
    return report


def coverage_report(results: list[dict[str, Any]], cases: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    observed: dict[str, set[str]] = {
        "workers": set(),
        "statuses": set(),
        "intents": set(),
        "tool_calls": set(),
        "scenes": set(),
        "scenario_types": set(),
        "business_states": set(),
        "quality_issue_regressions": set(),
    }

    for result in results:
        if not isinstance(result, dict):
            continue
        observed["scenario_types"].add("multi_turn" if isinstance(result.get("turns"), list) else "single_turn")
        for item in _iter_result_items(result):
            _add_string(observed["workers"], item.get("worker"))
            _add_string(observed["statuses"], item.get("status"))
            _add_string(observed["intents"], item.get("intent"))
            answer = item.get("answer")
            if isinstance(answer, dict):
                _add_string(observed["business_states"], answer.get("state"))
            for tool in item.get("tool_calls") if isinstance(item.get("tool_calls"), list) else []:
                _add_string(observed["tool_calls"], tool)

    for case in cases or []:
        if not isinstance(case, dict):
            continue
        for finding_type in case.get("covers_quality_findings") if isinstance(case.get("covers_quality_findings"), list) else []:
            _add_string(observed["quality_issue_regressions"], finding_type)
        turns = case.get("turns")
        if isinstance(turns, list):
            for turn in turns:
                if isinstance(turn, dict):
                    _add_string(observed["scenes"], turn.get("scene"))
        else:
            _add_string(observed["scenes"], case.get("scene"))

    missing = {
        key: sorted(required - observed.get(key, set()))
        for key, required in REQUIRED_COVERAGE.items()
    }
    passed = not any(values for values in missing.values())
    return {
        "passed": passed,
        "required": {key: sorted(values) for key, values in REQUIRED_COVERAGE.items()},
        "observed": {key: sorted(values) for key, values in observed.items()},
        "missing": missing,
    }


def _coverage_failure_entry(coverage: dict[str, Any]) -> dict[str, Any]:
    missing = coverage.get("missing") if isinstance(coverage.get("missing"), dict) else {}
    violations = [
        f"coverage:{key}:missing:{','.join(values)}"
        for key, values in sorted(missing.items())
        if isinstance(values, list) and values
    ]
    return {"id": "__coverage__", "message": None, "violations": violations or ["coverage:failed"]}


async def _run_multiturn_case(graph: Any, case: dict[str, Any]) -> dict[str, Any]:
    session_id = f"det-{case['id']}"
    carried_context: dict[str, Any] = dict(case.get("context_overrides") or {})
    turn_results: list[dict[str, Any]] = []
    for index, turn in enumerate(case.get("turns") or []):
        if not isinstance(turn, dict):
            continue
        turn_context = _merge_context(carried_context, turn.get("context_overrides") if isinstance(turn.get("context_overrides"), dict) else {})
        output = await _run_graph_turn(
            graph,
            {**turn, "id": f"{case.get('id')}:{index + 1}"},
            session_id=session_id,
            context_overrides=turn_context,
        )
        result = _result_from_output({**turn, "id": f"{case.get('id')}:{index + 1}"}, output)
        turn_results.append(result)
        carried_context = _merge_context(turn_context, _context_from_result(result))

    return {
        "id": case.get("id"),
        "message": None,
        "fallback": any(item.get("fallback") for item in turn_results),
        "status": turn_results[-1].get("status") if turn_results else None,
        "worker": turn_results[-1].get("worker") if turn_results else None,
        "intent": turn_results[-1].get("intent") if turn_results else None,
        "failure_class": next((item.get("failure_class") for item in turn_results if item.get("failure_class")), None),
        "turns": turn_results,
        "evaluation": {
            "passed": all((item.get("evaluation") or {}).get("passed") for item in turn_results),
            "violations": [
                f"{item.get('id')}:{violation}"
                for item in turn_results
                for violation in ((item.get("evaluation") or {}).get("violations") or [])
            ],
        },
    }


async def _run_graph_turn(
    graph: Any,
    case: dict[str, Any],
    *,
    session_id: str,
    context_overrides: Any = None,
) -> dict[str, Any]:
    return await graph.ainvoke(
        {
            "session_id": session_id,
            "trace_id": f"trace-{case.get('id')}",
            "message": case["message"],
            "scene": case.get("scene") or "chat",
            "context_overrides": context_overrides if isinstance(context_overrides, dict) and context_overrides else None,
            "steps_left": 6,
        }
    )


def _result_from_output(case: dict[str, Any], output: dict[str, Any]) -> dict[str, Any]:
    agent_result = output.get("agent_result") if isinstance(output.get("agent_result"), dict) else {}
    diagnostics = agent_result.get("diagnostics") if isinstance(agent_result, dict) else {}
    route = diagnostics.get("route") if isinstance(diagnostics, dict) else {}
    final = agent_result.get("final") if isinstance(agent_result.get("final"), dict) else output.get("final_json") or {}
    failure_class = agent_result.get("failure_class") if isinstance(agent_result, dict) else None
    result = {
        "id": case.get("id"),
        "message": case.get("message"),
        "fallback": bool(failure_class) or is_fallback(final),
        "status": agent_result.get("status") if isinstance(agent_result, dict) else None,
        "worker": route.get("worker") if isinstance(route, dict) else agent_result.get("worker"),
        "intent": route.get("intent") if isinstance(route, dict) else None,
        "failure_class": failure_class,
        "trace_id": output.get("trace_id") or f"trace-{case.get('id')}",
        "active_tools": _string_list(diagnostics.get("active_tools") if isinstance(diagnostics, dict) else None),
        "active_skills": _active_skill_ids(diagnostics.get("active_skills") if isinstance(diagnostics, dict) else None),
        "tool_calls": _tool_call_names(diagnostics.get("tools") if isinstance(diagnostics, dict) else output.get("tool_calls")),
        "answer": final,
        "agent_result": agent_result,
    }
    result["evaluation"] = evaluate_result(case, result)
    return result


def _context_from_result(result: dict[str, Any]) -> dict[str, Any]:
    final = result.get("answer") if isinstance(result.get("answer"), dict) else {}
    context: dict[str, Any] = {}
    if _is_travel_final(final):
        context["latest_travel_final_json"] = final

    restaurants = _restaurants_from_final(final)
    if restaurants:
        context["last_restaurants"] = restaurants
        context.setdefault("cached_location", {"lat": 31.2304, "lng": 121.4737, "city": "杭州"})
    selected = _selected_restaurant_from_final(final, restaurants)
    if selected:
        context["selected_restaurant"] = selected
    return context


def _is_travel_final(final: dict[str, Any]) -> bool:
    return any(key in final for key in ("state", "candidates", "itinerary", "map")) and final.get("state") is not None


def _restaurants_from_final(final: dict[str, Any]) -> list[dict[str, Any]]:
    recommendations = final.get("recommendations")
    if not isinstance(recommendations, list):
        return []
    restaurants: list[dict[str, Any]] = []
    for item in recommendations:
        if not isinstance(item, dict) or item.get("type") != "restaurant":
            continue
        raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
        name = str(raw.get("name") or item.get("title") or "").strip()
        if not name:
            continue
        row = dict(raw)
        row["name"] = name
        lat = row.get("lat") if row.get("lat") is not None else row.get("latitude")
        lng = row.get("lng") if row.get("lng") is not None else row.get("longitude")
        if lat is not None and lng is not None:
            row["geo"] = {"lat": lat, "lng": lng}
        restaurants.append(row)
    return restaurants


def _selected_restaurant_from_final(final: dict[str, Any], restaurants: list[dict[str, Any]]) -> dict[str, Any] | None:
    selected = final.get("selected_restaurant")
    if isinstance(selected, dict):
        row = dict(selected)
        name = str(row.get("name") or row.get("title") or "").strip()
        if name:
            for restaurant in restaurants:
                if str(restaurant.get("name") or "").strip() == name:
                    return {**restaurant, **row}
            return row
    if len(restaurants) == 1:
        return restaurants[0]
    return None


def _merge_context(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_context(merged[key], value)
        else:
            merged[key] = value
    return merged


def _iter_result_items(result: dict[str, Any]):
    yield result
    turns = result.get("turns")
    if isinstance(turns, list):
        for turn in turns:
            if isinstance(turn, dict):
                yield turn


def _add_string(values: set[str], value: Any) -> None:
    if isinstance(value, str) and value:
        values.add(value)


def _ai_tool_call(name: str, args: dict[str, Any]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": f"call_{uuid4().hex[:10]}", "type": "tool_call"}],
    )


def _tool_names(tools: list[Any]) -> set[str]:
    return {name for name in (getattr(tool, "name", None) for tool in tools) if isinstance(name, str)}


def _latest_tool_name(messages: list[Any]) -> str | None:
    for message in reversed(messages or []):
        name = getattr(message, "name", None)
        if isinstance(message, ToolMessage) and isinstance(name, str) and name:
            return name
    return None


def _message_text(messages: list[Any]) -> str:
    for message in reversed(messages or []):
        content = getattr(message, "content", None)
        if getattr(message, "type", None) == "human" and isinstance(content, str) and content.strip():
            return content.strip()
    for message in reversed(messages or []):
        content = getattr(message, "content", None)
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ""


def _looks_like_eat_out(text: str) -> bool:
    return any(token in text for token in ("附近", "餐厅", "饭店", "粤菜", "好吃", "吃"))


def _looks_like_route(text: str) -> bool:
    return any(token in text for token in ("怎么走", "怎么去", "导航", "路线"))


def _travel_keyword(text: str) -> str:
    for token in ("宽窄巷子", "武侯祠", "杜甫草堂", "拙政园", "平江路", "苏州博物馆", "七里山塘", "西湖", "灵隐寺", "浅草寺", "东京塔"):
        if token in text:
            return token
    return "西湖"


def _final_args_for_tool(tool_name: str) -> dict[str, Any]:
    titles = {
        "plan_route": "路线已规划",
        "rag_search_recipes": "可以做番茄炒蛋",
        "travel_search_poi": "已验证候选地点",
        "travel_create_personal_map": "旅行地图已生成",
    }
    return {
        "recommendations": [
            {
                "type": "note",
                "title": titles.get(tool_name, "已完成 deterministic replay"),
                "reason": f"based_on_{tool_name}",
            }
        ],
        "followups": [],
        "warnings": [],
    }


def _tool_result(name: str, args: dict[str, Any]) -> Any:
    if name == "submit_final_answer":
        return {"_final_answer": args}
    if name == "food_decision":
        return {
            "decision": {"type": "dish", "title": "番茄炒蛋配米饭"},
            "reasons": ["快手", "食材常见", "适合当前场景"],
            "actions": [{"label": "换成附近餐厅"}, {"label": "给我做法"}],
        }
    if name == "search_restaurants":
        return [
            {
                "name": "人民广场粤味小馆",
                "address": "人民广场步行 8 分钟",
                "rating": "4.7",
                "price": "人均 88",
                "lat": 31.2338,
                "lng": 121.4821,
            }
        ]
    if name == "plan_route":
        return {
            "distance_m": 980,
            "duration_s": 780,
            "origin": {"lat": args.get("origin_lat"), "lng": args.get("origin_lng")},
            "destination": {"lat": args.get("destination_lat"), "lng": args.get("destination_lng")},
            "mode": args.get("mode") or "walking",
            "steps": [{"instruction": "沿人民大道向东步行约 800 米"}, {"instruction": "右转后到达目的地"}],
        }
    if name == "get_fridge_items":
        return {"items": [{"name": "鸡蛋"}, {"name": "番茄"}]}
    if name == "rag_search_recipes":
        return {"items": [{"title": "番茄炒蛋", "snippet": "鸡蛋滑嫩，番茄出汁，10 分钟可以完成。"}]}
    if name == "travel_search_poi":
        keyword = str(args.get("keywords") or "西湖")
        poi_by_keyword = {
            "宽窄巷子": {
                "poi_id": "poi-kuanzhai",
                "name": "宽窄巷子",
                "address": "成都市青羊区长顺上街",
                "longitude": 104.043,
                "latitude": 30.67,
                "type": "风景名胜",
            },
            "武侯祠": {
                "poi_id": "poi-wuhou",
                "name": "成都武侯祠博物馆",
                "address": "成都市武侯区武侯祠大街231号",
                "longitude": 104.047,
                "latitude": 30.645,
                "type": "博物馆",
            },
            "杜甫草堂": {
                "poi_id": "poi-dufu",
                "name": "杜甫草堂博物馆",
                "address": "成都市青羊区青华路37号",
                "longitude": 104.028,
                "latitude": 30.66,
                "type": "风景名胜",
            },
            "拙政园": {
                "poi_id": "poi-zhuozheng",
                "name": "拙政园",
                "address": "苏州市姑苏区东北街178号",
                "longitude": 120.63,
                "latitude": 31.324,
                "type": "风景名胜",
            },
            "平江路": {
                "poi_id": "poi-pingjiang",
                "name": "平江路",
                "address": "苏州市姑苏区平江路",
                "longitude": 120.631,
                "latitude": 31.315,
                "type": "风景名胜",
            },
            "苏州博物馆": {
                "poi_id": "poi-suzhou-museum",
                "name": "苏州博物馆",
                "address": "苏州市姑苏区东北街204号",
                "longitude": 120.627,
                "latitude": 31.324,
                "type": "博物馆",
            },
            "七里山塘": {
                "poi_id": "poi-shantang",
                "name": "七里山塘",
                "address": "苏州市姑苏区山塘街",
                "longitude": 120.594,
                "latitude": 31.319,
                "type": "风景名胜",
            },
            "灵隐寺": {
                "poi_id": "poi-lingyin",
                "name": "灵隐寺",
                "address": "杭州市西湖区法云弄1号",
                "longitude": 120.102,
                "latitude": 30.24,
                "type": "风景名胜",
            },
        }
        selected = poi_by_keyword.get(keyword) or {
            "poi_id": "poi-west-lake",
            "name": "西湖风景名胜区",
            "address": "杭州市西湖区龙井路1号",
            "longitude": 120.141,
            "latitude": 30.25,
            "type": "风景名胜",
        }
        return {
            "query": {"keywords": keyword, "category": args.get("category") or "attraction"},
            "source_name": keyword,
            "selected_poi": selected,
            "pois": [selected],
        }
    if name == "travel_create_personal_map":
        return {
            "title": args.get("title") or "Smart Eats 旅行地图",
            "qr_code_url": "https://example.com/deterministic-map.png",
            "schema_url": "amapuri://workInAmap/createWithToken?polymericId=deterministic",
            "line_list": args.get("line_list") if isinstance(args.get("line_list"), list) else [],
            "message": "deterministic map generated",
        }
    return {"ok": True}


def _string_list(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _active_skill_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    ids: list[str] = []
    for item in value:
        if isinstance(item, str) and item:
            ids.append(item)
        elif isinstance(item, dict) and isinstance(item.get("id"), str):
            ids.append(item["id"])
    return ids


def _tool_call_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        if isinstance(item, str) and item:
            names.append(item)
        elif isinstance(item, dict):
            name = item.get("name") or item.get("tool") or item.get("tool_name")
            if isinstance(name, str) and name:
                names.append(name)
    return names


def main() -> None:
    parser = argparse.ArgumentParser(description="Run deterministic in-process Smart Eats agent replay")
    parser.add_argument("--out", default="deterministic_replay_report.json", help="Output JSON report path")
    args = parser.parse_args()

    report = asyncio.run(run_replay())
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
