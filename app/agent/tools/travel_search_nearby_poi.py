from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agent.tools.native import RuntimeContext
from app.infra.external.amap import amap


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_location(value: Any, longitude: Any = None, latitude: Any = None) -> tuple[str | None, float | None, float | None]:
    if isinstance(value, str) and "," in value:
        left, right = value.split(",", 1)
        lng = _coerce_float(left)
        lat = _coerce_float(right)
        if lng is not None and lat is not None:
            return f"{lng},{lat}", lng, lat
    if isinstance(value, dict):
        lng = _coerce_float(value.get("lng", value.get("lon", value.get("longitude"))))
        lat = _coerce_float(value.get("lat", value.get("latitude")))
        if lng is not None and lat is not None:
            return f"{lng},{lat}", lng, lat
    lng = _coerce_float(longitude)
    lat = _coerce_float(latitude)
    if lng is not None and lat is not None:
        return f"{lng},{lat}", lng, lat
    return None, None, None


def _normalize_poi(item: dict[str, Any]) -> dict[str, Any]:
    location = item.get("location")
    _location, longitude, latitude = _coerce_location(location)
    if longitude is None or latitude is None:
        _location, longitude, latitude = _coerce_location(
            {
                "longitude": item.get("longitude", item.get("lng", item.get("lon"))),
                "latitude": item.get("latitude", item.get("lat")),
            }
        )
    distance = item.get("distance")
    try:
        distance = int(float(distance)) if distance is not None else None
    except (TypeError, ValueError):
        distance = None
    return {
        "poi_id": item.get("id") or item.get("poi_id") or item.get("poiId"),
        "name": item.get("name"),
        "address": item.get("address"),
        "longitude": longitude,
        "latitude": latitude,
        "distance_meters": distance,
        "tel": item.get("tel"),
        "type": item.get("type") or item.get("typecode"),
        "raw": item,
    }


class TravelSearchNearbyPoiArgs(BaseModel):
    keywords: str | None = Field(default=None, description="Nearby POI search keywords.")
    location: str | None = Field(default=None, description="Center location as lng,lat.")
    longitude: float | None = Field(default=None, description="Center longitude when location is omitted.")
    latitude: float | None = Field(default=None, description="Center latitude when location is omitted.")
    radius: int | None = Field(default=None, description="Search radius in meters.")
    types: str | None = Field(default=None, description="Optional AMap POI type filter.")
    page_size: int | None = Field(default=None, description="Result page size, 1-20.")
    runtime_context: RuntimeContext = Field(default_factory=dict)


async def _travel_search_nearby_poi(
    keywords: str | None = None,
    location: str | None = None,
    longitude: float | None = None,
    latitude: float | None = None,
    radius: int | None = None,
    types: str | None = None,
    page_size: int | None = None,
    runtime_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = runtime_context or {}
    location_text, lng, lat = _coerce_location(location, longitude, latitude)
    if not location_text:
        return {"error": "missing_location"}
    try:
        page_size = int(page_size) if page_size is not None else 5
    except (TypeError, ValueError):
        page_size = 5
    page_size = max(1, min(page_size, 20))
    try:
        radius = int(radius) if radius is not None else 1000
    except (TypeError, ValueError):
        radius = 1000
    radius = max(100, min(radius, 50000))

    pois = await amap.around_search(
        location_text,
        str(keywords or "").strip() or None,
        types,
        radius=radius,
        page_size=page_size,
        servers_path=ctx.get("servers_path"),
    )
    return {
        "query": {
            "keywords": keywords,
            "location": location_text,
            "longitude": lng,
            "latitude": lat,
            "radius": radius,
            "types": types,
        },
        "pois": [_normalize_poi(item) for item in pois if isinstance(item, dict)],
    }


async def travel_search_nearby_poi(args: dict[str, Any]) -> dict[str, Any]:
    runtime_context = {
        "servers_path": args.get("servers_path"),
    }
    return await _travel_search_nearby_poi(
        keywords=args.get("keywords"),
        location=args.get("location"),
        longitude=args.get("longitude"),
        latitude=args.get("latitude"),
        radius=args.get("radius"),
        types=args.get("types"),
        page_size=args.get("page_size"),
        runtime_context=runtime_context,
    )


travel_search_nearby_poi_tool = StructuredTool.from_function(
    coroutine=_travel_search_nearby_poi,
    name="travel_search_nearby_poi",
    description=(
        "Search nearby travel POIs around a verified coordinate through AMap. "
        "Input: {keywords?:string, location?:'lng,lat', longitude?:number, latitude?:number, "
        "radius?:integer, types?:string, page_size?:integer}."
    ),
    args_schema=TravelSearchNearbyPoiArgs,
    infer_schema=False,
)
