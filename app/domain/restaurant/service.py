from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import redis.asyncio as redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.external import amap, meituan
from app.infra.models.restaurant import RestaurantCache, RestaurantSearchLog
from app.tasks import restaurant_summarize


class RestaurantService:
    @staticmethod
    async def search(
        db: AsyncSession,
        redis_client: redis.Redis,
        user_id: str | None,
        query: str | None,
        tag: str | None,
        lat: float | None,
        lng: float | None,
        sort: str | None,
    ) -> list[dict[str, Any]]:
        cache_key = f"restaurant:search:{query}:{tag}:{lat}:{lng}:{sort}"
        cached = await redis_client.get(cache_key)
        if cached:
            return json.loads(cached)

        amap_results = amap.search_restaurants(query, tag, lat, lng, limit=5)
        meituan_results = meituan.search_restaurants(query, tag, lat, lng, limit=5)
        results = amap_results + meituan_results

        await _persist_search(db, user_id, query, tag, lat, lng, sort, results)
        await redis_client.setex(cache_key, 300, json.dumps(results, ensure_ascii=True))
        return results

    @staticmethod
    async def get_detail(
        db: AsyncSession,
        redis_client: redis.Redis,
        provider: str,
        provider_id: str,
    ) -> dict[str, Any] | None:
        cache_key = f"restaurant:detail:{provider}:{provider_id}"
        cached = await redis_client.get(cache_key)
        if cached:
            return json.loads(cached)

        result = await db.execute(
            select(RestaurantCache).where(
                RestaurantCache.provider == provider,
                RestaurantCache.provider_id == provider_id,
            )
        )
        record = result.scalar_one_or_none()
        if record:
            tags = record.tags or await restaurant_summarize.summarize_tags(record.raw_json)
            payload = {
                "id": record.id,
                "provider": record.provider,
                "provider_id": record.provider_id,
                "name": record.name,
                "geo": record.geo,
                "rating": record.rating,
                "price": record.price,
                "tags": tags,
                "raw": record.raw_json,
                "navigation": _build_navigation(record.geo),
                "ai_tags": tags,
            }
            await redis_client.setex(cache_key, 300, json.dumps(payload, ensure_ascii=True))
            return payload

        payload = {
            "id": str(uuid4()),
            "provider": provider,
            "provider_id": provider_id,
            "name": f"Restaurant {provider_id}",
            "geo": None,
            "rating": None,
            "price": None,
            "tags": [],
            "raw": {"mock": True},
            "navigation": None,
            "ai_tags": [],
        }
        await redis_client.setex(cache_key, 300, json.dumps(payload, ensure_ascii=True))
        return payload


async def _persist_search(
    db: AsyncSession,
    user_id: str | None,
    query: str | None,
    tag: str | None,
    lat: float | None,
    lng: float | None,
    sort: str | None,
    results: list[dict[str, Any]],
) -> None:
    log = RestaurantSearchLog(
        id=str(uuid4()),
        user_id=user_id or "anonymous",
        query=query or "",
        filters_json={"tag": tag, "sort": sort},
        geo={"lat": lat, "lng": lng},
    )
    db.add(log)

    for item in results:
        if (item.get("raw") or {}).get("mock"):
            continue
        record = RestaurantCache(
            id=str(uuid4()),
            provider=item.get("provider"),
            provider_id=item.get("provider_id"),
            name=item.get("name"),
            geo=item.get("geo"),
            rating=item.get("rating"),
            price=item.get("price"),
            tags=item.get("tags"),
            raw_json=item.get("raw"),
        )
        db.add(record)

    await db.commit()


def _build_navigation(geo: dict[str, Any] | None) -> dict[str, Any] | None:
    if not geo:
        return None
    lat = geo.get("lat")
    lng = geo.get("lng")
    if lat is None or lng is None:
        return None
    return {
        "provider": "amap",
        "lat": lat,
        "lng": lng,
        "url": f"https://uri.amap.com/navigation?to={lng},{lat}",
    }
