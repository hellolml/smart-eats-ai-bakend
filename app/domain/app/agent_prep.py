from __future__ import annotations

import re
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.agent_state import AgentContext, agent_context_from_mapping, dump_agent_context
from app.agent.intent import infer_chat_intent as _infer_chat_intent
from app.infra.models.chat import ChatMessage


async def latest_travel_final_json(db: AsyncSession, session_id: str) -> dict[str, Any] | None:
    return await latest_plan_final_json(db, session_id, plan_type="travel")


async def latest_scene_final_json(db: AsyncSession, session_id: str, *, scene: str) -> dict[str, Any] | None:
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id, ChatMessage.role == "assistant")
        .order_by(desc(ChatMessage.created_at))
        .limit(10)
    )
    for row in result.scalars().all():
        payload = row.tool_payload_json if isinstance(row.tool_payload_json, dict) else {}
        answer = payload.get("answer")
        if isinstance(answer, dict) and answer.get("scene") == scene:
            return answer
        agent_result = payload.get("agent_result")
        final = agent_result.get("final") if isinstance(agent_result, dict) else None
        if isinstance(final, dict) and final.get("scene") == scene:
            return final
    return None


async def latest_plan_final_json(
    db: AsyncSession,
    session_id: str,
    *,
    plan_type: str | None = None,
) -> dict[str, Any] | None:
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id, ChatMessage.role == "assistant")
        .order_by(desc(ChatMessage.created_at))
        .limit(10)
    )
    for row in result.scalars().all():
        payload = row.tool_payload_json if isinstance(row.tool_payload_json, dict) else {}
        answer = payload.get("answer")
        if not isinstance(answer, dict) or not answer.get("state"):
            continue
        if plan_type and answer.get("plan_type") not in {None, plan_type}:
            continue
        return answer
    return None


async def latest_restaurant_recommendations(
    db: AsyncSession,
    session_id: str,
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id, ChatMessage.role == "assistant")
        .order_by(desc(ChatMessage.created_at))
        .limit(10)
    )
    restaurants: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for row in result.scalars().all():
        payload = row.tool_payload_json if isinstance(row.tool_payload_json, dict) else {}
        for item in _restaurant_recommendations_from_payload(payload):
            key = str(item.get("name") or item.get("title") or "").strip()
            if not key:
                continue
            if key in seen:
                existing_index = seen[key]
                existing = restaurants[existing_index]
                if not _has_restaurant_geo(existing) and _has_restaurant_geo(item):
                    restaurants[existing_index] = item
                continue
            restaurants.append(item)
            seen[key] = len(restaurants) - 1
            if len(restaurants) >= limit:
                return restaurants
    return restaurants


async def latest_selected_restaurant(
    db: AsyncSession,
    session_id: str,
) -> dict[str, Any] | None:
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id, ChatMessage.role == "assistant")
        .order_by(desc(ChatMessage.created_at))
        .limit(10)
    )
    for row in result.scalars().all():
        payload = row.tool_payload_json if isinstance(row.tool_payload_json, dict) else {}
        selected = _selected_restaurant_from_payload(payload)
        if selected:
            return selected
    return None


