from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.infra.db import AsyncSessionLocal, init_db
from app.infra.models.recipe import Recipe
from app.infra.models.restaurant import RestaurantCache


async def list_data() -> None:
    await init_db()
    async with AsyncSessionLocal() as session:
        recipe_rows = (await session.execute(select(Recipe))).scalars().all()
        restaurant_rows = (await session.execute(select(RestaurantCache))).scalars().all()

        print("Recipes:")
        for row in recipe_rows:
            print(f"- {row.title} ({row.calories} kcal, {row.cook_time_min} min)")

        print("\nRestaurants:")
        for row in restaurant_rows:
            print(f"- {row.name} (rating {row.rating}, price {row.price})")


if __name__ == "__main__":
    asyncio.run(list_data())
