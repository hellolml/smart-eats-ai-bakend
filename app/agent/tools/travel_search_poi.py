from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agent.tools.native import RuntimeContext
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


class TravelSearchPoiArgs(BaseModel):
    keywords: str = Field(..., description="POI keyword to search.")
    city: str | None = Field(default=None, description="Optional city.")
    types: str | None = Field(default=None, description="Optional AMap POI type code.")
    location: str | None = Field(default=None, description="Optional coordinate string lng,lat.")
    page_size: int | None = Field(default=None, description="Number of POIs to return.")
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

    pois = await amap.text_search(
        keywords=keywords,
        types=types,
        city=city,
        location=location,
        page_size=max(1, min(page_size, 20)),
        servers_path=ctx.get("servers_path"),
    )
    return {
        "query": {
            "keywords": keywords,
            "city": city,
            "types": types,
            "location": location,
        },
        "pois": [_normalize_poi(item) for item in pois if isinstance(item, dict)],
    }


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
