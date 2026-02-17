import pytest

from app.common.config import settings
from app.domain.restaurant.service import RestaurantService


async def _register_and_get_headers(client, email: str) -> dict[str, str]:
    resp = await client.post(
        "/api/v1/app/auth/register",
        json={"email": email, "password": "secret123", "name": "eatout"},
    )
    assert resp.status_code == 200
    token = resp.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_app_restaurants_happy_path(client, monkeypatch):
    async def fake_search(redis_client, q, tag, lat, lng, sort, city=None):
        return [
            {
                "provider": "amap",
                "provider_id": "poi_001",
                "name": "老上海本帮菜",
                "rating": 4.8,
                "price": 88,
                "tags": ["剁椒鱼头必点"],
                "geo": {"lat": 31.23, "lng": 121.47},
                "source": "live",
            }
        ]

    async def fake_get_detail(db, redis_client, provider, provider_id):
        return {
            "provider": provider,
            "provider_id": provider_id,
            "name": "老上海本帮菜",
            "rating": 4.8,
            "price": 88,
            "tags": ["剁椒鱼头必点"],
            "geo": {"lat": 31.23, "lng": 121.47},
            "source": "live",
            "raw": {"id": provider_id},
        }

    monkeypatch.setattr(RestaurantService, "search", fake_search)
    monkeypatch.setattr(RestaurantService, "get_detail", fake_get_detail)

    headers = await _register_and_get_headers(client, "app_rest_happy@example.com")

    resp = await client.get(
        "/api/v1/app/restaurants",
        params={"q": "火锅", "lat": 31.23, "lng": 121.47},
        headers=headers,
    )
    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert len(rows) == 1
    first = rows[0]
    assert first["provider"] == "amap"
    assert first["provider_id"] == "poi_001"
    assert first["distance_text"] == "0m"
    assert first["price_text"] == "￥88/人"
    assert isinstance(first["navigation_url"], str)

    resp = await client.get(
        f"/api/v1/app/restaurants/{first['provider']}/{first['provider_id']}",
        headers=headers,
    )
    assert resp.status_code == 200
    detail = resp.json()["data"]
    assert detail["provider"] == first["provider"]
    assert detail["provider_id"] == first["provider_id"]
    assert detail["name"] == first["name"]
    assert isinstance(detail["navigation_url"], str)


