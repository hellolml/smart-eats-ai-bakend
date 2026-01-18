from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.api.deps import db_dep, redis_dep
from app.common.errors import envelope
from app.domain.restaurant.service import RestaurantService

router = APIRouter()


@router.get("/search")
async def search_restaurants(
    request: Request,
    redis: redis_dep,
    q: str | None = None,
    sort: str | None = None,
    tag: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
):
    results = await RestaurantService.search(redis, q, tag, lat, lng, sort)
    trace_id = getattr(request.state, "trace_id", "")
    return envelope(results, trace_id)


@router.get("/{provider}/{provider_id}")
async def restaurant_detail(
    provider: str,
    provider_id: str,
    request: Request,
    db: db_dep,
    redis: redis_dep,
):
    detail = await RestaurantService.get_detail(db, redis, provider, provider_id)
    if not detail:
        raise HTTPException(status_code=404, detail="restaurant not found")
    trace_id = getattr(request.state, "trace_id", "")
    return envelope(detail, trace_id)
