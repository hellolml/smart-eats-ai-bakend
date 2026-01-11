from __future__ import annotations

from typing import Any
from datetime import datetime, timezone

from app.agent.tools_registry import register_tool


@register_tool(
    name="ping",
    description="Return a simple heartbeat with server time",
    args_schema={
        "type": "object",
        "properties": {},
        "required": [],
    },
)
async def ping(_: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "server_time": datetime.now(timezone.utc).isoformat()}
