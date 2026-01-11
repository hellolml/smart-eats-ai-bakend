from __future__ import annotations

import asyncio
from uuid import uuid4

from sqlalchemy import select, func

from app.infra.db import AsyncSessionLocal, init_db
from app.infra.models.recipe import Recipe
from app.infra.models.restaurant import RestaurantCache


async def _seed_recipes(session) -> None:
    result = await session.execute(select(func.count()).select_from(Recipe))
    if result.scalar_one() > 0:
        return

    recipes = [
        Recipe(
            id=str(uuid4()),
            source="seed",
            title="番茄炒蛋",
            image_url=None,
            cook_time_min=10,
            calories=350,
            tags=["quick", "home"],
            instructions="鸡蛋打散，番茄切块，先炒蛋后炒番茄，合炒调味。",
        ),
        Recipe(
            id=str(uuid4()),
            source="seed",
            title="土豆炖牛肉",
            image_url=None,
            cook_time_min=45,
            calories=620,
            tags=["stew", "hearty"],
            instructions="牛肉焯水，土豆切块，小火炖煮至软烂。",
        ),
        Recipe(
            id=str(uuid4()),
            source="seed",
            title="清炒西兰花",
            image_url=None,
            cook_time_min=8,
            calories=180,
            tags=["light", "veggie"],
            instructions="西兰花焯水，蒜末爆香快炒。",
        ),
    ]
    session.add_all(recipes)


async def _seed_restaurants(session) -> None:
    result = await session.execute(select(func.count()).select_from(RestaurantCache))
    if result.scalar_one() > 0:
        return

    restaurants = [
        RestaurantCache(
            id=str(uuid4()),
            provider="seed",
            provider_id="seed-001",
            name="街角小馆",
            geo={"lat": 31.2304, "lng": 121.4737},
            rating=4.5,
            price=40,
            tags=["家常", "实惠"],
            raw_json={"seed": True},
        ),
        RestaurantCache(
            id=str(uuid4()),
            provider="seed",
            provider_id="seed-002",
            name="轻食实验室",
            geo={"lat": 31.228, "lng": 121.475},
            rating=4.3,
            price=55,
            tags=["轻食", "健康"],
            raw_json={"seed": True},
        ),
        RestaurantCache(
            id=str(uuid4()),
            provider="seed",
            provider_id="seed-003",
            name="面馆老地方",
            geo={"lat": 31.231, "lng": 121.47},
            rating=4.2,
            price=28,
            tags=["面食", "快餐"],
            raw_json={"seed": True},
        ),
    ]
    session.add_all(restaurants)


async def seed() -> None:
    await init_db()
    async with AsyncSessionLocal() as session:
        await _seed_recipes(session)
        await _seed_restaurants(session)
        await session.commit()


if __name__ == "__main__":
    asyncio.run(seed())
