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
