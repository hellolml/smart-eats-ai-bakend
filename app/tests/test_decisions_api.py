from __future__ import annotations

import pytest

from app.domain.recipe.service import RecipeService
from app.domain.restaurant.service import RestaurantService


@pytest.mark.asyncio
async def test_blindbox_returns_single_decision_with_actions(client, monkeypatch):
    async def fake_search_restaurants(redis_client, query, tag, lat, lng, sort, city=None):
        return [
            {
                "provider": "amap",
                "provider_id": "poi_001",
                "name": "牛腩火锅",
                "rating": 4.8,
                "price": 88,
                "geo": {"lat": 31.23, "lng": 121.47},
            }
        ]

    async def fake_search_recipes(redis_client, query):
        return [
            {
                "title": "番茄鸡蛋面",
                "cook_time_min": 15,
                "calories": 480,
            }
        ]

    async def fake_get_weather(city, *, servers_path):
        return {"city": city, "weather": "小雨", "temperature": 9, "display": "9°小雨"}

    monkeypatch.setattr(RestaurantService, "search", fake_search_restaurants)
    monkeypatch.setattr(RecipeService, "search", fake_search_recipes)
    monkeypatch.setattr("app.domain.decision.service.amap.get_weather", fake_get_weather)

    resp = await client.post(
        "/api/v1/decisions/blindbox",
        json={"city": "上海", "lat": 31.23, "lng": 121.47, "query": "火锅"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["decision"]["title"]
    assert data["decision"]["type"] in {"restaurant", "recipe", "fallback"}
    assert isinstance(data["reasons"], list) and data["reasons"]
    assert isinstance(data["actions"], list)


@pytest.mark.asyncio
async def test_blindbox_uses_ip_fallback_location_when_no_coords(client, monkeypatch):
    captured = {}

    async def fake_search_restaurants(redis_client, query, tag, lat, lng, sort, city=None):
        captured['lat'] = lat
        captured['lng'] = lng
        captured['city'] = city
        return [
            {
                "provider": "amap",
                "provider_id": "poi_ip_001",
                "name": "附近牛肉面",
                "rating": 4.7,
                "price": 35,
                "geo": {"lat": 31.2304, "lng": 121.4737},
            }
        ]

    async def fake_get_ip_location(ip, *, servers_path):
        assert ip == '8.8.8.8'
        return ({"lat": 31.2304, "lng": 121.4737}, "上海")

    monkeypatch.setattr(RestaurantService, "search", fake_search_restaurants)
    monkeypatch.setattr("app.domain.decision.service.amap.get_ip_location", fake_get_ip_location)

    resp = await client.post(
        "/api/v1/decisions/blindbox",
        headers={"x-forwarded-for": "8.8.8.8"},
        json={"query": "附近美食"},
    )
    assert resp.status_code == 200
    assert captured["lat"] == 31.2304
    assert captured["lng"] == 121.4737


@pytest.mark.asyncio
async def test_quick_filter_three_rounds_then_finalize(client, monkeypatch):
    async def fake_search_restaurants(redis_client, query, tag, lat, lng, sort, city=None):
        return [
            {
                "provider": "amap",
                "provider_id": "poi_002",
                "name": "黄焖鸡米饭",
                "rating": 4.6,
                "price": 28,
                "geo": {"lat": 31.2301, "lng": 121.4701},
            }
        ]

    async def fake_search_recipes(redis_client, query):
        return []

    monkeypatch.setattr(RestaurantService, "search", fake_search_restaurants)
    monkeypatch.setattr(RecipeService, "search", fake_search_recipes)

    start = await client.post("/api/v1/decisions/quick-filter/start", json={"query": "晚饭"})
    assert start.status_code == 200
    flow_id = start.json()["data"]["flow_id"]

    r1 = await client.post(
        "/api/v1/decisions/quick-filter/answer",
        json={"flow_id": flow_id, "answer": "清淡"},
    )
    assert r1.status_code == 200
    assert r1.json()["data"]["done"] is False

    r2 = await client.post(
        "/api/v1/decisions/quick-filter/answer",
        json={"flow_id": flow_id, "answer": "饭"},
    )
    assert r2.status_code == 200
    assert r2.json()["data"]["done"] is False

    r3 = await client.post(
        "/api/v1/decisions/quick-filter/answer",
        json={"flow_id": flow_id, "answer": "外卖", "lat": 31.23, "lng": 121.47},
    )
    assert r3.status_code == 200
    data = r3.json()["data"]
    assert data["done"] is True
    assert data["result"]["decision"]["title"]
