from __future__ import annotations

from typing import Any

from app.agent.tools_registry import register_tool
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


@register_tool(
    name="travel_search_poi",
    description=(
        "Search and verify travel POIs by keyword through AMap. "
        "Input: {keywords:string, city?:string, types?:string, location?:string, page_size?:integer}. "
        "Output: {query, pois:[{poi_id,name,address,longitude,latitude,tel,raw}]}."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "keywords": {"type": "string"},
            "city": {"type": "string"},
            "types": {"type": "string"},
            "location": {"type": "string"},
            "page_size": {"type": "integer"},
        },
        "required": ["keywords"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "query": {"type": "object"},
            "pois": {"type": "array", "items": {"type": "object"}},
            "error": {"type": "string"},
        },
    },
)
async def travel_search_poi(args: dict[str, Any]) -> dict[str, Any]:
    keywords = str(args.get("keywords") or "").strip()
    if not keywords:
        return {"error": "missing_keywords"}
    page_size = args.get("page_size")
    try:
        page_size = int(page_size) if page_size is not None else 5
    except (TypeError, ValueError):
        page_size = 5

    pois = await amap.text_search(
        keywords=keywords,
        types=args.get("types"),
        city=args.get("city"),
        location=args.get("location"),
        page_size=max(1, min(page_size, 20)),
        servers_path=args.get("servers_path"),
    )
    return {
        "query": {
            "keywords": keywords,
            "city": args.get("city"),
            "types": args.get("types"),
            "location": args.get("location"),
        },
        "pois": [_normalize_poi(item) for item in pois if isinstance(item, dict)],
    }

