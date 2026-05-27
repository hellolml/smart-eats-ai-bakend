from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agent.tools.native import RuntimeContext
from app.common.config import settings
from app.infra.external.amap import amap


def _coerce_location(value: Any) -> tuple[float | None, float | None]:
    if isinstance(value, str) and "," in value:
        left, right = value.split(",", 1)
        try:
            return float(left), float(right)
        except (TypeError, ValueError):
            return None, None
    if isinstance(value, dict):
        lng = value.get("lng", value.get("longitude"))
        lat = value.get("lat", value.get("latitude"))
        try:
            return float(lng), float(lat)
        except (TypeError, ValueError):
            return None, None
    return None, None


def _normalize_poi(item: dict[str, Any]) -> dict[str, Any]:
    longitude, latitude = _coerce_location(item.get("location"))
    return {
        "poi_id": item.get("id") or item.get("poi_id"),
        "name": item.get("name"),
        "address": item.get("address"),
        "longitude": longitude,
        "latitude": latitude,
        "tel": item.get("tel"),
        "raw": item,
    }


def _is_valid_poi(item: dict[str, Any]) -> bool:
    return bool(
        item.get("poi_id")
        and item.get("name")
        and item.get("longitude") is not None
        and item.get("latitude") is not None
    )


def _cache_key(*, keywords: str, city: Any, types: Any, location: Any, page_size: int) -> str:
    payload = {
        "keywords": keywords.strip().lower(),
        "city": str(city or "").strip(),
        "types": str(types or "").strip(),
        "location": str(location or "").strip(),
        "page_size": page_size,
    }
    return f"travel:poi:search:{json.dumps(payload, ensure_ascii=True, sort_keys=True)}"


async def _load_cached_pois(redis_client: Any, key: str) -> list[dict[str, Any]] | None:
    if redis_client is None:
        return None
    try:
        raw = await redis_client.get(key)
    except Exception:
        return None
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(data, list):
        return None
    pois = [item for item in data if isinstance(item, dict) and _is_valid_poi(item)]
    return pois or None


async def _cache_valid_pois(redis_client: Any, key: str, pois: list[dict[str, Any]]) -> None:
    valid = [item for item in pois if _is_valid_poi(item)]
    if redis_client is None or not valid:
        return
    try:
        await redis_client.setex(
            key,
            settings.TRAVEL_POI_CACHE_TTL_SECONDS,
            json.dumps(valid, ensure_ascii=True),
        )
    except Exception:
        return


class TravelSearchPoiArgs(BaseModel):
    keywords: str = Field(..., description="POI search keywords.")
    city: str | None = Field(default=None, description="Optional city hint.")
    types: str | None = Field(default=None, description="Optional AMap POI type filter.")
    location: str | None = Field(default=None, description="Optional center location as lng,lat.")
    page_size: int | None = Field(default=None, description="Result page size, 1-20.")
    runtime_context: RuntimeContext = Field(default_factory=dict)


async def _travel_search_poi(
    keywords: str,
    city: str | None = None,
    types: str | None = None,
    location: str | None = None,
    page_size: int | None = None,
    runtime_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = runtime_context or {}
    keywords = str(keywords or "").strip()
    if not keywords:
        return {"error": "missing_keywords"}
    try:
        page_size = int(page_size) if page_size is not None else 5
    except (TypeError, ValueError):
        page_size = 5
    page_size = max(1, min(page_size, 20))
    redis_client = ctx.get("redis_client")
    cache_key = _cache_key(
        keywords=keywords,
        city=city,
        types=types,
        location=location,
        page_size=page_size,
    )
    cached_pois = await _load_cached_pois(redis_client, cache_key)
    if cached_pois:
        return {
            "query": {
                "keywords": keywords,
                "city": city,
                "types": types,
                "location": location,
            },
            "pois": cached_pois,
            "cache_hit": True,
        }

    pois = await amap.text_search(
        keywords=keywords,
        types=types,
        city=city,
        location=location,
        page_size=page_size,
        servers_path=ctx.get("servers_path"),
    )
    normalized = [_normalize_poi(item) for item in pois if isinstance(item, dict)]
    await _cache_valid_pois(redis_client, cache_key, normalized)
    return {
        "query": {
            "keywords": keywords,
            "city": city,
            "types": types,
            "location": location,
        },
        "pois": normalized,
        "cache_hit": False,
    }


async def travel_search_poi(args: dict[str, Any]) -> dict[str, Any]:
    runtime_context = {
        "redis_client": args.get("redis_client"),
        "servers_path": args.get("servers_path"),
    }
    return await _travel_search_poi(
        keywords=str(args.get("keywords") or ""),
        city=args.get("city"),
        types=args.get("types"),
        location=args.get("location"),
        page_size=args.get("page_size"),
        runtime_context=runtime_context,
    )


travel_search_poi_tool = StructuredTool.from_function(
    coroutine=_travel_search_poi,
    name="travel_search_poi",
    description=(
        "Search and verify travel POIs by keyword through AMap. "
        "Input: {keywords:string, city?:string, types?:string, location?:string, page_size?:integer}."
    ),
    args_schema=TravelSearchPoiArgs,
    infer_schema=False,
)
