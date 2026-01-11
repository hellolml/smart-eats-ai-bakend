from __future__ import annotations

from typing import Any
from uuid import uuid4


def search_recipes(query: str | None, limit: int = 5) -> list[dict[str, Any]]:
    base = query or "home"
    results = []
    for idx in range(limit):
        results.append(
            {
                "source": "mock",
                "id": str(uuid4()),
                "title": f"{base.title()} Recipe {idx + 1}",
                "image_url": None,
                "cook_time_min": 15 + idx * 5,
                "calories": 400 + idx * 50,
                "tags": ["quick", "simple"],
            }
        )
    return results
