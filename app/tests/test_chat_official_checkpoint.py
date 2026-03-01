from __future__ import annotations

import json

import pytest


@pytest.mark.asyncio
async def test_chat_stream_official_runtime_resume_from_checkpoint(client, monkeypatch):
    from app.agent import factory

    class _Settings:
        AGENT_GRAPH_RUNTIME = "official"

    async def _fake_plan_tool_calls(self, system, user, available_tools):
        return {
            "content": "",
            "tool_calls": [
                {
                    "name": "submit_final_answer",
                    "args": {
                        "recommendations": [
                            {"type": "note", "title": "恢复成功", "reason": "resume_checkpoint"}
                        ],
                        "followups": [],
                        "warnings": [],
                    },
                    "id": "call_resume_final",
                    "type": "tool_call",
                }
            ],
        }

    async def _should_not_call_graph_helper(*_args, **_kwargs):
        raise AssertionError("smart_eats official runtime should not call app.agent.graph helpers")

    monkeypatch.setattr(factory, "settings", _Settings())
    monkeypatch.setattr("app.agent.llm_adapters.OpenAIPlanner.plan_tool_calls", _fake_plan_tool_calls)
    monkeypatch.setattr("app.agent.legacy_builder_helpers._ensure_chat_session", _should_not_call_graph_helper)
    monkeypatch.setattr("app.agent.legacy_builder_helpers._refresh_observation_context", _should_not_call_graph_helper)

    resp = await client.post("/api/v1/chat/sessions")
    assert resp.status_code == 200
    session_id = resp.json()["data"]["session_id"]

    got_final = False
    current_event = None

    async with client.stream(
        "POST",
        f"/api/v1/chat/sessions/{session_id}/stream",
        json={"message": "继续上次"},
    ) as response:
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                current_event = line.split(":", 1)[1].strip()
                continue
            if line.startswith("data:") and current_event == "final":
                payload = json.loads(line.split(":", 1)[1].strip())
                assert payload.get("stopped") is False
                answer = payload.get("answer") or {}
                recs = answer.get("recommendations") or []
                assert recs and recs[0].get("title") == "恢复成功"
                got_final = True
                break

    assert got_final
