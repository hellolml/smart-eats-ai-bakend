from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.deps import redis_dep
from app.common.errors import envelope
from app.domain.recipe.service import RecipeService

router = APIRouter()


@router.get("/search")
async def search_recipes(
    request: Request,
    redis: redis_dep,
    q: str | None = None,
):
    results = await RecipeService.search(redis, q)
    trace_id = getattr(request.state, "trace_id", "")
    return envelope(results, trace_id)