async def prepare_supervisor_payload(
    db: AsyncSession,
    session_id: str,
    user_id: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    next_payload = dict(payload)
    context = agent_context_from_mapping(next_payload.get("client_context_overrides")) or AgentContext()
    latest_travel = await latest_travel_final_json(db, session_id)
    if latest_travel:
        context.latest_travel_final_json = latest_travel
    latest_home_chef = await latest_scene_final_json(db, session_id, scene="home_chef")
    latest_restaurants = await latest_restaurant_recommendations(db, session_id)
    selected_restaurant = await latest_selected_restaurant(db, session_id)
    if selected_restaurant and latest_restaurants:
        selected_restaurant = _merge_selected_restaurant_with_recommendations(selected_restaurant, latest_restaurants)
    if user_id:
        from app.domain.preferences.markdown_profile import build_preference_context, ensure_user_preference_file

        profile = await ensure_user_preference_file(user_id)
        preference_context = build_preference_context(profile)
        context.user_preference_md = preference_context
        context.food_profile = preference_context.get("profile") or {}
        context.travel_food_preferences = preference_context.get("profile") or {}
        context.travel_food_preference_summary = preference_context.get("summary")
    context_payload = dump_agent_context(context)
    if latest_restaurants and not context_payload.get("last_restaurants"):
        context_payload["last_restaurants"] = latest_restaurants
    if selected_restaurant and not context_payload.get("selected_restaurant"):
        context_payload["selected_restaurant"] = selected_restaurant
    if latest_home_chef and not context_payload.get("latest_home_chef_final_json"):
        context_payload["latest_home_chef_final_json"] = latest_home_chef
    if latest_home_chef and _message_refines_home_chef(payload.get("message")):
        context_payload["intent"] = "cook_home"
        forced = context_payload.get("forced_skill_ids")
        merged_forced = [item for item in forced if isinstance(item, str)] if isinstance(forced, list) else []
        if "home_chef" not in merged_forced:
            merged_forced.append("home_chef")
        context_payload["forced_skill_ids"] = merged_forced
    if latest_restaurants and (
        _message_selects_restaurant(payload.get("message"), latest_restaurants)
        or _message_is_restaurant_selection_followup(payload.get("message"))
    ) and not _message_is_route_request(payload.get("message")):
        context_payload.setdefault("intent", "eat_out")
        forced = context_payload.get("forced_skill_ids")
        merged_forced = [item for item in forced if isinstance(item, str)] if isinstance(forced, list) else []
        for skill_id in ("food_decision", "restaurant_finder"):
            if skill_id not in merged_forced:
                merged_forced.append(skill_id)
        context_payload["forced_skill_ids"] = merged_forced
    if context_payload:
        next_payload["client_context_overrides"] = context_payload
    return next_payload


def _restaurant_recommendations_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    recommendations: list[Any] = []
    answer = payload.get("answer")
    if isinstance(answer, dict):
        value = answer.get("recommendations")
        if isinstance(value, list):
            recommendations.extend(value)
    agent_result = payload.get("agent_result")
    final = agent_result.get("final") if isinstance(agent_result, dict) else None
    if isinstance(final, dict):
        value = final.get("recommendations")
        if isinstance(value, list):
            recommendations.extend(value)

    restaurants: list[dict[str, Any]] = []
    for item in recommendations:
        if not isinstance(item, dict) or item.get("type") != "restaurant":
            continue
        raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
        name = str(raw.get("name") or item.get("title") or item.get("name") or "").strip()
        if not name:
            continue
        raw_inner = raw.get("raw") if isinstance(raw.get("raw"), dict) else {}
        location = raw.get("location") if isinstance(raw.get("location"), dict) else {}
        if not location and isinstance(raw_inner.get("location"), dict):
            location = raw_inner["location"]
        restaurant = {
            "name": name,
            "title": item.get("title") or name,
            "address": raw.get("address") or raw_inner.get("address") or item.get("address"),
            "rating": raw.get("rating") or item.get("rating"),
            "avg_price": raw.get("avg_price") or item.get("avg_price"),
            "poi_id": raw.get("poi_id") or raw.get("id") or item.get("poi_id"),
            "lat": raw.get("lat") or raw.get("latitude") or location.get("lat") or location.get("latitude"),
            "lng": raw.get("lng") or raw.get("lon") or raw.get("longitude") or location.get("lng") or location.get("lon") or location.get("longitude"),
            "raw": raw or item,
        }
        restaurants.append({key: value for key, value in restaurant.items() if value not in (None, "", [], {})})
    return restaurants


def _selected_restaurant_from_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    candidates: list[Any] = []
    answer = payload.get("answer")
    if isinstance(answer, dict):
        candidates.append(answer.get("selected_restaurant"))
    agent_result = payload.get("agent_result")
    final = agent_result.get("final") if isinstance(agent_result, dict) else None
    if isinstance(final, dict):
        candidates.append(final.get("selected_restaurant"))
    for item in candidates:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("title") or item.get("verified_name") or "").strip()
        if name:
            return _normalize_restaurant_payload(item)
    restaurants = _restaurant_recommendations_from_payload(payload)
    if len(restaurants) == 1:
        return restaurants[0]
    return None


def _normalize_restaurant_payload(item: dict[str, Any]) -> dict[str, Any]:
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    raw_inner = raw.get("raw") if isinstance(raw.get("raw"), dict) else {}
    location = item.get("location") if isinstance(item.get("location"), dict) else {}
    if not location and isinstance(raw.get("location"), dict):
        location = raw["location"]
    if not location and isinstance(raw_inner.get("location"), dict):
        location = raw_inner["location"]
    normalized = {
        "name": item.get("name") or item.get("title") or item.get("verified_name") or raw.get("name"),
        "title": item.get("title") or item.get("name") or raw.get("name"),
        "address": item.get("address") or raw.get("address") or raw_inner.get("address"),
        "rating": item.get("rating") or raw.get("rating"),
        "avg_price": item.get("avg_price") or item.get("price") or raw.get("avg_price") or raw.get("price"),
        "poi_id": item.get("poi_id") or item.get("provider_id") or raw.get("poi_id") or raw.get("provider_id") or raw.get("id"),
        "lat": item.get("lat") or item.get("latitude") or raw.get("lat") or raw.get("latitude") or location.get("lat") or location.get("latitude"),
        "lng": item.get("lng") or item.get("lon") or item.get("longitude") or raw.get("lng") or raw.get("lon") or raw.get("longitude") or location.get("lng") or location.get("lon") or location.get("longitude"),
        "raw": raw or item,
    }
    cleaned = {key: value for key, value in normalized.items() if value not in (None, "", [], {})}
    return cleaned or item


