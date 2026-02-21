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


def test_parse_tool_call_with_tool_name_and_args_format():
    raw = '''
<tool_calls>
<tool_call>
{"tool_name": "search_restaurants", "args": {"lat": 32.52, "lng": 108.53, "keyword": "麻辣烫"}}
</tool_call>
</tool_calls>
'''

    action = normalize_action_from_raw(raw)

    assert action is not None
    assert getattr(action, "type", None) == "tool_calls"
    assert len(action.calls) == 1
    assert action.calls[0]["search_restaurants"]["keyword"] == "麻辣烫"


def test_parse_tool_call_with_flattened_tool_name_payload():
    raw = '''
<tool_calls>
[{"tool_name": "search_restaurants", "query": "麻辣烫", "location": {"lat": 32.3, "lng": 108.3}, "radius": 2000}]
</tool_calls>
'''

    action = normalize_action_from_raw(raw)

    assert action is not None
    assert getattr(action, "type", None) == "tool_calls"
    assert action.calls[0]["search_restaurants"]["query"] == "麻辣烫"
    assert "location" in action.calls[0]["search_restaurants"]


def test_parse_tool_call_with_xml_tool_name_args_format():
    raw = '''
<tool_calls>
<tool_call>
<tool_name>search_restaurants</tool_name>
<args>{"query":"美食","lat":32.521417,"lng":108.532332}</args>
</tool_call>
</tool_calls>
'''

    action = normalize_action_from_raw(raw)

    assert action is not None
    assert getattr(action, "type", None) == "tool_calls"
    assert action.calls[0]["search_restaurants"]["lat"] == 32.521417


def test_text_fallback_strips_embedded_tool_calls_block():
    raw = '''
我来帮你找附近麻辣烫。
<tool_calls><tool_call>{"tool_name": "search_restaurants", "args": {"lat": 1, "lng": 2}}</tool_call></tool_calls>
'''

    action = normalize_action_from_raw(raw)

    assert action is not None
    assert getattr(action, "type", None) == "final"
    answer = action.answer.model_dump()
    assert "tool_calls" not in answer["recommendations"][0]["title"]


def test_bare_answer_json_is_normalized_to_final():
    raw = '''
{
  "recommendations": [{"type": "note", "title": "旺森农家味", "reason": "离你近"}],
  "followups": ["要不要我再按口味筛选？"],
  "warnings": []
}
'''

    action = normalize_action_from_raw(raw)

    assert action is not None
    assert getattr(action, "type", None) == "final"
    answer = action.answer.model_dump()
    assert answer["recommendations"][0]["title"] == "旺森农家味"


def test_text_fallback_strips_thinking_blocks():
    raw = '''
<think>内部推理不要输出</think>
我建议你做番茄鸡蛋面。
'''

    action = normalize_action_from_raw(raw)

    assert action is not None
    assert getattr(action, "type", None) == "final"
    answer = action.answer.model_dump()
    assert "内部推理" not in answer["recommendations"][0]["title"]
    assert "番茄鸡蛋面" in answer["recommendations"][0]["title"]
