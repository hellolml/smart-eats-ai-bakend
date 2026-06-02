from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

import httpx
import redis.asyncio as redis
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.llm_adapters import ProviderRegistry
from app.common.config import settings
from app.domain.recipe.service import RecipeService
from app.domain.restaurant.service import RestaurantService
from app.infra.external.amap import amap
from app.infra.models.preference import UserPreference, UserTasteProfile


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
        taste_profile = await _get_taste_profile(db, user_id)
        hard_filter_terms = _hard_filter_terms(pref, taste_profile)

        restaurant_query = _restaurant_search_query(query, scene)
        restaurants = await RestaurantService.search(
            redis_client,
            query=restaurant_query,
            tag=None,
            lat=lat,
            lng=lng,
            sort="rating_desc",
            city=city,
        )
        if restaurants and hard_filter_terms:
            restaurants = [
                item
                for item in restaurants
                if not _contains_blocked_terms(str(item.get("name") or ""), hard_filter_terms)
            ]

        recipes: list[dict[str, Any]] = []
        chosen: dict[str, Any] | None = None

        # 用户要求：优先从附近餐厅里随机抽一家
        if restaurants:
            picked = await _pick_with_recent_guard(
                redis_client,
                candidates=restaurants,
                memory_key=_blindbox_memory_key(
                    user_id=user_id,
                    client_ip=client_ip,
                    scope="restaurant",
                    query=query,
                    scene=scene,
                ),
                candidate_id_fn=_restaurant_candidate_id,
            )
            if picked is not None:
                score = _score_restaurant(picked, pref, budget_level, decision_ctx, lat, lng)
                chosen = {
                    "type": "restaurant",
                    "id": f"amap:{picked.get('provider_id') or uuid4()}",
                    "title": picked.get("name") or "附近美食",
                    "score": score,
                    "raw": picked,
                }

        if chosen is None and _allow_recipe_fallback(scene):
            recipes = await RecipeService.search(redis_client, query or "快手菜")
            if recipes and hard_filter_terms:
                recipes = [
                    item
                    for item in recipes
                    if not _contains_blocked_terms(str(item.get("title") or ""), hard_filter_terms)
                ]
            if recipes:
                picked_recipe = await _pick_with_recent_guard(
                    redis_client,
                    candidates=recipes,
                    memory_key=_blindbox_memory_key(
                        user_id=user_id,
                        client_ip=client_ip,
                        scope="recipe",
                        query=query,
                        scene=scene,
                    ),
                    candidate_id_fn=_recipe_candidate_id,
                )
                if picked_recipe is not None:
                    score = _score_recipe(picked_recipe, pref, budget_level, decision_ctx, scene)
                    chosen = {
                        "type": "recipe",
                        "id": f"recipe:{(picked_recipe.get('title') or str(uuid4()))}",
                        "title": picked_recipe.get("title") or "家常菜",
                        "score": score,
                        "raw": picked_recipe,
                    }

        if chosen is None:
            ai_choice = await _generate_cn_home_style_fallback(
                query=query,
                scene=scene,
                weather=decision_ctx.weather,
            )
            if ai_choice is not None:
                chosen = {
                    "type": "fallback",
                    "id": f"food:{uuid4()}",
                    "title": ai_choice,
                    "score": 46.0,
                    "raw": {"title": ai_choice, "source": "ai_generated"},
                }

        if chosen is None:
            food_pool = [
                "番茄炒蛋盖饭", "青椒肉丝盖饭", "宫保鸡丁", "鱼香肉丝", "麻婆豆腐", "蒜香小酥肉", "锅贴", "生煎包", "鸡丝凉面", "葱油拌面",
            ]
            fallback_title = await _pick_fallback_title_with_recent_guard(
                redis_client,
                memory_key=_blindbox_memory_key(
                    user_id=user_id,
                    client_ip=client_ip,
                    scope="fallback",
                    query=query,
                    scene=scene,
                ),
                candidates=food_pool,
            )
            chosen = {
                "type": "fallback",
                "id": f"food:{uuid4()}",
                "title": fallback_title,
                "score": 45.0,
                "raw": {"title": fallback_title, "source": "fallback_pool"},
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


async def _get_taste_profile(db: AsyncSession, user_id: str | None) -> UserTasteProfile | None:
    if not user_id:
        return None
    result = await db.execute(select(UserTasteProfile).where(UserTasteProfile.user_id == user_id))
    return result.scalar_one_or_none()


def _hard_filter_terms(pref: UserPreference | None, taste_profile: UserTasteProfile | None) -> set[str]:
    terms: set[str] = set()
    if pref:
        terms.update({x.strip().lower() for x in (pref.avoid_ingredients or []) if str(x).strip()})
        terms.update({x.strip().lower() for x in (pref.allergens or []) if str(x).strip()})
    if taste_profile:
        terms.update({x.strip().lower() for x in (taste_profile.dislikes or []) if str(x).strip()})
        terms.update({x.strip().lower() for x in (taste_profile.allergens or []) if str(x).strip()})
    return terms


def _contains_blocked_terms(text: str, terms: set[str]) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in terms)


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
        scene_label = {"eat": "吃点啥", "food_decision": "吃点啥", "cook_home": "在家做饭"}.get(scene, scene)
        reasons.append(f"按你当前场景“{scene_label}”做了收敛，减少纠结。")
    else:
        reasons.append("这是综合口味、时间和便利性后最稳的一票。")
    return reasons[:3]


