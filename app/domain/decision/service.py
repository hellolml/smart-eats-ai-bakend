from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

import redis.asyncio as redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.recipe.service import RecipeService
from app.domain.restaurant.service import RestaurantService
from app.infra.external.amap import amap
from app.infra.models.preference import UserPreference


@dataclass
class DecisionContext:
    city: str | None
    weather: dict[str, Any] | None
    now: datetime


class DecisionService:
    @staticmethod
    async def blindbox(
        db: AsyncSession,
        redis_client: redis.Redis,
        *,
        user_id: str | None,
        query: str | None,
        city: str | None,
        lat: float | None,
        lng: float | None,
        budget_level: int | None,
        scene: str | None,
        client_ip: str | None = None,
    ) -> dict[str, Any]:
        lat, lng, city = await _resolve_location(lat, lng, city, client_ip=client_ip)
        decision_ctx = await _build_context(city)
        pref = await _get_preference(db, user_id)

        restaurants = await RestaurantService.search(
            redis_client,
            query=query or "美食",
            tag=None,
            lat=lat,
            lng=lng,
            sort="rating_desc",
            city=city,
        )
        chosen: dict[str, Any] | None = None

        # 用户要求：优先从附近餐厅里随机抽一家
        if restaurants:
            picked = random.choice(restaurants[:5])
            score = _score_restaurant(picked, pref, budget_level, decision_ctx, lat, lng)
            chosen = {
                "type": "restaurant",
                "id": f"amap:{picked.get('provider_id') or uuid4()}",
                "title": picked.get("name") or "附近美食",
                "score": score,
                "raw": picked,
            }
        else:
            recipes = await RecipeService.search(redis_client, query or "快手菜")
            if recipes:
                picked_recipe = random.choice(recipes[:5])
                score = _score_recipe(picked_recipe, pref, budget_level, decision_ctx, scene)
                chosen = {
                    "type": "recipe",
                    "id": f"recipe:{(picked_recipe.get('title') or str(uuid4()))}",
                    "title": picked_recipe.get("title") or "家常菜",
                    "score": score,
                    "raw": picked_recipe,
                }
            else:
                food_pool = [
                    "牛肉面", "蛋炒饭", "麻辣香锅", "寿司", "披萨", "汉堡", "沙拉", "饺子", "火锅", "小笼包",
                ]
                fallback_title = random.choice(food_pool)
                chosen = {
                    "type": "fallback",
                    "id": f"food:{uuid4()}",
                    "title": fallback_title,
                    "score": 45.0,
                    "raw": {"title": fallback_title},
                }

        confidence = min(0.95, max(0.4, (chosen["score"] if chosen else 40.0) / 100.0))
        actions = _build_actions(chosen)
        decision_payload: dict[str, Any] = {
            "type": chosen["type"],
            "title": chosen["title"],
            "confidence": round(confidence, 2),
        }
        if chosen["type"] == "restaurant":
            raw = chosen.get("raw") or {}
            decision_payload.update(
                {
                    "provider": raw.get("provider") or "amap",
                    "provider_id": raw.get("provider_id"),
                    "navigation_url": actions[0].get("url") if actions else None,
                }
            )

        return {
            "decision": decision_payload,
            "reasons": _build_reasons(chosen["title"], decision_ctx, scene),
            "actions": actions,
            "meta": {
                "candidates": len(restaurants) if restaurants else 0,
                "time_slot": _time_slot(decision_ctx.now),
                "weather": (decision_ctx.weather or {}).get("weather"),
                "city": decision_ctx.city,
            },
        }

    @staticmethod
    async def quick_filter_start(redis_client: redis.Redis, *, query: str | None) -> dict[str, Any]:
        flow_id = str(uuid4())
        state = {
            "flow_id": flow_id,
            "query": query,
            "round": 1,
            "answers": {},
            "next_question": _QUESTION_SET[0],
            "done": False,
        }
        await redis_client.setex(_flow_key(flow_id), 1800, _json_dump(state))
        return state

    @staticmethod
    async def quick_filter_answer(
        redis_client: redis.Redis,
        db: AsyncSession,
        *,
        flow_id: str,
        user_id: str | None,
        answer: str,
        city: str | None,
        lat: float | None,
        lng: float | None,
        budget_level: int | None,
        client_ip: str | None = None,
    ) -> dict[str, Any] | None:
        raw = await redis_client.get(_flow_key(flow_id))
        if not raw:
            return None
        state = _json_load(raw)
        if state.get("done"):
            return state

        round_idx = int(state.get("round") or 1)
        question = _QUESTION_SET[min(round_idx - 1, len(_QUESTION_SET) - 1)]
        state.setdefault("answers", {})[question["slot"]] = answer
        round_idx += 1
        state["round"] = round_idx

        if round_idx <= len(_QUESTION_SET):
            state["next_question"] = _QUESTION_SET[round_idx - 1]
            await redis_client.setex(_flow_key(flow_id), 1800, _json_dump(state))
            return state

        # finalize
        decision = await DecisionService.blindbox(
            db,
            redis_client,
            user_id=user_id,
            query=state.get("query") or _query_from_answers(state.get("answers") or {}),
            city=city,
            lat=lat,
            lng=lng,
            budget_level=budget_level,
            scene="quick_filter",
            client_ip=client_ip,
        )
        state["done"] = True
        state["result"] = decision
        state["next_question"] = None
        await redis_client.setex(_flow_key(flow_id), 1800, _json_dump(state))
        return state


