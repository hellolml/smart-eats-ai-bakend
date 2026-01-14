from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import select, func

from app.common.security import hash_password
from app.infra.db import AsyncSessionLocal, init_db
from app.infra.models.fridge import FridgeItem
from app.infra.models.recipe import Recipe
from app.infra.models.restaurant import RestaurantCache
from app.infra.models.user import User


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
        Recipe(
            id=str(uuid4()),
            source="seed",
            title="青椒肉丝",
            image_url=None,
            cook_time_min=20,
            calories=520,
            tags=["home", "stirfry"],
            instructions="瘦肉切丝腌制，青椒切丝，快速翻炒调味。",
        ),
        Recipe(
            id=str(uuid4()),
            source="seed",
            title="番茄牛腩",
            image_url=None,
            cook_time_min=60,
            calories=680,
            tags=["stew", "hearty"],
            instructions="牛腩焯水，番茄炒香后小火炖煮至软烂。",
        ),
        Recipe(
            id=str(uuid4()),
            source="seed",
            title="蒜蓉生菜",
            image_url=None,
            cook_time_min=8,
            calories=160,
            tags=["light", "veggie"],
            instructions="蒜蓉爆香，生菜快炒，调味即可。",
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


async def _seed_users(session) -> str:
    result = await session.execute(select(func.count()).select_from(User))
    if result.scalar_one() > 0:
        user_result = await session.execute(select(User).limit(1))
        user = user_result.scalar_one()
        return user.id

    user_id = str(uuid4())
    user = User(
        id=user_id,
        email="demo@example.com",
        phone="13800000000",
        nickname="DemoUser",
        password_hash=hash_password("password123"),
    )
    session.add(user)
    return user_id


async def _seed_fridge_items(session, user_id: str) -> None:
    result = await session.execute(select(func.count()).select_from(FridgeItem))
    if result.scalar_one() > 0:
        return

    now = datetime.now()
    items = [
        FridgeItem(
            id=str(uuid4()),
            user_id=user_id,
            name="鸡蛋",
            quantity=6,
            unit="个",
            expiry_date=now + timedelta(days=7),
            source="seed",
        ),
        FridgeItem(
            id=str(uuid4()),
            user_id=user_id,
            name="番茄",
            quantity=4,
            unit="个",
            expiry_date=now + timedelta(days=4),
            source="seed",
        ),
        FridgeItem(
            id=str(uuid4()),
            user_id=user_id,
            name="牛肉",
            quantity=0.5,
            unit="kg",
            expiry_date=now + timedelta(days=3),
            source="seed",
        ),
        FridgeItem(
            id=str(uuid4()),
            user_id=user_id,
            name="土豆",
            quantity=3,
            unit="个",
            expiry_date=now + timedelta(days=10),
            source="seed",
        ),
        FridgeItem(
            id=str(uuid4()),
            user_id=user_id,
            name="西兰花",
            quantity=1,
            unit="颗",
            expiry_date=now + timedelta(days=2),
            source="seed",
        ),
        FridgeItem(
            id=str(uuid4()),
            user_id=user_id,
            name="大蒜",
            quantity=1,
            unit="头",
            expiry_date=now + timedelta(days=30),
            source="seed",
        ),
    ]
    session.add_all(items)


async def seed() -> None:
    await init_db()
    async with AsyncSessionLocal() as session:
        user_id = await _seed_users(session)
        await _seed_recipes(session)
        await _seed_restaurants(session)
        await _seed_fridge_items(session, user_id)
        await session.commit()
        print(f"SEED_DEMO_USER_ID={user_id}")


if __name__ == "__main__":
    asyncio.run(seed())
