from __future__ import annotations

from typing import Any

from app.agent.tools_registry import register_tool
from app.domain.decision.service import DecisionService


@register_tool(
    name="food_decision",
    description=(
        "Make a concrete food decision for 'what should I eat'. "
        "Input: {query?:string, city?:string, lat?:number, lng?:number, budget_level?:integer, scene?:string}. "
        "Output: existing decision payload {decision,reasons,actions,meta}."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "city": {"type": "string"},
            "lat": {"type": "number"},
            "lng": {"type": "number"},
            "budget_level": {"type": "integer"},
            "scene": {"type": "string"},
        },
    },
    output_schema={
        "type": "object",
        "properties": {
            "decision": {"type": "object"},
            "reasons": {"type": "array", "items": {"type": "string"}},
            "actions": {"type": "array", "items": {"type": "object"}},
            "meta": {"type": "object"},
            "error": {"type": "string"},
        },
    },
)
async def food_decision(args: dict[str, Any]) -> dict[str, Any]:
    db = args.get("db")
    redis_client = args.get("redis_client")
    if db is None or redis_client is None:
        return {"error": "missing_runtime_context"}
    context = args.get("context") if isinstance(args.get("context"), dict) else {}
    location = _extract_location(context)
    city = args.get("city") or context.get("city")
    return await DecisionService.blindbox(
        db,
        redis_client,
        user_id=args.get("user_id"),
        query=args.get("query") or args.get("last_user_message") or "今天吃点啥",
        city=city if isinstance(city, str) else None,
        lat=args.get("lat") if args.get("lat") is not None else (location or {}).get("lat"),
        lng=args.get("lng") if args.get("lng") is not None else (location or {}).get("lng"),
        budget_level=args.get("budget_level"),
        scene=args.get("scene") or "food_decision",
        client_ip=args.get("client_ip"),
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
