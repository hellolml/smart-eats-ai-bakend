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
async def test_food_decision_generic_eat_query_searches_restaurants_not_recipe(monkeypatch):
    captured: dict = {}

    class FakeRedis:
        async def get(self, _key):
            return None

        async def setex(self, *_args):
            return None

    async def fake_restaurant_search(_redis, query, tag, lat, lng, sort, city=None):
        captured["restaurant_query"] = query
        captured["lat"] = lat
        captured["lng"] = lng
        return []

    async def fail_recipe_search(*_args, **_kwargs):
        raise AssertionError("eat scene should not fall back to recipe placeholders")

    async def fake_ai_fallback(**_kwargs):
        return "番茄炒蛋盖饭"

    monkeypatch.setattr("app.domain.decision.service.RestaurantService.search", fake_restaurant_search)
    monkeypatch.setattr("app.domain.decision.service.RecipeService.search", fail_recipe_search)
    monkeypatch.setattr("app.domain.decision.service._generate_cn_home_style_fallback", fake_ai_fallback)

    result = await DecisionService.blindbox(
        None,
        FakeRedis(),
        user_id=None,
        query="今天吃点啥？",
        city=None,
        lat=31.23,
        lng=121.47,
        budget_level=None,
        scene="eat",
    )

    assert captured["restaurant_query"] == "美食"
    assert result["decision"]["title"] == "番茄炒蛋盖饭"


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
