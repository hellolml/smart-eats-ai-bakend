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
