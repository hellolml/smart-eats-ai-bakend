import pytest

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
async def test_v1_restaurants_search_reject_invalid_sort(client):
    headers = await _register_and_get_headers(client, "v1_rest_invalid_sort@example.com")
    resp = await client.get(
        "/api/v1/restaurants/search",
        params={"sort": "unknown"},
        headers=headers,
    )
    assert resp.status_code == 422
    assert "筛选参数不合法" in resp.json()["message"]


@pytest.mark.asyncio
async def test_v1_restaurants_search_reject_single_coordinate(client):
    headers = await _register_and_get_headers(client, "v1_rest_invalid_coord@example.com")
    resp = await client.get(
        "/api/v1/restaurants/search",
        params={"lng": 121.47},
        headers=headers,
    )
    assert resp.status_code == 422
    assert "筛选参数不合法" in resp.json()["message"]


@pytest.mark.asyncio
async def test_v1_restaurants_search_accepts_valid_enum_sort(client, monkeypatch):
    async def fake_search(redis_client, q, tag, lat, lng, sort, city=None):
        assert sort == "nearest"
        return [
            {
                "provider": "amap",
                "provider_id": "poi_001",
                "name": "测试餐厅",
                "rating": 4.5,
                "price": 50,
                "tags": ["火锅"],
                "geo": {"lat": 31.23, "lng": 121.47},
                "source": "live",
            }
        ]

    monkeypatch.setattr(RestaurantService, "search", fake_search)

    headers = await _register_and_get_headers(client, "v1_rest_valid_sort@example.com")
    resp = await client.get(
        "/api/v1/restaurants/search",
        params={"sort": "nearest", "lat": 31.23, "lng": 121.47},
        headers=headers,
    )
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 1