@pytest.mark.asyncio
async def test_app_restaurants_empty_result_does_not_force_fallback(client, monkeypatch):
    async def fake_search(redis_client, q, tag, lat, lng, sort, city=None):
        return []

    monkeypatch.setattr(RestaurantService, "search", fake_search)
    monkeypatch.setattr(settings, "APP_FALLBACK_ENABLED", True)

    headers = await _register_and_get_headers(client, "app_rest_empty@example.com")
    resp = await client.get(
        "/api/v1/app/restaurants",
        params={"q": "很偏的关键词", "lat": 31.23, "lng": 121.47},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == []


@pytest.mark.asyncio
async def test_app_restaurants_fallback_only_on_exception(client, monkeypatch):
    async def fake_search(redis_client, q, tag, lat, lng, sort, city=None):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(RestaurantService, "search", fake_search)
    monkeypatch.setattr(settings, "APP_FALLBACK_ENABLED", True)

    headers = await _register_and_get_headers(client, "app_rest_fallback@example.com")
    resp = await client.get(
        "/api/v1/app/restaurants",
        params={"q": "火锅", "lat": 31.23, "lng": 121.47},
        headers=headers,
    )
    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert rows
    assert all(row["source"] == "fallback_mock" for row in rows)


@pytest.mark.asyncio
async def test_app_restaurants_no_fallback_when_disabled(client, monkeypatch):
    async def fake_search(redis_client, q, tag, lat, lng, sort, city=None):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(RestaurantService, "search", fake_search)
    monkeypatch.setattr(settings, "APP_FALLBACK_ENABLED", False)

    headers = await _register_and_get_headers(client, "app_rest_no_fallback@example.com")
    resp = await client.get(
        "/api/v1/app/restaurants",
        params={"q": "火锅", "lat": 31.23, "lng": 121.47},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == []


@pytest.mark.asyncio
async def test_app_restaurants_sort_orders(client, monkeypatch):
    async def fake_search(redis_client, q, tag, lat, lng, sort, city=None):
        return [
            {
                "provider": "amap",
                "provider_id": "far",
                "name": "Far",
                "rating": 4.9,
                "price": 20,
                "tags": ["火锅"],
                "geo": {"lat": 31.24, "lng": 121.50},
                "source": "live",
            },
            {
                "provider": "amap",
                "provider_id": "near",
                "name": "Near",
                "rating": 3.9,
                "price": 100,
                "tags": ["火锅"],
                "geo": {"lat": 31.23, "lng": 121.47},
                "source": "live",
            },
            {
                "provider": "amap",
                "provider_id": "mid",
                "name": "Mid",
                "rating": 4.5,
                "price": 60,
                "tags": ["火锅"],
                "geo": {"lat": 31.231, "lng": 121.471},
                "source": "live",
            },
        ]

    monkeypatch.setattr(RestaurantService, "search", fake_search)

    headers = await _register_and_get_headers(client, "app_rest_sort@example.com")

    nearest_resp = await client.get(
        "/api/v1/app/restaurants",
        params={"lat": 31.23, "lng": 121.47, "sort": "nearest"},
        headers=headers,
    )
    nearest_ids = [row["provider_id"] for row in nearest_resp.json()["data"]]
    assert nearest_ids == ["near", "mid", "far"]

    rating_resp = await client.get(
        "/api/v1/app/restaurants",
        params={"lat": 31.23, "lng": 121.47, "sort": "rating_desc"},
        headers=headers,
    )
    rating_ids = [row["provider_id"] for row in rating_resp.json()["data"]]
    assert rating_ids == ["far", "mid", "near"]

    price_resp = await client.get(
        "/api/v1/app/restaurants",
        params={"lat": 31.23, "lng": 121.47, "sort": "price_asc"},
        headers=headers,
    )
    price_ids = [row["provider_id"] for row in price_resp.json()["data"]]
    assert price_ids == ["far", "mid", "near"]


@pytest.mark.asyncio
async def test_app_restaurant_detail_not_found_returns_404(client, monkeypatch):
    async def fake_get_detail(db, redis_client, provider, provider_id):
        return None

    monkeypatch.setattr(RestaurantService, "get_detail", fake_get_detail)
    monkeypatch.setattr(settings, "APP_FALLBACK_ENABLED", True)

    headers = await _register_and_get_headers(client, "app_rest_detail_404@example.com")
    resp = await client.get(
        "/api/v1/app/restaurants/amap/not_found",
        headers=headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_app_restaurant_detail_fallback_on_exception(client, monkeypatch):
    async def fake_get_detail(db, redis_client, provider, provider_id):
        raise RuntimeError("detail service error")

    monkeypatch.setattr(RestaurantService, "get_detail", fake_get_detail)
    monkeypatch.setattr(settings, "APP_FALLBACK_ENABLED", True)

    headers = await _register_and_get_headers(client, "app_rest_detail_fallback@example.com")
    resp = await client.get(
        "/api/v1/app/restaurants/amap/poi_error",
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["source"] == "fallback_mock"


@pytest.mark.asyncio
async def test_app_restaurants_reject_invalid_sort(client):
    headers = await _register_and_get_headers(client, "app_rest_invalid_sort@example.com")
    resp = await client.get(
        "/api/v1/app/restaurants",
        params={"sort": "xxx"},
        headers=headers,
    )
    assert resp.status_code == 422
    assert "筛选参数不合法" in resp.json()["message"]


@pytest.mark.asyncio
async def test_app_restaurants_reject_single_coordinate(client):
    headers = await _register_and_get_headers(client, "app_rest_invalid_coord@example.com")
    resp = await client.get(
        "/api/v1/app/restaurants",
        params={"lat": 31.23},
        headers=headers,
    )
    assert resp.status_code == 422
    assert "筛选参数不合法" in resp.json()["message"]