async def _resolve_location(
    lat: float | None,
    lng: float | None,
    city: str | None,
    *,
    client_ip: str | None,
) -> tuple[float | None, float | None, str | None]:
    if lat is not None and lng is not None:
        resolved_city = city
        if not resolved_city:
            try:
                resolved_city = await amap.reverse_geocode_city(
                    {"lat": float(lat), "lng": float(lng)},
                    servers_path=None,
                )
            except Exception:
                resolved_city = city
        return lat, lng, resolved_city

    if client_ip:
        try:
            ip_loc, ip_city = await amap.get_ip_location(client_ip, servers_path=None)
            if ip_loc and ip_loc.get("lat") is not None and ip_loc.get("lng") is not None:
                return float(ip_loc["lat"]), float(ip_loc["lng"]), city or ip_city
        except Exception:
            pass

    return lat, lng, city


async def _build_context(city: str | None) -> DecisionContext:
    now = datetime.now()
    weather = None
    resolved_city = city
    if city:
        try:
            weather = await amap.get_weather(city, servers_path=None)
        except Exception:
            weather = None
    if not resolved_city and weather:
        resolved_city = weather.get("city")
    return DecisionContext(city=resolved_city, weather=weather, now=now)


async def _get_preference(db: AsyncSession, user_id: str | None) -> UserPreference | None:
    if not user_id:
        return None
    result = await db.execute(select(UserPreference).where(UserPreference.user_id == user_id))
    return result.scalar_one_or_none()


def _score_restaurant(
    item: dict[str, Any],
    pref: UserPreference | None,
    budget_level: int | None,
    ctx: DecisionContext,
    lat: float | None,
    lng: float | None,
) -> float:
    score = 55.0
    rating = item.get("rating")
    if isinstance(rating, (int, float)):
        score += float(rating) * 6.0

    # budget rough match (1-5)
    if budget_level and item.get("price"):
        try:
            price = float(item["price"])
            target = budget_level * 20
            score += max(-8.0, 10.0 - abs(price - target) / 5)
        except Exception:
            pass

    score += _weather_bonus(ctx.weather, prefer_hot=True)

    title = (item.get("name") or "").lower()
    if pref:
        avoids = {x.lower() for x in (pref.avoid_ingredients or [])}
        allergens = {x.lower() for x in (pref.allergens or [])}
        if any(word in title for word in avoids.union(allergens)):
            score -= 30
        tastes = {x.lower() for x in (pref.taste_tags or [])}
        if any(tag in title for tag in tastes):
            score += 8

    geo = item.get("geo") or {}
    if lat is not None and lng is not None and geo.get("lat") is not None and geo.get("lng") is not None:
        d = _distance_km(lat, lng, float(geo["lat"]), float(geo["lng"]))
        score += max(-5.0, 10.0 - d * 2.0)

    return score


