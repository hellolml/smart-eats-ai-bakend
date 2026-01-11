from __future__ import annotations

from typing import Any

from app.agent.tools_registry import register_tool


@register_tool(
    name="get_weather",
    description="Get weather by city name",
    args_schema={
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
)
async def get_weather(args: dict[str, Any]) -> dict[str, Any]:
    city = args.get("city") or "unknown"
    return {"city": city, "status": "sunny", "temperature_c": 26}