def _restaurant_search_query(query: str | None, scene: str | None) -> str:
    text = (query or "").strip()
    compact = text.replace("？", "").replace("?", "").replace("！", "").replace("!", "").strip()
    generic_terms = {
        "",
        "吃点啥",
        "吃什么",
        "今天吃点啥",
        "今天吃点啥？",
        "今天吃什么",
        "今天吃什么？",
        "午饭吃什么",
        "晚饭吃什么",
        "早餐吃什么",
        "夜宵吃什么",
    }
    if compact in generic_terms or scene in {"eat", "food_decision"} and len(compact) <= 8 and any(token in compact for token in ("吃", "饭", "餐")):
        return "美食"
    return text or "美食"


def _allow_recipe_fallback(scene: str | None) -> bool:
    return scene not in {"blindbox", "eat", "food_decision"}


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


def _blindbox_memory_key(
    *,
    user_id: str | None,
    client_ip: str | None,
    scope: str,
    query: str | None,
    scene: str | None,
) -> str:
    identity = user_id or (client_ip or "anon")
    seed = f"{scope}|{identity}|{(query or '').strip()}|{(scene or '').strip()}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:20]
    return f"decision:blindbox:last:{digest}"


def _restaurant_candidate_id(item: dict[str, Any]) -> str:
    return str(item.get("provider_id") or item.get("name") or "")


def _recipe_candidate_id(item: dict[str, Any]) -> str:
    return str(item.get("title") or item.get("id") or "")


async def _pick_with_recent_guard(
    redis_client: redis.Redis,
    *,
    candidates: list[dict[str, Any]],
    memory_key: str,
    candidate_id_fn,
) -> dict[str, Any] | None:
    unique_candidates = _unique_candidates(candidates, candidate_id_fn)
    last_id = _decode_redis_text(await redis_client.get(memory_key))

    if len(unique_candidates) == 1:
        only = unique_candidates[0]
        only_id = _normalize_candidate_id(candidate_id_fn(only))
        if only_id and only_id == _normalize_candidate_id(last_id):
            return None
        if only_id:
            await redis_client.setex(memory_key, 600, only_id)
        return only

    pool = [
        item
        for item in unique_candidates
        if _normalize_candidate_id(candidate_id_fn(item)) != _normalize_candidate_id(last_id)
    ]
    picked = random.choice(pool or unique_candidates)
    picked_id = _normalize_candidate_id(candidate_id_fn(picked))
    if picked_id:
        await redis_client.setex(memory_key, 600, picked_id)
    return picked


async def _pick_fallback_title_with_recent_guard(
    redis_client: redis.Redis,
    *,
    memory_key: str,
    candidates: list[str],
) -> str:
    unique_candidates = _unique_titles(candidates)
    if len(unique_candidates) == 1:
        return unique_candidates[0]

    last_id = _decode_redis_text(await redis_client.get(memory_key))
    pool = [item for item in unique_candidates if _normalize_candidate_id(item) != _normalize_candidate_id(last_id)]
    picked = random.choice(pool or unique_candidates)
    await redis_client.setex(memory_key, 600, _normalize_candidate_id(picked))
    return picked


def _normalize_candidate_id(value: str | None) -> str:
    return (value or "").strip().lower()


def _decode_redis_text(value: Any) -> str:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except Exception:
            return ""
    if isinstance(value, str):
        return value
    return ""


def _unique_candidates(candidates: list[dict[str, Any]], candidate_id_fn) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in candidates:
        cid = _normalize_candidate_id(candidate_id_fn(item))
        if not cid:
            cid = hashlib.sha1(_json_dump(item).encode("utf-8")).hexdigest()
        if cid in seen:
            continue
        seen.add(cid)
        unique.append(item)
    return unique or candidates


def _unique_titles(candidates: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        cid = _normalize_candidate_id(item)
        if not cid or cid in seen:
            continue
        seen.add(cid)
        unique.append(item)
    return unique or candidates


async def _generate_cn_home_style_fallback(
    *,
    query: str | None,
    scene: str | None,
    weather: dict[str, Any] | None,
) -> str | None:
    try:
        provider = ProviderRegistry.get(settings.LLM_PROVIDER)
        if not provider.api_key:
            return None

        client = AsyncOpenAI(
            api_key=provider.api_key,
            base_url=provider.base_url,
            max_retries=1,
            timeout=httpx.Timeout(20.0, connect=5.0),
        )
        prompt = (
            "你是中文美食推荐助手。\n"
            "当附近餐厅没有结果时，请在‘家常便饭菜/小吃’范围内给出一个推荐菜名。\n"
            "必须满足：\n"
            "1) 只输出中文菜名，不要解释，不要标点，不要编号；\n"
            "2) 禁止输出英文或拼音；\n"
            "3) 优先常见家常菜或常见小吃。"
        )
        user_text = json.dumps(
            {
                "query": query or "美食",
                "scene": scene or "blindbox",
                "weather": weather or {},
            },
            ensure_ascii=False,
        )
        resp = await client.chat.completions.create(
            model=provider.model_writer,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_text},
            ],
            temperature=0.8,
        )
        text = str(resp.choices[0].message.content or "").strip()
        text = text.replace("\n", " ").replace("\r", " ").strip(" 。；;:：!！?？")
        if not text:
            return None
        if not _is_chinese_text(text):
            return None
        return text[:16]
    except Exception:
        return None


def _is_chinese_text(text: str) -> bool:
    clean = text.strip()
    if not clean:
        return False
    has_cjk = False
    for ch in clean:
        code = ord(ch)
        if 0x4E00 <= code <= 0x9FFF:
            has_cjk = True
            continue
        if ch in "·-（）() ":
            continue
        if ch.isdigit():
            continue
        return False
    return has_cjk


def _json_dump(obj: dict[str, Any]) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)


def _json_load(raw: str) -> dict[str, Any]:
    import json

    return json.loads(raw)
