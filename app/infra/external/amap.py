from __future__ import annotations

from typing import Any
from uuid import uuid4


def search_restaurants(
    query: str | None,
    tag: str | None,
    lat: float | None,
    lng: float | None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    base = query or tag or "food"
    results = []
    for idx in range(limit):
        results.append(
            {
                "provider": "amap",
                "provider_id": str(uuid4()),
                "name": f"{base.title()} Place {idx + 1}",
                "rating": 4.2 + (idx % 3) * 0.1,
                "price": 30 + idx * 5,
                "geo": {"lat": lat, "lng": lng},
                "tags": [tag or "local", "popular"],
                "raw": {"mock": True},
            }
        )
    return results
