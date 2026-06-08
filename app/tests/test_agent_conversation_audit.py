from __future__ import annotations

import json

from scripts.agent_conversation_audit import Message, audit_turns, build_turns


def _message(
    *,
    session_id: str = "s1",
    scene: str = "chat",
    role: str,
    content: str = "",
    tool_name: str | None = None,
    tool_payload_json: str | None = None,
) -> Message:
    return Message(
        session_id=session_id,
        scene=scene,
        title="fixture",
        role=role,
        content=content,
        tool_name=tool_name,
        tool_payload_json=tool_payload_json,
        created_at="2026-06-08 00:00:00",
    )


def test_conversation_audit_detects_quality_findings():
    messages: list[Message] = [
        _message(role="user", content="成都旅行攻略", scene="travel_planner"),
        *[
            _message(role="tool", tool_name="travel_search_poi", scene="travel_planner")
            for _ in range(9)
        ],
        _message(role="assistant", content="候选地点", scene="travel_planner"),
        _message(role="user", content="怎么走"),
        _message(role="tool", tool_name="memory_search"),
        _message(role="tool", tool_name="plan_route"),
        _message(role="assistant", content="路线如下"),
        _message(role="user", content="出去吃", scene="eat"),
        _message(role="tool", tool_name="search_restaurants", scene="eat"),
        _message(role="assistant", content="要不要我按距离、评分或口味再帮你筛一轮？", scene="eat"),
        _message(role="user", content="可以啊", scene="eat"),
        _message(role="tool", tool_name="food_decision", scene="eat"),
        _message(role="assistant", content="番茄炒蛋 Recipe", scene="eat"),
    ]

    report = audit_turns(build_turns(messages))

    assert report["findings_by_type"]["travel_tool_explosion"] == 1
    assert report["findings_by_type"]["route_memory_tool_leak"] == 1
    assert report["findings_by_type"]["food_affirmation_mode_drift"] == 1


def test_conversation_audit_detects_restaurant_selection_decision_drift():
    restaurant_payload = {
        "answer": {
            "recommendations": [
                {"type": "restaurant", "title": "长沙米粉(惟盛园店)"},
                {"type": "restaurant", "title": "味汁园(惟盛园店)"},
            ]
        }
    }
    messages: list[Message] = [
        _message(role="user", content="出去吃", scene="eat"),
        _message(
            role="assistant",
            content="长沙米粉、味汁园",
            scene="eat",
            tool_payload_json=json.dumps(restaurant_payload),
        ),
        _message(role="user", content="味汁园把", scene="eat"),
        _message(role="tool", tool_name="food_decision", scene="eat"),
        _message(role="assistant", content="美团搜索", scene="eat"),
    ]

    report = audit_turns(build_turns(messages))

    assert report["findings_by_type"]["restaurant_selection_ack"] == 1
    assert report["findings"][0]["reason"] == "user selected recent restaurant 味汁园(惟盛园店), but turn used food_decision"


def test_conversation_audit_detects_restaurant_selection_context_loss():
    restaurant_payload = {
        "answer": {
            "recommendations": [
                {"type": "restaurant", "title": "五星之家"},
                {"type": "restaurant", "title": "屋门口土菜研究院(岳麓店)"},
            ]
        }
    }
    messages: list[Message] = [
        _message(role="user", content="我想吃湘菜", scene="chat"),
        _message(
            role="assistant",
            content="五星之家、屋门口土菜研究院",
            scene="chat",
            tool_payload_json=json.dumps(restaurant_payload),
        ),
        _message(role="user", content="五星之家", scene="chat"),
        _message(role="tool", tool_name="memory_search", scene="chat"),
        _message(
            role="assistant",
            content="抱歉，我暂时无法查询到关于“五星之家”的更多信息",
            scene="chat",
            tool_payload_json=json.dumps({"agent_result": {"worker": "general_chat", "status": "completed"}}),
        ),
    ]

    report = audit_turns(build_turns(messages))

    assert report["findings_by_type"]["restaurant_selection_context_loss"] == 1


def test_conversation_audit_detects_ordinal_restaurant_selection_context_loss():
    restaurant_payload = {
        "answer": {
            "recommendations": [
                {"type": "restaurant", "title": "一号清粥小馆"},
                {"type": "restaurant", "title": "二号粤菜馆"},
            ]
        }
    }
    messages: list[Message] = [
        _message(role="user", content="我想找清淡餐厅", scene="chat"),
        _message(
            role="assistant",
            content="一号清粥小馆、二号粤菜馆",
            scene="chat",
            tool_payload_json=json.dumps(restaurant_payload),
        ),
        _message(role="user", content="就第二家吧", scene="chat"),
        _message(role="tool", tool_name="food_decision", scene="chat"),
        _message(role="assistant", content="推荐番茄炒蛋", scene="chat"),
    ]

    report = audit_turns(build_turns(messages))

    assert report["findings_by_type"]["restaurant_selection_ack"] == 1
    assert report["findings"][0]["reason"] == "user selected recent restaurant 二号粤菜馆, but turn used food_decision"