def _merge_selected_restaurant_with_recommendations(
    selected: dict[str, Any],
    restaurants: list[dict[str, Any]],
) -> dict[str, Any]:
    if _has_restaurant_geo(selected):
        return selected
    selected_key = _restaurant_match_key(selected)
    selected_poi = _restaurant_poi_id(selected)
    for restaurant in restaurants:
        if not isinstance(restaurant, dict) or not _has_restaurant_geo(restaurant):
            continue
        if selected_poi and selected_poi == _restaurant_poi_id(restaurant):
            return {**restaurant, **{key: value for key, value in selected.items() if value not in (None, "", [], {})}}
        if selected_key and selected_key == _restaurant_match_key(restaurant):
            return {**restaurant, **{key: value for key, value in selected.items() if value not in (None, "", [], {})}}
    return selected


def _has_restaurant_geo(item: dict[str, Any]) -> bool:
    if item.get("lat") is not None and item.get("lng") is not None:
        return True
    geo = item.get("geo") if isinstance(item.get("geo"), dict) else {}
    return geo.get("lat") is not None and geo.get("lng") is not None


def _restaurant_poi_id(item: dict[str, Any]) -> str:
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    raw_inner = raw.get("raw") if isinstance(raw.get("raw"), dict) else {}
    return str(item.get("poi_id") or item.get("provider_id") or raw.get("poi_id") or raw.get("provider_id") or raw.get("id") or raw_inner.get("poi_id") or raw_inner.get("provider_id") or raw_inner.get("id") or "").strip()


def _restaurant_match_key(item: dict[str, Any]) -> str:
    name = str(item.get("name") or item.get("title") or item.get("verified_name") or "").strip()
    if not name and isinstance(item.get("raw"), dict):
        raw = item["raw"]
        name = str(raw.get("name") or raw.get("title") or "").strip()
    return _normalize_selection_text(name)


def _message_selects_restaurant(message: Any, restaurants: list[dict[str, Any]]) -> bool:
    if restaurants and _selection_index(str(message or "")) is not None:
        return True
    hints = _selection_hints(str(message or ""))
    text = _normalize_selection_text(str(message or ""))
    if not text:
        return False
    for item in restaurants:
        name = str(item.get("name") or item.get("title") or "").strip()
        for alias in _restaurant_aliases(name):
            if alias and alias in text:
                return True
            if hints and any(hint in alias for hint in hints):
                return True
    return False


def _message_is_restaurant_selection_followup(message: Any) -> bool:
    text = str(message or "")
    normalized = _normalize_selection_text(text)
    if not normalized:
        return False
    if _selection_index(text) is not None or _selection_hints(text):
        return True
    return any(
        token in text
        for token in (
            "上面推荐",
            "刚才推荐",
            "就这家",
            "就那家",
            "选这家",
            "选那家",
            "这家餐厅",
            "那家餐厅",
            "换到",
            "回到上面",
            "推荐的",
        )
    )


def _message_is_route_request(message: Any) -> bool:
    text = str(message or "")
    if _message_negates_route_request(text):
        return False
    return any(token in text for token in ("怎么走", "怎么去", "路线", "导航", "从", "过去", "到"))


def _message_refines_home_chef(message: Any) -> bool:
    text = str(message or "")
    if not text.strip():
        return False
    if _message_switches_to_eat_out(text):
        return False
    return any(
        token in text
        for token in (
            "不要辣",
            "不辣",
            "少油",
            "蛋白质",
            "步骤",
            "时间线",
            "采购",
            "清单",
            "一个锅",
            "厨具",
            "补充",
            "做法",
        )
    )


def _message_switches_to_eat_out(message: Any) -> bool:
    text = str(message or "")
    return any(
        token in text
        for token in (
            "不想做饭",
            "不做饭",
            "不想在家吃",
            "不在家吃",
            "出去吃",
            "外面吃",
            "出门吃",
            "找餐厅",
            "找饭店",
            "附近找",
        )
    )


def _message_negates_route_request(message: Any) -> bool:
    text = str(message or "")
    return any(
        token in text
        for token in (
            "不要规划路线",
            "先不要规划路线",
            "不用规划路线",
            "先不用规划路线",
            "不需要路线",
            "暂时不规划路线",
            "先不规划路线",
        )
    )


def _selection_index(value: str) -> int | None:
    text = str(value or "")
    digit = re.search(r"第\s*(\d+)\s*家|(\d+)\s*号|第\s*(\d+)\s*个", text)
    if digit:
        for group in digit.groups():
            if group:
                return max(0, int(group) - 1)
    chinese = re.search(r"第?\s*([一二两三四五六七八九十])\s*(?:家|个|号)", text)
    if not chinese and any(token in text for token in ("第一家", "第一个", "第一")):
        return 0
    if chinese:
        index = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}.get(chinese.group(1))
        return max(0, index - 1) if index else None
    return None


