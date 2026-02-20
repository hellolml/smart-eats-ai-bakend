from __future__ import annotations

from app.agent.agents.base import normalize_action_from_raw


def test_text_fallback_does_not_expose_planner_text_marker():
    action = normalize_action_from_raw("帮我推荐个晚饭")

    assert action is not None
    assert getattr(action, "type", None) == "final"

    answer = action.answer.model_dump()
    assert answer["recommendations"][0]["type"] == "note"
    assert answer["recommendations"][0]["title"] == "帮我推荐个晚饭"
    assert answer["recommendations"][0]["reason"] is None


def test_parse_tool_calls_with_nested_tool_call_blocks():
    raw = '''
<thinking>
我先分析一下用户意图
</thinking>
<tool_calls>
<tool_call>
{"search_restaurants": {"lat": 32.3, "lng": 108.3}}
</tool_call>
</tool_calls>
'''

    action = normalize_action_from_raw(raw)

    assert action is not None
    assert getattr(action, "type", None) == "tool_calls"
    assert len(action.calls) == 1
    assert "search_restaurants" in action.calls[0]


def test_parse_tool_calls_with_json_array_inside_tag():
    raw = '<tool_calls>[{"get_weather": {"city": "上海"}}]</tool_calls>'

    action = normalize_action_from_raw(raw)

    assert action is not None
    assert getattr(action, "type", None) == "tool_calls"
    assert len(action.calls) == 1
    assert action.calls[0]["get_weather"]["city"] == "上海"
