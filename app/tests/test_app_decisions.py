from __future__ import annotations

import pytest

from app.domain.decision.service import DecisionService


async def _register_and_get_headers(client, email: str) -> dict[str, str]:
    resp = await client.post(
        "/api/v1/app/auth/register",
        json={"email": email, "password": "secret123", "name": "decider"},
    )
    assert resp.status_code == 200
    token = resp.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_app_decision_blindbox(client, monkeypatch):
    async def fake_blindbox(*args, **kwargs):
        return {
            "decision": {"type": "restaurant", "title": "牛腩火锅", "confidence": 0.88},
            "reasons": ["天气适合热食", "晚餐时段更匹配"],
            "actions": [{"type": "navigate", "label": "高德导航", "url": "https://uri.amap.com/navigation"}],
            "meta": {"candidates": 8},
        }

    monkeypatch.setattr(DecisionService, "blindbox", fake_blindbox)

    headers = await _register_and_get_headers(client, "app_decision_blindbox@example.com")
    resp = await client.post(
        "/api/v1/app/decisions/blindbox",
        headers=headers,
        json={"query": "火锅", "city": "上海"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["decision"]["title"] == "牛腩火锅"
    assert data["actions"]


@pytest.mark.asyncio
async def test_app_quick_filter_flow(client, monkeypatch):
    async def fake_start(*args, **kwargs):
        return {
            "flow_id": "flow-123",
            "round": 1,
            "answers": {},
            "done": False,
            "next_question": {"slot": "flavor", "question": "清淡还是重口？", "options": ["清淡", "重口"]},
        }

    async def fake_answer(*args, **kwargs):
        return {
            "flow_id": "flow-123",
            "round": 4,
            "answers": {"flavor": "清淡", "carb": "饭", "scene": "外卖"},
            "done": True,
            "next_question": None,
            "result": {
                "decision": {"type": "restaurant", "title": "黄焖鸡米饭", "confidence": 0.76},
                "reasons": ["执行成本低"],
                "actions": [],
            },
        }

    monkeypatch.setattr(DecisionService, "quick_filter_start", fake_start)
    monkeypatch.setattr(DecisionService, "quick_filter_answer", fake_answer)

    headers = await _register_and_get_headers(client, "app_decision_qf@example.com")
    start = await client.post("/api/v1/app/decisions/quick-filter/start", headers=headers, json={"query": "晚饭"})
    assert start.status_code == 200
    assert start.json()["data"]["flow_id"] == "flow-123"

    answer = await client.post(
        "/api/v1/app/decisions/quick-filter/answer",
        headers=headers,
        json={"flow_id": "flow-123", "answer": "清淡"},
    )
    assert answer.status_code == 200
    data = answer.json()["data"]
    assert data["done"] is True
    assert data["result"]["decision"]["title"] == "黄焖鸡米饭"
