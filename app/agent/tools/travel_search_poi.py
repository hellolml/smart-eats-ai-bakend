from __future__ import annotations

import json
import re
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
    if longitude is None or latitude is None:
        longitude, latitude = _coerce_location(
            {
                "lng": item.get("lng", item.get("lon", item.get("longitude"))),
                "lat": item.get("lat", item.get("latitude")),
            }
        )
    return {
        "poi_id": item.get("id") or item.get("poi_id") or item.get("poiId"),
        "name": item.get("name"),
        "address": item.get("address"),
        "longitude": longitude,
        "latitude": latitude,
        "tel": item.get("tel"),
        "type": item.get("type") or item.get("typeName") or item.get("category"),
        "typecode": item.get("typecode") or item.get("typeCode"),
        "name_aliases": _coerce_alias_list(item.get("name_aliases") or item.get("aliases")),
        "raw": item,
    }


def _is_valid_poi(item: dict[str, Any]) -> bool:
    return bool(
        item.get("poi_id")
        and item.get("name")
        and item.get("longitude") is not None
        and item.get("latitude") is not None
    )


def _normalize_name_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[\s，。！？、,.!?:：；;（）()\[\]{}<>\-_'\"“”‘’/\\|]+", "", text)


def _coerce_alias_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,，/、|]", value) if item.strip()]
    return []


TRANSPORT_AFFIX_TOKENS = (
    "地铁站",
    "公交站",
    "停车场",
    "停车点",
    "出入口",
    "入口",
    "出口",
    "站台",
    "售票处",
    "服务区",
)

TRAVEL_PREFERRED_TOKENS = (
    "景区",
    "风景区",
    "博物馆",
    "文化",
    "遗址",
    "公园",
    "纪念馆",
    "名胜",
    "古镇",
    "古街",
    "寺",
    "祠",
    "山",
    "草堂",
    "巷子",
)


def _is_transport_affix_poi(item: dict[str, Any]) -> bool:
    text = " ".join(
        str(value or "")
        for value in (
            item.get("name"),
            item.get("address"),
            item.get("type"),
            item.get("typecode"),
        )
    )
    return any(token in text for token in TRANSPORT_AFFIX_TOKENS)


def _is_transport_source(category: Any, types: Any, keywords: str) -> bool:
    text = " ".join(str(value or "") for value in (category, types, keywords)).lower()
    return any(token in text for token in ("transport", "station", "地铁", "公交", "车站", "火车站", "机场"))


def _poi_match_score(item: dict[str, Any], *, keywords: str, aliases: list[str], source_category: Any, types: Any) -> tuple[int, str]:
    names = [keywords, *aliases]
    normalized_names = [_normalize_name_key(name) for name in names if _normalize_name_key(name)]
    poi_name = _normalize_name_key(item.get("name"))
    if not normalized_names or not poi_name:
        return 0, "missing_name"

    is_transport = _is_transport_affix_poi(item)
    source_is_transport = _is_transport_source(source_category, types, keywords)
    if is_transport and not source_is_transport:
        return -100, "交通附属点不是攻略地点本体"

    score = 0
    reason = "low_confidence"
    for name in normalized_names:
        if poi_name == name:
            score = max(score, 120)
            reason = "exact_name_or_alias"
        elif name in poi_name:
            score = max(score, 90)
            reason = "poi_contains_source_name"
        elif poi_name in name and len(poi_name) >= 3:
            score = max(score, 65)
            reason = "source_contains_poi_name"

    info_text = " ".join(str(value or "") for value in (item.get("name"), item.get("type"), item.get("address")))
    if any(token in info_text for token in TRAVEL_PREFERRED_TOKENS):
        score += 12
    if item.get("longitude") is not None and item.get("latitude") is not None:
        score += 5
    return score, reason


