from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools_registry import register_tool
from app.domain.context.service import ContextService


@register_tool(
    name="get_user_info",
    description=(
        "Fetch user profile, preferences, and environment snapshot. "
        "Input: {scene?:string}. Output: snapshot object with user/preferences/fridge/environment. "
        "Example input: {\"scene\":\"chat\"}."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "scene": {"type": "string"},
        },
        "required": [],
    },
    output_schema={
        "type": "object",
        "properties": {
            "user": {"type": "object"},
            "preferences": {"type": "object"},
            "fridge": {"type": "object"},
            "environment": {"type": "object"},
            "history": {"type": "object"},
            "constraints": {"type": "object"},
            "ui_scene": {"type": "string"},
        },
    },
)
async def get_user_info(args: dict[str, Any]) -> dict[str, Any]:
    db = args.get("db")
    if not isinstance(db, AsyncSession):
        raise RuntimeError("db session unavailable")
    user_id = args.get("user_id")
    scene = args.get("scene") or "chat"
    session_id = args.get("session_id")
    overrides = args.get("overrides")
    if overrides is not None and not isinstance(overrides, dict):
        overrides = None
    return await ContextService.build(
        db=db,
        user_id=user_id,
        scene=scene,
        session_id=session_id,
        overrides=overrides,
    )
