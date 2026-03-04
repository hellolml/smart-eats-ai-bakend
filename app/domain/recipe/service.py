from __future__ import annotations

import json
from typing import Any

import redis.asyncio as redis

from app.infra.external.recipe_sources import search_recipes


class RecipeService:
    @staticmethod
    async def search(
        redis_client: redis.Redis,
        query: str | None,
    ) -> list[dict[str, Any]]:
        cache_key = f"recipe:search:{query}"
        cached = await redis_client.get(cache_key)
        if cached:
            return json.loads(cached)

        results = search_recipes(query, limit=15)
        await redis_client.setex(cache_key, 300, json.dumps(results, ensure_ascii=True))
        return results