def test_conversation_audit_detects_restaurant_route_context_loss():
    restaurant_payload = {
        "answer": {
            "recommendations": [
                {"type": "restaurant", "title": "虹桥清淡小馆"},
                {"type": "restaurant", "title": "虹桥安静面馆"},
            ]
        }
    }
    messages: list[Message] = [
        _message(role="user", content="虹桥附近找餐厅", scene="chat"),
        _message(
            role="assistant",
            content="虹桥清淡小馆、虹桥安静面馆",
            scene="chat",
            tool_payload_json=json.dumps(restaurant_payload),
        ),
        _message(role="user", content="从虹桥火车站到第二家怎么走？", scene="chat"),
        _message(role="tool", tool_name="memory_search", scene="chat"),
        _message(role="assistant", content="你要去哪里？", scene="chat"),
    ]

    report = audit_turns(build_turns(messages))

    assert report["findings_by_type"]["restaurant_route_context_loss"] == 1
    route_finding = next(item for item in report["findings"] if item["type"] == "restaurant_route_context_loss")
    assert "二号" not in route_finding["reason"]
    assert "虹桥安静面馆" in route_finding["reason"]


def test_conversation_audit_detects_travel_context_failures():
    bad_travel_payload = {
        "answer": {
            "state": "candidates_ready",
            "trip_meta": {"destination": None, "days": None},
            "places": [
                {"name": "这样我可以提取地点后用高德验证POI，再为您排出一份靠谱的每日候选行程😊"}
            ],
            "candidates": [
                {"name": "这样我可以提取地点后用高德验证POI，再为您排出一份靠谱的每日候选行程😊"}
            ],
        }
    }
    short_itinerary_payload = {
        "answer": {
            "state": "itinerary_generated",
            "trip_meta": {"destination": "成都", "days": 3},
            "itinerary": {"days": [{"day_number": 1, "items": [{"place_name": "宽窄巷子"}]}]},
        }
    }
    messages: list[Message] = [
        _message(
            role="user",
            content="目的地：成都\n出行时间：2026-06-10\n出行天数：三天 2 晚 天\n出行人数：1 人",
            scene="travel_planner",
        ),
        _message(
            role="assistant",
            content="候选地点",
            scene="travel_planner",
            tool_payload_json=json.dumps(bad_travel_payload),
        ),
        _message(role="user", content="确认这些候选地点，请继续生成最终每日行程。", scene="travel_planner"),
        _message(
            role="assistant",
            content="Day 1",
            scene="travel_planner",
            tool_payload_json=json.dumps(short_itinerary_payload),
        ),
        _message(role="user", content="为什么只有 day1,不是 3 天两晚吗", scene="travel_planner"),
    ]

    report = audit_turns(build_turns(messages))

    assert report["findings_by_type"]["travel_trip_meta_missing"] == 1
    assert report["findings_by_type"]["travel_prompt_text_extracted_as_poi"] == 1
    assert report["findings_by_type"]["travel_itinerary_day_mismatch"] == 1
    assert report["findings_by_type"]["missing_assistant_response"] == 1


def test_conversation_audit_flags_no_tool_fallback():
    messages = [
        _message(role="user", content="你好"),
        _message(role="assistant", content="抱歉，我暂时没能完成这个请求。（fallback）"),
    ]

    report = audit_turns(build_turns(messages))

    assert report["findings_by_type"]["fallback"] == 1
    assert report["findings_by_type"]["no_tool_fallback"] == 1


def test_conversation_audit_separates_environment_failure_from_product_fallback():
    payload = {
        "answer": {
            "recommendations": [{"type": "note", "title": "模型订阅已过期", "reason": "fallback"}],
            "failure_class": "upstream_error",
            "status": "failed",
        },
        "agent_result": {
            "status": "failed",
            "worker": "food_advisor",
            "failure_class": "upstream_error",
            "diagnostics": {
                "provider_issue": {
                    "category": "provider_auth",
                    "code": "subscription_expired",
                    "action": "switch_model_or_refresh_provider_subscription",
                }
            },
            "final": {
                "recommendations": [{"type": "note", "title": "模型订阅已过期", "reason": "fallback"}],
                "failure_class": "upstream_error",
                "status": "failed",
                "provider_issue": {
                    "category": "provider_auth",
                    "code": "subscription_expired",
                    "action": "switch_model_or_refresh_provider_subscription",
                },
            },
        },
        "failure_class": "upstream_error",
    }
    messages = [
        _message(role="user", content="今天吃点啥"),
        _message(
            role="assistant",
            content="模型订阅已过期。（fallback）",
            tool_payload_json=json.dumps(payload),
        ),
    ]

    report = audit_turns(build_turns(messages))

    assert report["fallback_count"] == 0
    assert report["environment_failure_count"] == 1
    assert report["quality_finding_count"] == 0
    assert report["status_counts"] == {"failed": 1}
    assert report["worker_counts"] == {"food_advisor": 1}
    assert report["failure_class_counts"] == {"upstream_error": 1}
    assert report["provider_issue_counts"] == {"subscription_expired": 1}
    assert report["provider_issue_category_counts"] == {"provider_auth": 1}
    assert report["provider_action_counts"] == {"switch_model_or_refresh_provider_subscription": 1}
    assert report["findings_by_type"]["environment_failure"] == 1
    assert "fallback" not in report["findings_by_type"]
    assert "no_tool_fallback" not in report["findings_by_type"]


