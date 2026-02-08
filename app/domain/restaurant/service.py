from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import redis.asyncio as redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.config import settings
from app.infra.external.amap import amap
from app.infra.models.restaurant import RestaurantCache
from app.tasks import restaurant_summarize


class RestaurantService:
    @staticmethod
    async def search(
        redis_client: redis.Redis,
        query: str | None,
        tag: str | None,
        lat: float | None,
        lng: float | None,
        sort: str | None,
        city: str | None = None,
    ) -> list[dict[str, Any]]:
        cache_key = f"restaurant:search:{query}:{tag}:{lat}:{lng}:{sort}:{city}"
        cached = await redis_client.get(cache_key)
        if cached:
            cached_results = json.loads(cached)
            if cached_results:
                return cached_results

        keywords = query or tag or "美食"
        types = tag or "050000"
        if lat is not None and lng is not None:
            pois = await amap.around_search(
                f"{lng},{lat}",
                keywords,
                types,
                page_size=5,
                servers_path=settings.MCP_SERVERS_CONFIG_PATH,
            )
        else:
            pois = await amap.text_search(
                keywords,
                types,
                city=city,
                page_size=5,
                servers_path=settings.MCP_SERVERS_CONFIG_PATH,
            )
        if not pois:
            return []
        filtered = [item for item in pois if _is_food_poi(item)]
        if not filtered:
            return []
        results = [_normalize_poi(item) for item in filtered][:5]
        await redis_client.setex(
            cache_key,
            settings.AMAP_SEARCH_CACHE_TTL_SECONDS,
            json.dumps(results, ensure_ascii=True),
        )
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
            await redis_client.setex(
                cache_key,
                settings.RESTAURANT_DETAIL_CACHE_TTL_SECONDS,
                json.dumps(payload, ensure_ascii=True),
            )
            return payload

        if provider == "amap":
            detail = await amap.search_detail(
                provider_id,
                servers_path=settings.MCP_SERVERS_CONFIG_PATH,
            )
            if detail:
                normalized = _normalize_poi(detail)
                record = RestaurantCache(
                    id=str(uuid4()),
                    provider="amap",
                    provider_id=normalized.get("provider_id") or provider_id,
                    name=normalized.get("name"),
                    geo=normalized.get("geo"),
                    rating=normalized.get("rating"),
                    price=normalized.get("price"),
                    tags=normalized.get("tags"),
                    raw_json=normalized.get("raw"),
                )
                db.add(record)
                await db.commit()
                payload = {
                    "id": record.id,
                    "provider": record.provider,
                    "provider_id": record.provider_id,
                    "name": record.name,
                    "geo": record.geo,
                    "rating": record.rating,
                    "price": record.price,
                    "tags": record.tags or [],
                    "raw": record.raw_json,
                    "navigation": _build_navigation(record.geo),
                    "ai_tags": record.tags or [],
                }
                await redis_client.setex(
                    cache_key,
                    settings.RESTAURANT_DETAIL_CACHE_TTL_SECONDS,
                    json.dumps(payload, ensure_ascii=True),
                )
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
        await redis_client.setex(
            cache_key,
            settings.RESTAURANT_DETAIL_CACHE_TTL_SECONDS,
            json.dumps(payload, ensure_ascii=True),
        )
        return payload


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


def _parse_location(value: Any) -> dict[str, float] | None:
    if isinstance(value, dict):
        lat = value.get("lat")
        lng = value.get("lng")
        if lat is not None and lng is not None:
            return {"lat": float(lat), "lng": float(lng)}
    if isinstance(value, str) and "," in value:
        parts = value.split(",")
        if len(parts) >= 2:
            lng, lat = parts[0].strip(), parts[1].strip()
            try:
                return {"lat": float(lat), "lng": float(lng)}
            except ValueError:
                return None
    return None


def _normalize_poi(item: dict[str, Any]) -> dict[str, Any]:
    location = _parse_location(item.get("location") or item.get("geo"))
    tags = item.get("tags") or item.get("type") or []
    if isinstance(tags, str):
        tags = [tags]
    return {
        "provider": "amap",
        "provider_id": item.get("id") or item.get("uid") or str(uuid4()),
        "name": item.get("name") or item.get("title") or "",
        "rating": item.get("rating") or item.get("score"),
        "price": item.get("price") or item.get("per_capita") or item.get("cost"),
        "geo": location,
        "tags": tags,
        "raw": item,
        "typecode": item.get("typecode"),
    }


def _is_food_poi(item: dict[str, Any]) -> bool:
    typecode = str(item.get("typecode") or "")
    if typecode.startswith("05"):
        return True
    tags = item.get("tags") or item.get("type")
    if isinstance(tags, str):
        return "餐" in tags or "美食" in tags
    return False
