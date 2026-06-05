from __future__ import annotations

from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.agent_state import AgentContext, agent_context_from_mapping, dump_agent_context
from app.agent.intent import infer_chat_intent as _infer_chat_intent
from app.infra.models.chat import ChatMessage


async def latest_travel_final_json(db: AsyncSession, session_id: str) -> dict[str, Any] | None:
    return await latest_plan_final_json(db, session_id, plan_type="travel")


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
    if user_id:
        from app.domain.preferences.markdown_profile import build_preference_context, ensure_user_preference_file

        profile = await ensure_user_preference_file(user_id)
        preference_context = build_preference_context(profile)
        context.user_preference_md = preference_context
        context.food_profile = preference_context.get("profile") or {}
        context.travel_food_preferences = preference_context.get("profile") or {}
        context.travel_food_preference_summary = preference_context.get("summary")
    context_payload = dump_agent_context(context)
    if context_payload:
        next_payload["client_context_overrides"] = context_payload
    return next_payload


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
