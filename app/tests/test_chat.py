import asyncio
import json

import pytest

from app.agent.agents.smart_eats import _best_effort_final_from_observations, get_smart_eats_agent_config
from app.agent.graph import _render_final_text
from app.agent.state import ChatState


@pytest.mark.asyncio
async def test_chat_stream_stop(client):
    resp = await client.post("/api/v1/chat/sessions")
    assert resp.status_code == 200
    session_id = resp.json()["data"]["session_id"]

    got_tool_call = False
    got_delta = False
    got_final = False
    stopped_flag = None
    current_event = None
    async def send_stop():
        await asyncio.sleep(0.1)
        stop_resp = await client.post(f"/api/v1/chat/sessions/{session_id}/stop")
        assert stop_resp.status_code == 200

    stop_task = asyncio.create_task(send_stop())

    async with client.stream(
        "POST",
        f"/api/v1/chat/sessions/{session_id}/stream",
        json={"message": "quick dinner"},
    ) as response:
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                current_event = line.split(":", 1)[1].strip()
                continue
            if line.startswith("data:"):
                raw = line.split(":", 1)[1].strip()
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = {}

                if current_event == "tool_call":
                    got_tool_call = True
                if current_event == "delta":
                    got_delta = True
                if current_event == "final":
                    got_final = True
                    stopped_flag = payload.get("stopped")
                    break

    await stop_task

    assert got_final
    assert stopped_flag is True


def test_render_final_text_with_recommendations_followups_warnings():
    final_json = {
        "recommendations": [
            {"type": "note", "title": "推荐番茄炒蛋", "reason": "简单快手"},
            {"type": "note", "title": "推荐青椒肉丝"},
        ],
        "followups": ["想要10分钟内完成", "偏清淡口味"],
        "warnings": ["食材过敏请先确认"],
    }

    text = _render_final_text(final_json)

    assert "推荐番茄炒蛋（简单快手）" in text
    assert "推荐青椒肉丝" in text
    assert "**你可以继续：**" in text
    assert "想要10分钟内完成" in text
    assert "偏清淡口味" in text
    assert "**注意：**" in text
    assert "食材过敏请先确认" in text


def test_render_final_text_empty_returns_default():
    text = _render_final_text({"recommendations": [], "followups": [], "warnings": []})

    assert text == "好的。"


def test_best_effort_with_empty_fridge_avoids_fallback():
    state = ChatState(session_id="s1", context={"fridge_items": []})

    final_json = _best_effort_final_from_observations(state, get_smart_eats_agent_config())

    assert final_json["recommendations"][0]["reason"] != "fallback"
    assert "冰箱" in final_json["recommendations"][0]["title"]


def test_best_effort_with_rag_recipe_results_avoids_fallback():
    state = ChatState(
        session_id="s1",
        observations=[
            {
                "tool": "rag_search_recipes",
                "result": {
                    "items": [
                        {"title": "番茄炒蛋", "snippet": "鸡蛋打散，番茄切块，先炒蛋后下番茄翻炒。"},
                        {"title": "青椒土豆丝", "snippet": "土豆切丝泡水，热锅快炒保持脆爽。"},
                    ]
                },
            }
        ],
    )

    final_json = _best_effort_final_from_observations(state, get_smart_eats_agent_config())

    assert final_json["recommendations"][0]["reason"] != "fallback"
    assert final_json["recommendations"][0]["type"] == "recipe"
    assert final_json["recommendations"][0]["title"] == "番茄炒蛋"


def test_best_effort_without_business_signal_falls_back():
    state = ChatState(session_id="s1")

    final_json = _best_effort_final_from_observations(state, get_smart_eats_agent_config())

    assert final_json["recommendations"][0]["reason"] == "fallback"
    assert "抱歉" in final_json["recommendations"][0]["title"]