def _select_best_poi(
    pois: list[dict[str, Any]],
    *,
    keywords: str,
    aliases: list[str],
    source_category: Any,
    types: Any,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str]:
    rejected: list[dict[str, Any]] = []
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for item in pois:
        score, reason = _poi_match_score(
            item,
            keywords=keywords,
            aliases=aliases,
            source_category=source_category,
            types=types,
        )
        if score >= 70:
            scored.append((score, reason, item))
        else:
            rejected.append(
                {
                    "poi": item,
                    "reason": reason,
                    "score": score,
                }
            )
    if not scored:
        if rejected and all(str(item.get("reason")) == "交通附属点不是攻略地点本体" for item in rejected):
            return None, rejected, "only_transport_affix"
        return None, rejected, "no_confident_match"
    scored.sort(key=lambda row: row[0], reverse=True)
    selected_score, selected_reason, selected = scored[0]
    rejected.extend(
        {
            "poi": item,
            "reason": "lower_score_than_selected",
            "score": score,
        }
        for score, _reason, item in scored[1:]
    )
    selected = dict(selected)
    selected["match_score"] = selected_score
    selected["match_reason"] = selected_reason
    return selected, rejected, "selected"


def _cache_key(*, keywords: str, city: Any, types: Any, location: Any, page_size: int) -> str:
    payload = {
        "keywords": keywords.strip().lower(),
        "city": str(city or "").strip(),
        "types": str(types or "").strip(),
        "location": str(location or "").strip(),
        "page_size": page_size,
    }
    return f"travel:poi:search:{json.dumps(payload, ensure_ascii=True, sort_keys=True)}"


def _poi_name_key(*, city: Any, name: Any) -> str:
    return f"travel:poi:name:{str(city or '').strip().lower()}:{_normalize_name_key(name)}"


def _poi_alias_key(*, city: Any, alias: Any) -> str:
    return f"travel:poi:alias:{str(city or '').strip().lower()}:{_normalize_name_key(alias)}"


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


async def _load_cached_poi_by_name_or_alias(redis_client: Any, *, city: Any, name: Any) -> list[dict[str, Any]] | None:
    if redis_client is None or not _normalize_name_key(name):
        return None
    name_key = _poi_name_key(city=city, name=name)
    try:
        raw = await redis_client.get(name_key)
        if raw:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            payload = json.loads(raw)
            if isinstance(payload, dict) and _is_valid_poi(payload):
                return [payload]

        alias_raw = await redis_client.get(_poi_alias_key(city=city, alias=name))
        if not alias_raw:
            return None
        if isinstance(alias_raw, bytes):
            alias_raw = alias_raw.decode("utf-8")
        canonical_name = json.loads(alias_raw)
        if not isinstance(canonical_name, str) or not canonical_name.strip():
            return None
        canonical_raw = await redis_client.get(_poi_name_key(city=city, name=canonical_name))
        if not canonical_raw:
            return None
        if isinstance(canonical_raw, bytes):
            canonical_raw = canonical_raw.decode("utf-8")
        payload = json.loads(canonical_raw)
        if isinstance(payload, dict) and _is_valid_poi(payload):
            return [payload]
    except Exception:
        return None
    return None


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


async def _cache_poi_aliases(
    redis_client: Any,
    *,
    city: Any,
    keywords: str,
    name_aliases: list[str],
    pois: list[dict[str, Any]],
) -> None:
    valid = [item for item in pois if _is_valid_poi(item)]
    if redis_client is None or not valid:
        return
    primary = valid[0]
    canonical_name = str(primary.get("name") or "").strip()
    if not canonical_name:
        return
    aliases = [
        str(item).strip()
        for item in [keywords, *name_aliases, *_coerce_alias_list(primary.get("name_aliases"))]
        if str(item or "").strip()
    ]
    try:
        await redis_client.setex(
            _poi_name_key(city=city, name=canonical_name),
            settings.TRAVEL_POI_CACHE_TTL_SECONDS,
            json.dumps(primary, ensure_ascii=True),
        )
        for alias in aliases:
            await redis_client.setex(
                _poi_alias_key(city=city, alias=alias),
                settings.TRAVEL_POI_CACHE_TTL_SECONDS,
                json.dumps(canonical_name, ensure_ascii=True),
            )
    except Exception:
        return


async def _cache_session_pois(redis_client: Any, session_id: Any, pois: list[dict[str, Any]]) -> None:
    valid = [item for item in pois if _is_valid_poi(item)]
    if redis_client is None or not session_id or not valid:
        return
    key = f"travel:pois:{session_id}"
    try:
        raw = await redis_client.get(key)
        existing: list[dict[str, Any]] = []
        if raw:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                existing = [item for item in parsed if isinstance(item, dict) and _is_valid_poi(item)]
        seen = {str(item.get("poi_id") or item.get("name")) for item in existing}
        for item in valid:
            marker = str(item.get("poi_id") or item.get("name"))
            if marker not in seen:
                existing.append(item)
                seen.add(marker)
        await redis_client.setex(
            key,
            settings.TRAVEL_POI_CACHE_TTL_SECONDS,
            json.dumps(existing, ensure_ascii=True),
        )
    except Exception:
        return


