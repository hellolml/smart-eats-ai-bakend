from __future__ import annotations

from app.agent.runtime.finalization import final_json_from_text


def test_text_fallback_does_not_expose_planner_text_marker():
    answer = final_json_from_text("帮我推荐个晚饭")

    assert answer["recommendations"][0]["type"] == "note"
    assert answer["recommendations"][0]["title"] == "帮我推荐个晚饭"
    assert answer["recommendations"][0]["reason"] is None


def test_text_fallback_strips_embedded_legacy_tool_block():
    raw = '''
我来帮你查一下。
<tool_calls><tool_call>{"tool_name": "some_tool", "args": {"x": 1}}</tool_call></tool_calls>
'''

    answer = final_json_from_text(raw)

    assert "tool_calls" not in answer["recommendations"][0]["title"]
    assert "我来帮你查一下" in answer["recommendations"][0]["title"]


def test_bare_answer_json_is_normalized_to_final():
    raw = '''
{
  "recommendations": [{"type": "note", "title": "已完成", "reason": "ok"}],
  "followups": ["还需要继续吗？"],
  "warnings": []
}
'''

    answer = final_json_from_text(raw)

    assert answer["recommendations"][0]["title"] == "已完成"
    assert answer["followups"] == ["还需要继续吗？"]


def test_final_wrapper_json_is_normalized_to_answer():
    raw = '''
{
  "type": "final",
  "answer": {
    "recommendations": ["纯文本建议"],
    "followups": [],
    "warnings": []
  }
}
'''

    answer = final_json_from_text(raw)

    assert answer["recommendations"][0] == {
        "type": "note",
        "title": "纯文本建议",
        "reason": None,
    }


def test_text_fallback_strips_thinking_blocks():
    answer = final_json_from_text(
        """
<think>内部推理不要输出</think>
我建议先补充关键信息。
"""
    )

    assert "内部推理" not in answer["recommendations"][0]["title"]
    assert "补充关键信息" in answer["recommendations"][0]["title"]


def test_incomplete_legacy_tool_tag_is_final_not_tool_call():
    answer = final_json_from_text("<tool_calls>")

    assert answer["recommendations"][0]["reason"] == "planner_output_incomplete"


def test_json_legacy_tool_call_payload_is_not_parsed_as_tool_call():
    answer = final_json_from_text('{"type": "tool_calls", "calls": [{"some_tool": {}}]}')

    assert answer["recommendations"][0]["reason"] == "unsupported_tool_call_text"