def _score_recipe(
    item: dict[str, Any],
    pref: UserPreference | None,
    budget_level: int | None,
    ctx: DecisionContext,
    scene: str | None,
) -> float:
    score = 50.0
    if scene in {"home", "quick_filter"}:
        score += 6.0
    cook_time = item.get("cook_time_min")
    if isinstance(cook_time, (int, float)):
        score += max(-5.0, 12.0 - float(cook_time) / 4)
    calories = item.get("calories")
    if isinstance(calories, (int, float)):
        score += max(-3.0, 8.0 - abs(float(calories) - 520) / 120)

    score += _weather_bonus(ctx.weather, prefer_hot=True)

    title = (item.get("title") or "").lower()
    if pref:
        avoids = {x.lower() for x in (pref.avoid_ingredients or [])}
        allergens = {x.lower() for x in (pref.allergens or [])}
        if any(word in title for word in avoids.union(allergens)):
            score -= 30
        tastes = {x.lower() for x in (pref.taste_tags or [])}
        if any(tag in title for tag in tastes):
            score += 8

    if budget_level and isinstance(item.get("price_level"), (int, float)):
        score += 6.0 - abs(float(item.get("price_level")) - budget_level) * 1.5

    return score


def _weather_bonus(weather: dict[str, Any] | None, *, prefer_hot: bool) -> float:
    if not weather:
        return 0.0
    text = str(weather.get("weather") or "")
    try:
        temp = float(weather.get("temperature")) if weather.get("temperature") is not None else None
    except Exception:
        temp = None
    bonus = 0.0
    if "雨" in text:
        bonus += 4.0
    if "雪" in text:
        bonus += 5.0
    if temp is not None:
        if temp <= 10 and prefer_hot:
            bonus += 5.0
        elif temp >= 30:
            bonus -= 1.0
    return bonus


def _build_reasons(title: str, ctx: DecisionContext, scene: str | None) -> list[str]:
    weather_text = (ctx.weather or {}).get("display") or (ctx.weather or {}).get("weather")
    reasons = []
    if weather_text:
        reasons.append(f"结合当前天气（{weather_text}），这道选择更对胃口。")
    reasons.append(f"现在是{_time_slot(ctx.now)}时段，这个选择执行成本低，不容易拖延。")
    if scene:
        reasons.append(f"按你当前场景“{scene}”做了收敛，减少纠结。")
    else:
        reasons.append("这是综合口味、时间和便利性后最稳的一票。")
    return reasons[:3]


def _build_actions(chosen: dict[str, Any]) -> list[dict[str, Any]]:
    if chosen["type"] == "restaurant":
        raw = chosen.get("raw") or {}
        geo = raw.get("geo") or {}
        lat, lng = geo.get("lat"), geo.get("lng")
        name = raw.get("name") or chosen["title"]
        actions = []
        if lat is not None and lng is not None:
            actions.append({
                "type": "navigate",
                "label": "高德导航",
                "url": f"https://uri.amap.com/navigation?to={lng},{lat},{name}",
            })
        actions.append({
            "type": "search",
            "label": "美团搜索",
            "url": f"https://waimai.meituan.com/search?keyword={name}",
        })
        actions.append({
            "type": "search",
            "label": "大众点评",
            "url": f"https://www.dianping.com/search/keyword/0/0_{name}",
        })
        return actions

    raw = chosen.get("raw") or {}
    title = raw.get("title") or chosen["title"]
    return [
        {
            "type": "recipe",
            "label": "查看做法",
            "url": raw.get("source_url") or f"https://www.xiachufang.com/search/?keyword={title}",
        }
    ]


def _query_from_answers(answers: dict[str, str]) -> str:
    mapping = {
        "flavor": {"清淡": "清淡", "重口": "重口味"},
        "carb": {"面": "面食", "饭": "米饭"},
        "scene": {"外卖": "附近外卖", "在家": "快手菜"},
    }
    terms: list[str] = []
    for slot, answer in answers.items():
        terms.append(mapping.get(slot, {}).get(answer, answer))
    return " ".join(terms).strip() or "美食"


def _time_slot(now: datetime) -> str:
    h = now.hour
    if h < 10:
        return "早餐"
    if h < 14:
        return "午餐"
    if h < 18:
        return "下午"
    if h < 22:
        return "晚餐"
    return "夜宵"


def _distance_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


_QUESTION_SET = [
    {
        "slot": "flavor",
        "question": "想吃清淡一点还是重口一点？",
        "options": ["清淡", "重口"],
    },
    {
        "slot": "carb",
        "question": "更想吃面还是吃饭？",
        "options": ["面", "饭"],
    },
    {
        "slot": "scene",
        "question": "今天是点外卖还是在家做？",
        "options": ["外卖", "在家"],
    },
]


def _flow_key(flow_id: str) -> str:
    return f"decision:quick_filter:{flow_id}"


def _json_dump(obj: dict[str, Any]) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)


def _json_load(raw: str) -> dict[str, Any]:
    import json

    return json.loads(raw)
