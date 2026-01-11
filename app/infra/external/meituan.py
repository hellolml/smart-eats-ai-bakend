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
    base = query or tag or "dining"
    results = []
    for idx in range(limit):
        results.append(
            {
                "provider": "meituan",
                "provider_id": str(uuid4()),
                "name": f"{base.title()} Spot {idx + 1}",
                "rating": 4.0 + (idx % 4) * 0.2,
                "price": 25 + idx * 6,
                "geo": {"lat": lat, "lng": lng},
                "tags": [tag or "featured", "value"],
                "raw": {"mock": True},
            }
        )
    return results