def _restaurant_aliases(name: str) -> list[str]:
    aliases: list[str] = []
    for value in (name, name.split("(", 1)[0], name.split("（", 1)[0]):
        cleaned = _normalize_selection_text(value)
        if len(cleaned) >= 2 and cleaned not in aliases:
            aliases.append(cleaned)
    return aliases


def _normalize_selection_text(value: str) -> str:
    text = str(value or "").strip().lower()
    for token in (" ", "\t", "\n", "，", "。", "！", "？", "!", "?", ",", ".", "“", "”", "\"", "'", "就", "选", "去"):
        text = text.replace(token, "")
    while text.endswith(("吧", "把", "呗", "呢", "啦", "了")):
        text = text[:-1]
    return text


def _selection_hints(value: str) -> list[str]:
    hints: list[str] = []
    for pattern in (r"名字带[“\"']?([^“”\"'，。！？\s]{1,8})", r"[“\"']([^“”\"']{1,8})[”\"']"):
        for match in re.findall(pattern, str(value or "")):
            cleaned = _normalize_selection_text(match)
            if len(cleaned) >= 2 and cleaned not in hints:
                hints.append(cleaned)
    return hints


async def merge_current_session_travel_context(
    db: AsyncSession,
    session_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    scene = payload.get("scene") or "chat"
    if scene != "travel_planner":
        return payload
    latest = await latest_travel_final_json(db, session_id)
    if not latest:
        return payload
    current = payload.get("travel_payload")
    current = current if isinstance(current, dict) else {}
    base = {
        "previous_final_json": latest,
        "state": latest.get("state"),
        "trip_meta": latest.get("trip_meta"),
        "sources": latest.get("sources"),
        "places": latest.get("places"),
        "candidates": latest.get("candidates"),
        "failed_places": latest.get("failed_places"),
        "itinerary": latest.get("itinerary"),
        "map": latest.get("map"),
        "raw_text": latest.get("raw_text"),
    }
    merged = {key: value for key, value in base.items() if value not in (None, [], {})}
    merged.update(current)
    next_payload = dict(payload)
    next_payload["travel_payload"] = merged
    return next_payload


def infer_chat_intent(message: Any) -> str | None:
    return _infer_chat_intent(message)


def forced_skill_ids_for_intent(intent: str) -> list[str]:
    if intent == "food":
        return ["food_decision", "restaurant_finder"]
    if intent == "route":
        return ["route_planner"]
    return []


def build_chat_context_overrides(payload: dict[str, Any]) -> dict[str, Any] | None:
    context = agent_context_from_mapping(payload.get("client_context_overrides"))
    context_overrides = (
        dump_agent_context(context)
        if context is not None
        else None
    )
    attachments = payload.get("attachments")
    if isinstance(attachments, list):
        clean_attachments = [item for item in attachments if isinstance(item, dict)]
        if clean_attachments:
            context_overrides = context_overrides or {}
            context_overrides["attachments"] = clean_attachments
    scene = payload.get("scene") or "chat"
    inferred_intent = None if scene == "travel_planner" else infer_chat_intent(payload.get("message"))
    if inferred_intent:
        context_overrides = context_overrides or {}
        context_overrides.setdefault("intent", inferred_intent)
        forced_skill_ids = forced_skill_ids_for_intent(inferred_intent)
        if forced_skill_ids:
            existing_forced = context_overrides.get("forced_skill_ids")
            merged_forced = []
            if isinstance(existing_forced, list):
                merged_forced.extend(item for item in existing_forced if isinstance(item, str))
            merged_forced.extend(item for item in forced_skill_ids if item not in merged_forced)
            context_overrides["forced_skill_ids"] = merged_forced
    if payload.get("travel_action"):
        context_overrides = context_overrides or {}
        context_overrides["travel_action"] = payload.get("travel_action")
    if isinstance(payload.get("travel_payload"), dict):
        context_overrides = context_overrides or {}
        context_overrides["travel_payload"] = payload.get("travel_payload")
    if payload.get("agent_id"):
        context_overrides = context_overrides or {}
        context_overrides["agent_id"] = payload.get("agent_id")
    if payload.get("plan_type"):
        context_overrides = context_overrides or {}
        context_overrides["plan_type"] = payload.get("plan_type")
    if payload.get("action"):
        context_overrides = context_overrides or {}
        context_overrides["action"] = payload.get("action")
    if isinstance(payload.get("payload"), dict):
        context_overrides = context_overrides or {}
        context_overrides["payload"] = payload.get("payload")
    return context_overrides