def test_conversation_audit_treats_missing_response_after_provider_failure_as_environment_noise():
    failed_payload = {
        "agent_result": {
            "status": "failed",
            "worker": "general_chat",
            "failure_class": "upstream_error",
            "diagnostics": {
                "provider_issue": {
                    "category": "provider_billing_unavailable",
                    "code": "provider_billing_unavailable",
                    "action": "recharge_provider_or_switch_model",
                }
            },
        },
        "failure_class": "upstream_error",
    }
    messages = [
        _message(role="user", content="你好", session_id="provider-blocked"),
        _message(
            role="assistant",
            content="模型余额不足",
            session_id="provider-blocked",
            tool_payload_json=json.dumps(failed_payload),
        ),
        _message(role="user", content="附近餐厅", session_id="provider-blocked"),
    ]

    report = audit_turns(build_turns(messages))

    assert report["environment_failure_count"] == 1
    assert report["quality_finding_count"] == 0
    assert report["findings_by_type"]["environment_failure"] == 1
    assert report["findings_by_type"]["environment_missing_assistant_response"] == 1
    assert "missing_assistant_response" not in report["findings_by_type"]


def test_conversation_audit_treats_session_without_any_agent_output_as_operational_noise():
    messages = [
        _message(role="user", content="帮我做成都三天旅行", session_id="no-output"),
        _message(role="user", content="确认候选地点", session_id="no-output"),
    ]

    report = audit_turns(build_turns(messages))

    assert report["quality_finding_count"] == 0
    assert report["findings_by_type"]["overlapping_user_turn_before_assistant"] == 1
    assert report["findings_by_type"]["incomplete_session_without_agent_output"] == 1
    assert "missing_assistant_response" not in report["findings_by_type"]


def test_conversation_audit_treats_user_overlap_before_assistant_as_operational_noise():
    messages = [
        _message(role="user", content="那怎么走？", session_id="overlap-food"),
        _message(role="user", content="我还没选地方，先找餐厅", session_id="overlap-food"),
        _message(role="assistant", content="你想去哪儿？", session_id="overlap-food"),
    ]

    report = audit_turns(build_turns(messages))

    assert report["quality_finding_count"] == 0
    assert report["findings_by_type"]["overlapping_user_turn_before_assistant"] == 1
    assert "missing_assistant_response" not in report["findings_by_type"]


def test_conversation_audit_treats_user_overlap_after_tool_as_operational_noise():
    messages = [
        _message(
            role="user",
            content="帮我做苏州 2 天旅行计划：拙政园、平江路、苏州博物馆。",
            scene="travel_planner",
            session_id="overlap-travel",
        ),
        _message(
            role="tool",
            tool_name="travel_search_poi",
            scene="travel_planner",
            session_id="overlap-travel",
        ),
        _message(
            role="user",
            content="临时改成杭州 1 天，只保留西湖和灵隐寺。",
            scene="travel_planner",
            session_id="overlap-travel",
        ),
        _message(
            role="assistant",
            content="已改成杭州一天候选地点。",
            scene="travel_planner",
            session_id="overlap-travel",
        ),
    ]

    report = audit_turns(build_turns(messages))

    assert report["quality_finding_count"] == 0
    assert report["findings_by_type"]["overlapping_user_turn_before_assistant"] == 1
    assert "missing_assistant_response" not in report["findings_by_type"]


def test_conversation_audit_treats_initial_missing_response_before_later_failure_as_operational_noise():
    failed_payload = {"agent_result": {"status": "failed", "failure_class": "upstream_error"}}
    messages = [
        _message(role="user", content="先帮我找餐厅", session_id="late-failure"),
        _message(role="user", content="怎么走", session_id="late-failure"),
        _message(
            role="assistant",
            content="模型失败",
            session_id="late-failure",
            tool_payload_json=json.dumps(failed_payload),
        ),
    ]

    report = audit_turns(build_turns(messages))

    assert report["quality_finding_count"] == 0
    assert report["findings_by_type"]["overlapping_user_turn_before_assistant"] == 1
    assert report["findings_by_type"]["environment_failure"] == 1
    assert "missing_assistant_response" not in report["findings_by_type"]
