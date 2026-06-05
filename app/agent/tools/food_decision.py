from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agent.tools.native import RuntimeContext
from app.domain.decision.service import DecisionService
from app.domain.preferences.markdown_profile import (
    build_preference_context,
    read_user_preference_profile,
    update_user_preference_profile,
)
from app.infra.external.amap import amap


class FoodDecisionArgs(BaseModel):
    query: str | None = Field(default=None, description="User's food decision request.")
    city: str | None = Field(default=None, description="Optional city hint.")
    lat: float | None = Field(default=None, description="Optional latitude. Falls back to device location in runtime context.")
    lng: float | None = Field(default=None, description="Optional longitude. Falls back to device location in runtime context.")
    budget_level: int | None = Field(default=None, description="Optional budget level.")
    scene: str | None = Field(default=None, description="Decision scene.")
    runtime_context: RuntimeContext = Field(default_factory=dict)


async def _food_decision(
    query: str | None = None,
    city: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    budget_level: int | None = None,
    scene: str | None = None,
    runtime_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = runtime_context or {}
    db = ctx.get("db")
    redis_client = ctx.get("redis_client")
    if db is None or redis_client is None:
        return {"error": "missing_runtime_context"}

    prompt_context = ctx.get("context") if isinstance(ctx.get("context"), dict) else {}
    preference_context = prompt_context.get("user_preference_md") if isinstance(prompt_context.get("user_preference_md"), dict) else {}
    if not preference_context:
        profile = await read_user_preference_profile(ctx.get("user_id"))
        preference_context = build_preference_context(profile)
    location = _extract_location(prompt_context)
    location_text = _extract_location_text(prompt_context)
    resolved_city = city or prompt_context.get("city")
    resolved_lat = lat if lat is not None else (location or {}).get("lat")
    resolved_lng = lng if lng is not None else (location or {}).get("lng")
    if (resolved_lat is None or resolved_lng is None) and location_text:
        geocoded = await _geocode_location_text(
            location_text,
            city=resolved_city if isinstance(resolved_city, str) else None,
            servers_path=ctx.get("servers_path"),
        )
        if geocoded:
            resolved_lat = geocoded.get("lat")
            resolved_lng = geocoded.get("lng")
    result = await DecisionService.blindbox(
        db,
        redis_client,
        user_id=ctx.get("user_id"),
        query=query or ctx.get("last_user_message") or "今天吃点啥",
        city=resolved_city if isinstance(resolved_city, str) else None,
        lat=resolved_lat,
        lng=resolved_lng,
        budget_level=budget_level,
        scene=scene or "eat",
        client_ip=ctx.get("client_ip"),
        preference_profile=preference_context.get("profile") if isinstance(preference_context, dict) else None,
    )
    await update_user_preference_profile(
        ctx.get("user_id"),
        user_text=query or ctx.get("last_user_message") or "",
        decision_result=result,
        source="food_decision_tool",
    )
    if isinstance(preference_context, dict) and preference_context.get("summary"):
        result.setdefault("meta", {})["user_preference_summary"] = preference_context["summary"]
    return result


async def food_decision(args: dict[str, Any]) -> dict[str, Any]:
    runtime_context = {
        "db": args.get("db"),
        "redis_client": args.get("redis_client"),
        "user_id": args.get("user_id"),
        "context": args.get("context") if isinstance(args.get("context"), dict) else {},
        "client_ip": args.get("client_ip"),
        "last_user_message": args.get("last_user_message"),
    }
    return await _food_decision(
        query=args.get("query"),
        city=args.get("city"),
        lat=args.get("lat"),
        lng=args.get("lng"),
        budget_level=args.get("budget_level"),
        scene=args.get("scene"),
        runtime_context=runtime_context,
    )


food_decision_tool = StructuredTool.from_function(
    coroutine=_food_decision,
    name="food_decision",
    description=(
        "Make a concrete food decision for 'what should I eat' using the app decision engine. "
        "When device location exists in runtime context, use it to prefer nearby restaurants."
    ),
    args_schema=FoodDecisionArgs,
    infer_schema=False,
)


def _extract_location(context: dict[str, Any]) -> dict[str, float] | None:
    environment = context.get("environment") if isinstance(context.get("environment"), dict) else {}
    location = environment.get("location") if isinstance(environment.get("location"), dict) else context.get("location")
    if not isinstance(location, dict):
        return None
    try:
        lat = float(location.get("lat"))
        lng = float(location.get("lng"))
    except (TypeError, ValueError):
        return None
    if lat == 0 or lng == 0:
        return None
    return {"lat": lat, "lng": lng}


def _extract_location_text(context: dict[str, Any]) -> str | None:
    environment = context.get("environment") if isinstance(context.get("environment"), dict) else {}
    value = context.get("location_text") or environment.get("location_text")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


async def _geocode_location_text(location_text: str, *, city: str | None, servers_path: str | None) -> dict[str, float] | None:
    try:
        location = await amap.geocode_address(location_text, city, servers_path=servers_path)
        if location:
            return {"lat": float(location["lat"]), "lng": float(location["lng"])}
        pois = await amap.text_search(location_text, None, city=city, page_size=1, servers_path=servers_path)
        if pois:
            return _parse_location(pois[0].get("location"))
    except Exception:
        return None
    return None


def _parse_location(value: Any) -> dict[str, float] | None:
    if isinstance(value, dict):
        lat = value.get("lat")
        lng = value.get("lng")
        if lat is not None and lng is not None:
            return {"lat": float(lat), "lng": float(lng)}
    if isinstance(value, str) and "," in value:
        parts = value.split(",")
        if len(parts) >= 2:
            try:
                return {"lng": float(parts[0].strip()), "lat": float(parts[1].strip())}
            except ValueError:
                return None
    return None
