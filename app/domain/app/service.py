from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import redis.asyncio as redis
from fastapi import HTTPException
from redis.exceptions import RedisError
from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.state import ChatState
from app.agent import history
from app.common.config import settings
from app.common.errors import AppError, REDIS_UNAVAILABLE
from app.common.rate_limit import ensure_rate_limit
from app.common.security import (
    AuthError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.domain.app.mappers import (
    map_home_chef_recipe,
    map_ingredient,
    map_me,
    map_preferences,
    map_restaurant,
)
from app.domain.game.blindbox_map import map_blindbox_result
from app.domain.recipe.service import RecipeService
from app.domain.restaurant.service import RestaurantService
from app.infra.models.chat import ChatMessage, ChatSession
from app.infra.models.fridge import FridgeItem, FridgePhoto, RecognitionJob
from app.infra.models.game import BlindboxRoll, WheelConfig, WheelSpin
from app.infra.models.preference import UserPreference, UserProfile
from app.infra.models.user import User
from app.infra.external.amap import amap

logger = logging.getLogger(__name__)


class AppBffService:
    @staticmethod
    async def issue_tokens(user_id: str, redis_client: redis.Redis) -> dict[str, Any]:
        access_token, _ = create_access_token(user_id)
        refresh_token, refresh_jti = create_refresh_token(user_id)
        try:
            await redis_client.setex(f"rt:{refresh_jti}", settings.REFRESH_TOKEN_TTL_SECONDS, user_id)
        except RedisError as exc:
            logger.warning("refresh token persistence skipped: redis unavailable user_id=%s err=%s", user_id, exc)
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
        }

    @staticmethod
    async def register(payload: dict[str, Any], db: AsyncSession, redis_client: redis.Redis) -> dict[str, Any]:
        email = payload.get("email")
        phone = payload.get("phone")
        if not email and not phone:
            raise HTTPException(status_code=400, detail="email or phone required")

        conditions = []
        if email:
            conditions.append(User.email == email)
        if phone:
            conditions.append(User.phone == phone)

        result = await db.execute(select(User).where(or_(*conditions)))
        if result.scalar_one_or_none() is not None:
            raise HTTPException(status_code=400, detail="email or phone already exists")

        user = User(
            id=str(uuid4()),
            email=email,
            phone=phone,
            nickname=payload.get("name") or email or phone or "user",
            password_hash=hash_password(payload["password"]),
        )
        db.add(user)
        await db.commit()
        return await AppBffService.issue_tokens(user.id, redis_client)

    @staticmethod
    async def login(
        payload: dict[str, Any],
        db: AsyncSession,
        redis_client: redis.Redis,
        client_ip: str,
    ) -> dict[str, Any]:
        account = payload["account"]
        await ensure_rate_limit(
            redis_client,
            key=f"rl:app_login:{client_ip}:{account}",
            limit=10,
            window_seconds=60,
        )

        if "@" in account:
            result = await db.execute(select(User).where(User.email == account))
        else:
            result = await db.execute(select(User).where(User.phone == account))

        user = result.scalar_one_or_none()
        if user is None or not verify_password(payload["password"], user.password_hash):
            raise HTTPException(status_code=401, detail="invalid credentials")

        return await AppBffService.issue_tokens(user.id, redis_client)

    @staticmethod
    async def refresh(refresh_token: str, redis_client: redis.Redis) -> dict[str, Any]:
        try:
            claims = decode_token(refresh_token, expected_type="refresh")
        except AuthError as exc:
            raise HTTPException(status_code=401, detail=exc.message) from exc

        jti = claims.get("jti")
        user_id = str(claims.get("sub"))
        if not jti or not user_id:
            raise HTTPException(status_code=401, detail="refresh token invalid")

        key = f"rt:{jti}"
        try:
            stored_user = await redis_client.get(key)
            if stored_user != user_id:
                raise HTTPException(status_code=401, detail="refresh token revoked")
            await redis_client.delete(key)
        except RedisError as exc:
            logger.error("refresh failed: redis unavailable user_id=%s err=%s", user_id, exc)
            raise AppError(code=REDIS_UNAVAILABLE, message="redis unavailable", http_status=503) from exc

        return await AppBffService.issue_tokens(user_id, redis_client)

    @staticmethod
    async def logout(refresh_token: str, redis_client: redis.Redis) -> dict[str, Any]:
        try:
            claims = decode_token(refresh_token, expected_type="refresh")
            jti = claims.get("jti")
            if jti:
                try:
                    await redis_client.delete(f"rt:{jti}")
                except RedisError as exc:
                    logger.error("logout failed: redis unavailable jti=%s err=%s", jti, exc)
                    raise AppError(code=REDIS_UNAVAILABLE, message="redis unavailable", http_status=503) from exc
        except AuthError:
            pass
        return {"logged_out": True}

    @staticmethod
    async def change_password(
        user_id: str,
        old_password: str,
        new_password: str,
        db: AsyncSession,
    ) -> dict[str, Any]:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail="user not found")
        if not verify_password(old_password, user.password_hash):
            raise HTTPException(status_code=401, detail="invalid credentials")

        user.password_hash = hash_password(new_password)
        await db.commit()
        return {"updated": True}

    @staticmethod
    async def _get_user(user_id: str, db: AsyncSession) -> User:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail="user not found")
        return user

    @staticmethod
    async def _get_or_create_profile(user_id: str, db: AsyncSession) -> UserProfile:
        result = await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))
        profile = result.scalar_one_or_none()
        if profile is None:
            profile = UserProfile(user_id=user_id)
            db.add(profile)
            await db.commit()
            await db.refresh(profile)
        return profile

    @staticmethod
    async def _get_or_create_preferences(user_id: str, db: AsyncSession) -> UserPreference:
        result = await db.execute(select(UserPreference).where(UserPreference.user_id == user_id))
        pref = result.scalar_one_or_none()
        if pref is None:
            pref = UserPreference(
                user_id=user_id,
                taste_tags=[],
                avoid_ingredients=[],
                allergens=[],
            )
            db.add(pref)
            await db.commit()
            await db.refresh(pref)
        return pref

    @staticmethod
    async def get_me(user_id: str, db: AsyncSession) -> dict[str, Any]:
        user = await AppBffService._get_user(user_id, db)
        profile = await AppBffService._get_or_create_profile(user_id, db)
        pref = await AppBffService._get_or_create_preferences(user_id, db)
        return map_me(user, profile, pref)

    @staticmethod
    async def update_me(
        user_id: str,
        payload: dict[str, Any],
        db: AsyncSession,
    ) -> dict[str, Any]:
        user = await AppBffService._get_user(user_id, db)
        profile = await AppBffService._get_or_create_profile(user_id, db)

        if payload.get("name") is not None:
            user.nickname = payload["name"]
        if payload.get("avatar") is not None:
            user.avatar_url = payload["avatar"]

        if payload.get("health_goal") is not None:
            profile.health_goal = payload["health_goal"]
        if payload.get("current_state") is not None:
            profile.current_state = payload["current_state"]

        await db.commit()
        pref = await AppBffService._get_or_create_preferences(user_id, db)
        return map_me(user, profile, pref)

    @staticmethod
    async def update_goal_state(
        user_id: str,
        payload: dict[str, Any],
        db: AsyncSession,
    ) -> dict[str, Any]:
        profile = await AppBffService._get_or_create_profile(user_id, db)

        if "health_goal" in payload:
            profile.health_goal = payload["health_goal"]
        if "current_state" in payload:
            profile.current_state = payload["current_state"]

        await db.commit()
        return {
            "health_goal": profile.health_goal,
            "current_state": profile.current_state,
        }

    @staticmethod
    async def get_home_overview(
        user_id: str,
        request_client_ip: str,
        db: AsyncSession,
        location: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        user = await AppBffService._get_user(user_id, db)
        profile = await AppBffService._get_or_create_profile(user_id, db)

        default_city = "北京"
        weather_query_city = default_city
        weather_display_city = default_city
        resolved_location = location

        if resolved_location:
            regeo_region = await amap.reverse_geocode_region(
                resolved_location,
                servers_path=settings.MCP_SERVERS_CONFIG_PATH,
            )
            if isinstance(regeo_region, dict):
                district = regeo_region.get("district")
                city = regeo_region.get("city")
                province = regeo_region.get("province")
                if isinstance(district, str) and district.strip():
                    weather_display_city = district.strip()
                elif isinstance(city, str) and city.strip():
                    weather_display_city = city.strip()
                elif isinstance(province, str) and province.strip():
                    weather_display_city = province.strip()

                if isinstance(city, str) and city.strip():
                    weather_query_city = city.strip()
                elif isinstance(province, str) and province.strip():
                    weather_query_city = province.strip()
        elif request_client_ip not in {"unknown", "testclient", "test", "localhost", "127.0.0.1", "::1"}:
            ip_location, ip_city = await amap.get_ip_location(
                request_client_ip,
                servers_path=settings.MCP_SERVERS_CONFIG_PATH,
            )
            if ip_location:
                resolved_location = ip_location
            if isinstance(ip_city, str) and ip_city.strip():
                value = ip_city.strip()
                weather_query_city = value
                weather_display_city = value

        weather = await amap.get_weather(weather_query_city, servers_path=settings.MCP_SERVERS_CONFIG_PATH)
        temperature = weather.get("temperature_c") if isinstance(weather, dict) else None

        if isinstance(temperature, (int, float)):
            temperature_text = f"{int(round(float(temperature)))}°"
        else:
            temperature_text = "--°"

        weather_status = ""
        if isinstance(weather, dict):
            status = weather.get("status")
            weather_status = str(status) if isinstance(status, str) else ""

        return {
            "name": user.nickname,
            "health_goal": profile.health_goal,
            "current_state": profile.current_state,
            "weather": {
                "city": weather_display_city,
                "temperature_c": temperature,
                "status": weather_status,
                "temperature_text": temperature_text,
                "display": f"{temperature_text}{weather_status}" if weather_status else temperature_text,
                "location": resolved_location,
            },
        }

    @staticmethod
    async def get_preferences(user_id: str, db: AsyncSession) -> dict[str, Any]:
        pref = await AppBffService._get_or_create_preferences(user_id, db)
        return map_preferences(pref)

    @staticmethod
    async def update_preferences(
        user_id: str,
        payload: dict[str, Any],
        db: AsyncSession,
        redis_client: redis.Redis,
    ) -> dict[str, Any]:
        pref = await AppBffService._get_or_create_preferences(user_id, db)
        if payload.get("tastes") is not None:
            pref.taste_tags = payload["tastes"]
        if payload.get("taboos") is not None:
            pref.avoid_ingredients = payload["taboos"]
        if payload.get("allergens") is not None:
            pref.allergens = payload["allergens"]
        if payload.get("spicy_level") is not None:
            pref.spicy_level = payload["spicy_level"]
        if payload.get("budget_level") is not None:
            pref.budget_level = payload["budget_level"]

        await db.commit()

        pattern = f"context:user:{user_id}:*"
        keys = []
        async for key in redis_client.scan_iter(match=pattern):
            keys.append(key)
        if keys:
            await redis_client.delete(*keys)

        return map_preferences(pref)

    @staticmethod
    async def list_ingredients(user_id: str, db: AsyncSession) -> list[dict[str, Any]]:
        result = await db.execute(
            select(FridgeItem)
            .where(FridgeItem.user_id == user_id)
            .order_by(desc(FridgeItem.updated_at))
        )
        items = result.scalars().all()
        return [map_ingredient(item) for item in items]

    @staticmethod
    async def create_ingredient(user_id: str, payload: dict[str, Any], db: AsyncSession) -> dict[str, Any]:
        item = FridgeItem(
            id=str(uuid4()),
            user_id=user_id,
            name=payload["name"],
            quantity=payload.get("quantity"),
            unit=payload.get("unit"),
            expiry_date=payload.get("expiry_date"),
            source=payload.get("source") or "manual",
        )
        db.add(item)
        await db.commit()
        await db.refresh(item)
        return map_ingredient(item)

    @staticmethod
    async def update_ingredient(
        user_id: str,
        ingredient_id: str,
        payload: dict[str, Any],
        db: AsyncSession,
    ) -> dict[str, Any]:
        result = await db.execute(
            select(FridgeItem).where(FridgeItem.id == ingredient_id, FridgeItem.user_id == user_id)
        )
        item = result.scalar_one_or_none()
        if item is None:
            raise HTTPException(status_code=404, detail="item not found")

        for field, value in payload.items():
            setattr(item, field, value)
        await db.commit()
        await db.refresh(item)
        return map_ingredient(item)

    @staticmethod
    async def delete_ingredient(user_id: str, ingredient_id: str, db: AsyncSession) -> dict[str, Any]:
        result = await db.execute(
            select(FridgeItem).where(FridgeItem.id == ingredient_id, FridgeItem.user_id == user_id)
        )
        item = result.scalar_one_or_none()
        if item is None:
            raise HTTPException(status_code=404, detail="item not found")
        await db.delete(item)
        await db.commit()
        return {"deleted": True}

    @staticmethod
    async def create_scan_job(
        user_id: str,
        object_key: str,
        captured_at: datetime | None,
        db: AsyncSession,
    ) -> dict[str, Any]:
        photo = FridgePhoto(
            id=str(uuid4()),
            user_id=user_id,
            object_key=object_key,
            captured_at=captured_at,
        )
        db.add(photo)

        job = RecognitionJob(
            id=str(uuid4()),
            user_id=user_id,
            photo_id=photo.id,
            status="queued",
            result_json=None,
            error=None,
        )
        db.add(job)
        await db.commit()
        return {"scan_id": job.id, "status": job.status, "photo_id": photo.id}

    @staticmethod
    async def get_scan(user_id: str, scan_id: str, db: AsyncSession) -> dict[str, Any]:
        result = await db.execute(
            select(RecognitionJob).where(RecognitionJob.id == scan_id, RecognitionJob.user_id == user_id)
        )
        job = result.scalar_one_or_none()
        if job is None:
            raise HTTPException(status_code=404, detail="scan not found")

        return {
            "scan_id": job.id,
            "status": job.status,
            "result": job.result_json,
            "error": job.error,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        }

    @staticmethod
    async def complete_scan_job_inline(scan_id: str, db: AsyncSession) -> None:
        result = await db.execute(select(RecognitionJob).where(RecognitionJob.id == scan_id))
        job = result.scalar_one_or_none()
        if job is None or job.status not in {"queued", "running"}:
            return

        job.status = "running"
        await db.commit()

        job.result_json = {
            "items": [
                {"name": "egg", "quantity": 2, "unit": "pcs"},
                {"name": "tomato", "quantity": 3, "unit": "pcs"},
            ],
            "request_id": str(uuid4()),
        }
        job.status = "success"
        job.finished_at = datetime.now(timezone.utc)
        await db.commit()

    @staticmethod
    async def apply_scan(user_id: str, scan_id: str, merge_by_name: bool, db: AsyncSession) -> dict[str, Any]:
        result = await db.execute(
            select(RecognitionJob).where(RecognitionJob.id == scan_id, RecognitionJob.user_id == user_id)
        )
        job = result.scalar_one_or_none()
        if job is None:
            raise HTTPException(status_code=404, detail="scan not found")
        if job.status != "success" or not isinstance(job.result_json, dict):
            raise HTTPException(status_code=400, detail="scan not ready")

        raw_items = job.result_json.get("items")
        if not isinstance(raw_items, list):
            raw_items = []

        applied: list[FridgeItem] = []
        for raw in raw_items:
            if not isinstance(raw, dict) or not raw.get("name"):
                continue
            name = str(raw["name"])
            quantity = raw.get("quantity")
            unit = raw.get("unit")

            existing = None
            if merge_by_name:
                existing_result = await db.execute(
                    select(FridgeItem).where(
                        FridgeItem.user_id == user_id,
                        FridgeItem.name == name,
                        FridgeItem.unit == unit,
                    )
                )
                existing = existing_result.scalar_one_or_none()

            if existing:
                if isinstance(quantity, (int, float)):
                    current = existing.quantity or 0
                    existing.quantity = current + float(quantity)
                applied.append(existing)
            else:
                item = FridgeItem(
                    id=str(uuid4()),
                    user_id=user_id,
                    name=name,
                    quantity=quantity if isinstance(quantity, (int, float)) else None,
                    unit=unit if isinstance(unit, str) else None,
                    source="recognition",
                )
                db.add(item)
                applied.append(item)

        await db.commit()

        response_items = [map_ingredient(item) for item in applied]
        return {
            "scan_id": scan_id,
            "applied_count": len(response_items),
            "items": response_items,
        }

    @staticmethod
    async def generate_home_chef_recipes(
        user_id: str,
        payload: dict[str, Any],
        db: AsyncSession,
        redis_client: redis.Redis,
    ) -> dict[str, Any]:
        ingredient_names = payload.get("ingredients") or []
        if not ingredient_names:
            result = await db.execute(select(FridgeItem).where(FridgeItem.user_id == user_id).limit(10))
            ingredient_names = [item.name for item in result.scalars().all()]

        ingredient_names = [name for name in ingredient_names if isinstance(name, str) and name.strip()]
        query = " ".join(ingredient_names[:4]) if ingredient_names else "home"
        count = payload.get("count", 2)

        recipes: list[dict[str, Any]] = []
        try:
            raw_recipes = await RecipeService.search(redis_client, query)
            for item in raw_recipes[:count]:
                recipes.append(map_home_chef_recipe(item, ingredient_names))
        except Exception:
            recipes = []

        if not recipes:
            recipes = [
                {
                    "title": "家常西红柿炒鸡蛋",
                    "desc": "经典酸甜下饭",
                    "time": "10min",
                    "cal": "180kcal",
                    "img": "cooking_dish",
                    "tag": "高蛋白",
                    "ingredients": ["鸡蛋 3个", "西红柿 2个", "小葱 1根"],
                    "steps": ["西红柿切块，鸡蛋打散", "先炒鸡蛋盛出，再炒西红柿", "回锅翻炒并调味"],
                },
                {
                    "title": "青椒炒肉丝",
                    "desc": "下饭快手菜",
                    "time": "15min",
                    "cal": "260kcal",
                    "img": "cooking_dish",
                    "tag": "家常",
                    "ingredients": ["猪肉 150g", "青椒 2个", "姜蒜 适量"],
                    "steps": ["肉丝腌制 10 分钟", "青椒切丝备用", "先炒肉再合炒青椒"],
                },
            ][:count]

        return {"recipes": recipes}

    @staticmethod
    async def get_today_card(user_id: str, db: AsyncSession) -> dict[str, Any]:
        user = await AppBffService._get_user(user_id, db)
        profile = await AppBffService._get_or_create_profile(user_id, db)
        now = datetime.now()
        return {
            "name": user.nickname,
            "health_goal": profile.health_goal,
            "current_state": profile.current_state,
            "weather": {"temp_c": 26, "text": "晴"},
            "time_of_day": now.strftime("%H:%M"),
            "weekday": now.strftime("%A"),
        }

    @staticmethod
    def _fallback_restaurants(lat: float | None, lng: float | None) -> list[dict[str, Any]]:
        base = [
            {"provider": "amap", "provider_id": "mock_1", "name": "老上海本帮菜", "rating": 4.8, "price": 88, "tags": ["剁椒鱼头必点"], "geo": {"lat": lat, "lng": lng}, "source": "fallback_mock"},
            {"provider": "amap", "provider_id": "mock_2", "name": "深夜拉面馆", "rating": 4.7, "price": 42, "tags": ["汤底浓郁"], "geo": {"lat": lat, "lng": lng}, "source": "fallback_mock"},
            {"provider": "amap", "provider_id": "mock_3", "name": "轻食能量碗", "rating": 4.6, "price": 36, "tags": ["减脂推荐"], "geo": {"lat": lat, "lng": lng}, "source": "fallback_mock"},
        ]
        return base

    @staticmethod
    def _extract_price_value(price_text: Any) -> float | None:
        if isinstance(price_text, (int, float)):
            value = float(price_text)
            return value if value > 0 else None
        if not isinstance(price_text, str):
            return None
        numeric = "".join(ch for ch in price_text if ch.isdigit() or ch == ".")
        if not numeric:
            return None
        try:
            value = float(numeric)
        except ValueError:
            return None
        return value if value > 0 else None

    @staticmethod
    def _sort_restaurants(rows: list[dict[str, Any]], sort: str | None) -> list[dict[str, Any]]:
        sort_key = (sort or "").strip().lower()
        if sort_key in {"nearest", "distance", "distance_asc"}:
            return sorted(rows, key=lambda row: (row.get("distance_m") is None, row.get("distance_m") or 0))
        if sort_key in {"rating_desc", "rating", "score_desc"}:
            return sorted(
                rows,
                key=lambda row: (
                    row.get("rating") is None,
                    -(float(row.get("rating"))) if isinstance(row.get("rating"), (int, float)) else 0,
                ),
            )
        if sort_key in {"price_asc", "price", "cost_asc"}:
            return sorted(
                rows,
                key=lambda row: (
                    AppBffService._extract_price_value(row.get("price_text")) is None,
                    AppBffService._extract_price_value(row.get("price_text")) or 0,
                ),
            )
        return rows

    @staticmethod
    async def list_restaurants(
        redis_client: redis.Redis,
        q: str | None,
        tag: str | None,
        lat: float | None,
        lng: float | None,
        sort: str | None,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        service_failed = False
        try:
            results = await RestaurantService.search(redis_client, q, tag, lat, lng, sort)
        except Exception:
            service_failed = True
            results = []

        if service_failed and not results and settings.APP_FALLBACK_ENABLED:
            results = AppBffService._fallback_restaurants(lat, lng)

        mapped_rows = [map_restaurant(item, lat, lng) for item in results]
        return AppBffService._sort_restaurants(mapped_rows, sort)

    @staticmethod
    async def restaurant_detail(
        provider: str,
        provider_id: str,
        db: AsyncSession,
        redis_client: redis.Redis,
    ) -> dict[str, Any]:
        detail = None
        service_failed = False
        try:
            detail = await RestaurantService.get_detail(db, redis_client, provider, provider_id)
        except Exception:
            service_failed = True
            detail = None

        if service_failed and not detail and settings.APP_FALLBACK_ENABLED:
            detail = {
                "provider": provider,
                "provider_id": provider_id,
                "name": "餐厅信息加载中",
                "rating": 4.6,
                "price": 58,
                "tags": ["fallback_mock"],
                "geo": None,
                "source": "fallback_mock",
            }

        if not detail:
            raise HTTPException(status_code=404, detail="restaurant not found")

        mapped = map_restaurant(detail, None, None)
        mapped["raw"] = detail.get("raw")
        return mapped

    @staticmethod
    async def blind_box_draw(user_id: str, seed: str | None, db: AsyncSession) -> dict[str, Any]:
        pool = ["noodles", "dumplings", "salad", "soup", "rice bowl"]
        pref_result = await db.execute(select(UserPreference).where(UserPreference.user_id == user_id))
        pref = pref_result.scalar_one_or_none()
        avoid = set((pref.avoid_ingredients or []) + (pref.allergens or [])) if pref else set()
        filtered = [item for item in pool if item not in avoid]
        warnings: list[str] = []
        if filtered:
            pool = filtered
        else:
            warnings.append("avoid list filtered all items; fallback to full pool")

        actual_seed = seed or str(uuid4())
        rng = random.Random(actual_seed)
        picked = rng.choice(pool)

        roll = BlindboxRoll(
            id=str(uuid4()),
            user_id=user_id,
            result=picked,
            seed=actual_seed,
        )
        db.add(roll)
        await db.commit()

        return {
            "result": map_blindbox_result(picked),
            "seed": actual_seed,
            "warnings": warnings,
        }

    @staticmethod
    async def _get_latest_wheel(user_id: str, db: AsyncSession) -> WheelConfig | None:
        result = await db.execute(
            select(WheelConfig)
            .where(WheelConfig.user_id == user_id)
            .order_by(desc(WheelConfig.updated_at))
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _default_wheel_options() -> list[str]:
        return ["火锅", "寿司", "汉堡", "拉面", "麻辣烫", "沙拉"]

    @staticmethod
    def _extract_labels(config: WheelConfig | None) -> list[str]:
        if not config:
            return AppBffService._default_wheel_options()
        raw = config.options
        if isinstance(raw, dict):
            options = raw.get("options")
            if isinstance(options, list):
                labels = []
                for opt in options:
                    if isinstance(opt, dict) and isinstance(opt.get("label"), str):
                        labels.append(opt["label"])
                if labels:
                    return labels
        return AppBffService._default_wheel_options()

    @staticmethod
    async def get_wheel_current(user_id: str, db: AsyncSession) -> dict[str, Any]:
        config = await AppBffService._get_latest_wheel(user_id, db)
        return {
            "wheel_id": config.id if config else None,
            "name": config.name if config else "我的转盘",
            "options": AppBffService._extract_labels(config),
        }

    @staticmethod
    async def upsert_wheel_current(user_id: str, payload: dict[str, Any], db: AsyncSession) -> dict[str, Any]:
        labels = [label.strip() for label in payload.get("options", []) if isinstance(label, str) and label.strip()]
        if len(labels) < 2:
            raise HTTPException(status_code=400, detail="wheel requires at least two options")

        config = await AppBffService._get_latest_wheel(user_id, db)
        options = {"options": [{"label": label} for label in labels]}

        if config is None:
            config = WheelConfig(
                id=str(uuid4()),
                user_id=user_id,
                name=payload.get("name") or "我的转盘",
                options=options,
            )
            db.add(config)
        else:
            config.name = payload.get("name") or config.name
            config.options = options

        await db.commit()
        await db.refresh(config)
        return {
            "wheel_id": config.id,
            "name": config.name,
            "options": labels,
        }

    @staticmethod
    async def spin_wheel_current(user_id: str, seed: str | None, db: AsyncSession) -> dict[str, Any]:
        config = await AppBffService._get_latest_wheel(user_id, db)
        if config is None:
            config = WheelConfig(
                id=str(uuid4()),
                user_id=user_id,
                name="我的转盘",
                options={"options": [{"label": label} for label in AppBffService._default_wheel_options()]},
            )
            db.add(config)
            await db.commit()
            await db.refresh(config)

        options = AppBffService._extract_labels(config)

        pref_result = await db.execute(select(UserPreference).where(UserPreference.user_id == user_id))
        pref = pref_result.scalar_one_or_none()
        avoid = set((pref.avoid_ingredients or []) + (pref.allergens or [])) if pref else set()

        def allowed(label: str) -> bool:
            lower = label.lower()
            return all(term.lower() not in lower for term in avoid)

        filtered = [label for label in options if allowed(label)]
        warnings: list[str] = []
        if filtered:
            options = filtered
        else:
            warnings.append("avoid list filtered all options; fallback to full options")

        actual_seed = seed or str(uuid4())
        rng = random.Random(actual_seed)
        winner = rng.choice(options)
        angle = rng.random() * 360

        spin = WheelSpin(
            id=str(uuid4()),
            user_id=user_id,
            config_id=config.id,
            result=winner,
            seed=actual_seed,
            angle=angle,
        )
        db.add(spin)
        await db.commit()

        return {
            "wheel_id": config.id,
            "winner": winner,
            "angle": angle,
            "seed": actual_seed,
            "warnings": warnings,
        }

    @staticmethod
    async def create_chat_session(user_id: str | None, db: AsyncSession) -> dict[str, Any]:
        session = ChatSession(
            id=str(uuid4()),
            user_id=user_id,
            scene="chat",
            title="新会话",
        )
        db.add(session)
        await db.commit()
        return {"session_id": session.id, "title": session.title}

    @staticmethod
    async def rename_chat_session(
        user_id: str | None,
        session_id: str,
        title: str,
        db: AsyncSession,
    ) -> dict[str, Any]:
        result = await db.execute(select(ChatSession).where(ChatSession.id == session_id))
        session = result.scalar_one_or_none()
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        if user_id and session.user_id != user_id:
            raise HTTPException(status_code=403, detail="forbidden")

        session.title = title
        await db.commit()
        return {"updated": True, "title": session.title}

    @staticmethod
    async def delete_chat_session(
        user_id: str | None,
        session_id: str,
        db: AsyncSession,
        redis_client: redis.Redis,
    ) -> dict[str, Any]:
        result = await db.execute(select(ChatSession).where(ChatSession.id == session_id))
        session = result.scalar_one_or_none()
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        if user_id and session.user_id != user_id:
            raise HTTPException(status_code=403, detail="forbidden")

        session.deleted_at = datetime.utcnow()
        await db.commit()

        await history.clear_session_cache(redis_client, session_id)

        return {"deleted": True}

    @staticmethod
    async def list_chat_sessions(
        user_id: str | None,
        db: AsyncSession,
        limit: int,
        offset: int,
        q: str | None,
    ) -> dict[str, Any]:
        stmt = (
            select(ChatSession)
            .where(ChatSession.deleted_at.is_(None))
            .order_by(desc(ChatSession.created_at))
            .offset(offset)
            .limit(limit)
        )
        if user_id:
            stmt = stmt.where(ChatSession.user_id == user_id)
        if q:
            stmt = stmt.where(ChatSession.title.contains(q))

        rows = (await db.execute(stmt)).scalars().all()
        updated = False
        for row in rows:
            if not row.title or row.title == "新会话":
                msg_stmt = (
                    select(ChatMessage)
                    .where(ChatMessage.session_id == row.id, ChatMessage.role == "user")
                    .order_by(ChatMessage.created_at)
                    .limit(1)
                )
                msg_result = await db.execute(msg_stmt)
                msg = msg_result.scalar_one_or_none()
                if msg and msg.content:
                    title = msg.content.strip().replace("\n", " ")
                    row.title = title[:24] if len(title) > 24 else title
                    updated = True
        if updated:
            await db.commit()

        tz = timezone(timedelta(hours=8))
        sessions = []
        for row in rows:
            created_at = row.created_at
            if created_at and created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            sessions.append(
                {
                    "session_id": row.id,
                    "scene": row.scene,
                    "title": row.title,
                    "created_at": created_at.astimezone(tz).isoformat() if created_at else None,
                }
            )
        return {"sessions": sessions, "offset": offset, "limit": limit}

    @staticmethod
    async def list_chat_messages(
        user_id: str | None,
        session_id: str,
        db: AsyncSession,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        result = await db.execute(select(ChatSession).where(ChatSession.id == session_id))
        session = result.scalar_one_or_none()
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        if user_id and session.user_id != user_id:
            raise HTTPException(status_code=403, detail="forbidden")

        rows = (
            await db.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.created_at)
                .offset(offset)
                .limit(limit)
            )
        ).scalars().all()

        messages = [
            {
                "id": row.id,
                "role": row.role,
                "content": row.content,
                "tool_name": row.tool_name,
                "tool_payload": row.tool_payload_json,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
        return {"messages": messages, "offset": offset, "limit": limit}

    @staticmethod
    async def ensure_chat_session_access(
        user_id: str | None,
        session_id: str,
        db: AsyncSession,
        allow_missing: bool = True,
    ) -> None:
        result = await db.execute(select(ChatSession).where(ChatSession.id == session_id))
        session = result.scalar_one_or_none()
        if session is None:
            if allow_missing:
                return
            raise HTTPException(status_code=404, detail="session not found")
        if user_id and session.user_id != user_id:
            raise HTTPException(status_code=403, detail="forbidden")

    @staticmethod
    async def stop_chat(session_id: str, redis_client: redis.Redis) -> dict[str, Any]:
        await redis_client.setex(f"chat:cancel:{session_id}", settings.CHAT_CANCEL_TTL, "1")
        return {"stopped": True}

    @staticmethod
    async def stop_chat_session(
        user_id: str | None,
        session_id: str,
        db: AsyncSession,
        redis_client: redis.Redis,
    ) -> dict[str, Any]:
        await AppBffService.ensure_chat_session_access(user_id, session_id, db)
        return await AppBffService.stop_chat(session_id, redis_client)

    @staticmethod
    def resolve_client_ip(
        forwarded_for: str | None,
        real_ip: str | None,
        request_client_host: str | None,
    ) -> str:
        forwarded = forwarded_for or real_ip
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request_client_host or "unknown"

    @staticmethod
    async def build_chat_state(
        session_id: str,
        user_id: str | None,
        payload: dict[str, Any],
        request_client_ip: str,
        redis_client: redis.Redis,
        rate_limit_key_prefix: str = "app_chat",
    ) -> ChatState:
        await ensure_rate_limit(
            redis_client,
            key=f"rl:{rate_limit_key_prefix}:{request_client_ip}",
            limit=30,
            window_seconds=60,
        )
        return ChatState(
            session_id=session_id,
            user_id=user_id,
            message=payload.get("message"),
            context_overrides=payload.get("client_context_overrides"),
            provider=payload.get("provider"),
            agent_type=payload.get("agent_type"),
            client_ip=request_client_ip,
            resume_from_checkpoint=bool(payload.get("resume_from_checkpoint")),
            checkpoint_ref=payload.get("checkpoint_ref"),
            replay_from_checkpoint=bool(payload.get("replay_from_checkpoint")),
            resume_payload=payload.get("resume_payload"),
        )

    @staticmethod
    async def prepare_chat_stream_state(
        session_id: str,
        user_id: str | None,
        payload: dict[str, Any] | None,
        db: AsyncSession,
        redis_client: redis.Redis,
        forwarded_for: str | None,
        real_ip: str | None,
        request_client_host: str | None,
        trace_id: str | None,
        rate_limit_key_prefix: str = "app_chat",
    ) -> ChatState:
        await AppBffService.ensure_chat_session_access(user_id, session_id, db)
        client_ip = AppBffService.resolve_client_ip(forwarded_for, real_ip, request_client_host)
        state = await AppBffService.build_chat_state(
            session_id=session_id,
            user_id=user_id,
            payload=payload or {},
            request_client_ip=client_ip,
            redis_client=redis_client,
            rate_limit_key_prefix=rate_limit_key_prefix,
        )
        state.trace_id = trace_id
        return state
