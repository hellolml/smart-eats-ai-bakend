from __future__ import annotations

from datetime import datetime, timezone
from math import asin, cos, radians, sin, sqrt
from typing import Any

from app.infra.models.fridge import FridgeItem
from app.infra.models.preference import UserPreference, UserProfile
from app.infra.models.user import User


def format_quantity_text(quantity: float | None, unit: str | None) -> str:
    if quantity is None:
        return ""
    if float(quantity).is_integer():
        q = str(int(quantity))
    else:
        q = str(quantity)
    return f"{q}{unit or ''}"


def map_ingredient(item: FridgeItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "quantity": item.quantity,
        "unit": item.unit,
        "quantity_text": format_quantity_text(item.quantity, item.unit),
        "expiry_date": item.expiry_date.isoformat() if item.expiry_date else None,
        "source": item.source,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


def map_me(
    user: User,
    profile: UserProfile | None,
    pref: UserPreference | None,
) -> dict[str, Any]:
    joined_at = user.created_at
    if joined_at and joined_at.tzinfo is None:
        joined_at = joined_at.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    joined_days = 1
    if joined_at:
        joined_days = max(1, (now.date() - joined_at.date()).days + 1)

    return {
        "id": user.id,
        "name": user.nickname,
        "avatar": user.avatar_url,
        "email": user.email,
        "phone": user.phone,
        "health_goal": profile.health_goal if profile else None,
        "current_state": profile.current_state if profile else None,
        "tastes": pref.taste_tags if pref and pref.taste_tags else [],
        "taboos": pref.avoid_ingredients if pref and pref.avoid_ingredients else [],
        "allergens": pref.allergens if pref and pref.allergens else [],
        "joined_at": joined_at.isoformat() if joined_at else None,
        "joined_days": joined_days,
    }


def map_preferences(pref: UserPreference) -> dict[str, Any]:
    return {
        "user_id": pref.user_id,
        "tastes": pref.taste_tags or [],
        "taboos": pref.avoid_ingredients or [],
        "allergens": pref.allergens or [],
        "spicy_level": pref.spicy_level,
        "budget_level": pref.budget_level,
    }


def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371000
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    c = 2 * asin(sqrt(a))
    return r * c


def map_restaurant(item: dict[str, Any], origin_lat: float | None, origin_lng: float | None) -> dict[str, Any]:
    geo = item.get("geo") if isinstance(item.get("geo"), dict) else None
    lat = geo.get("lat") if geo else None
    lng = geo.get("lng") if geo else None

    distance_m = None
    if origin_lat is not None and origin_lng is not None and lat is not None and lng is not None:
        distance_m = int(_haversine(origin_lat, origin_lng, float(lat), float(lng)))

    price = item.get("price")
    price_text = "价格未知"
    if isinstance(price, (int, float)) and price > 0:
        price_text = f"￥{int(price)}/人"

    tags = item.get("tags") if isinstance(item.get("tags"), list) else []
    primary_tag = tags[0] if tags else "AI 推荐"

    navigation_url = None
    if lat is not None and lng is not None:
        navigation_url = f"https://uri.amap.com/navigation?to={lng},{lat}"

    provider = item.get("provider") or "amap"
    provider_id = item.get("provider_id") or item.get("id") or "unknown"

    return {
        "id": f"{provider}_{provider_id}",
        "provider": provider,
        "provider_id": provider_id,
        "name": item.get("name") or "未知餐厅",
        "rating": item.get("rating"),
        "distance_m": distance_m,
        "distance_text": f"{distance_m}m" if distance_m is not None else "未知",
        "price_text": price_text,
        "tag": primary_tag,
        "tags": tags,
        "lat": lat,
        "lng": lng,
        "navigation_url": navigation_url,
        "source": item.get("source", "live"),
    }


def map_home_chef_recipe(item: dict[str, Any], ingredient_names: list[str]) -> dict[str, Any]:
    title = item.get("title") or "家常菜"
    cook_time = item.get("cook_time_min")
    calories = item.get("calories")
    tags = item.get("tags") if isinstance(item.get("tags"), list) else []

    ingredients = [f"{name} 适量" for name in ingredient_names[:4]] or ["食材 适量"]
    steps = [
        "准备食材并清洗干净",
        "热锅下油，按顺序翻炒",
        "调味后小火收汁即可出锅",
    ]

    return {
        "title": title,
        "desc": ("、".join(tags[:2])[:15] or "家常快手菜"),
        "time": f"{int(cook_time)}min" if isinstance(cook_time, (int, float)) else "15min",
        "cal": f"{int(calories)}kcal" if isinstance(calories, (int, float)) else "220kcal",
        "img": "cooking_dish",
        "tag": tags[0] if tags else "家常",
        "ingredients": ingredients,
        "steps": steps,
    }