class TravelSearchPoiArgs(BaseModel):
    keywords: str = Field(..., description="POI search keywords.")
    name_aliases: list[str] | None = Field(default=None, description="Aliases extracted from travel content for the same place.")
    category: str | None = Field(default=None, description="Source place category extracted from travel content.")
    city: str | None = Field(default=None, description="Optional city hint.")
    types: str | None = Field(default=None, description="Optional AMap POI type filter.")
    location: str | None = Field(default=None, description="Optional center location as lng,lat.")
    page_size: int | None = Field(default=None, description="Result page size, 1-20.")
    runtime_context: RuntimeContext = Field(default_factory=dict)


async def _travel_search_poi(
    keywords: str,
    name_aliases: list[str] | None = None,
    category: str | None = None,
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
    aliases = [str(item).strip() for item in name_aliases or [] if str(item or "").strip()]
    for lookup_name in [keywords, *aliases]:
        cached_by_alias = await _load_cached_poi_by_name_or_alias(redis_client, city=city, name=lookup_name)
        if cached_by_alias:
            await _cache_session_pois(redis_client, ctx.get("session_id"), cached_by_alias)
            return {
                "query": {
                    "keywords": keywords,
                    "name_aliases": aliases,
                    "category": category,
                    "city": city,
                    "types": types,
                    "location": location,
                },
                "pois": cached_by_alias,
                "selected_poi": cached_by_alias[0],
                "rejected_pois": [],
                "source_name": keywords,
                "match_status": "selected",
                "cache_hit": True,
                "cache_source": "name_alias",
            }
    cache_key = _cache_key(
        keywords=keywords,
        city=city,
        types=types,
        location=location,
        page_size=page_size,
    )
    cached_pois = await _load_cached_pois(redis_client, cache_key)
    if cached_pois:
        selected, rejected, match_status = _select_best_poi(
            cached_pois,
            keywords=keywords,
            aliases=aliases,
            source_category=category,
            types=types,
        )
        await _cache_session_pois(redis_client, ctx.get("session_id"), [selected] if selected else [])
        return {
            "query": {
                "keywords": keywords,
                "name_aliases": aliases,
                "category": category,
                "city": city,
                "types": types,
                "location": location,
            },
            "pois": cached_pois,
            "selected_poi": selected,
            "rejected_pois": rejected,
            "source_name": keywords,
            "match_status": match_status,
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
    selected, rejected, match_status = _select_best_poi(
        [item for item in normalized if _is_valid_poi(item)],
        keywords=keywords,
        aliases=aliases,
        source_category=category,
        types=types,
    )
    await _cache_valid_pois(redis_client, cache_key, [selected] if selected else [])
    await _cache_poi_aliases(redis_client, city=city, keywords=keywords, name_aliases=aliases, pois=[selected] if selected else [])
    await _cache_session_pois(redis_client, ctx.get("session_id"), [selected] if selected else [])
    return {
        "query": {
            "keywords": keywords,
            "name_aliases": aliases,
            "category": category,
            "city": city,
            "types": types,
            "location": location,
        },
        "source_name": keywords,
        "pois": normalized,
        "selected_poi": selected,
        "rejected_pois": rejected,
        "match_status": match_status,
        "cache_hit": False,
    }


async def travel_search_poi(args: dict[str, Any]) -> dict[str, Any]:
    runtime_context = {
        "redis_client": args.get("redis_client"),
        "servers_path": args.get("servers_path"),
        "session_id": args.get("session_id"),
    }
    return await _travel_search_poi(
        keywords=str(args.get("keywords") or ""),
        name_aliases=args.get("name_aliases") if isinstance(args.get("name_aliases"), list) else None,
        category=args.get("category"),
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
        "Input: {keywords:string, name_aliases?:string[], category?:string, city?:string, types?:string, location?:string, page_size?:integer}."
    ),
    args_schema=TravelSearchPoiArgs,
    infer_schema=False,
)
