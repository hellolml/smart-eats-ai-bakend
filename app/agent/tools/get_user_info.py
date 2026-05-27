from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools.native import RuntimeContext
from app.domain.context.service import ContextService


class GetUserInfoArgs(BaseModel):
    scene: str | None = Field(default=None, description="UI scene or conversation scene.")
    runtime_context: RuntimeContext = Field(default_factory=dict)


async def _get_user_info(
    scene: str | None = None,
    runtime_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = runtime_context or {}
    db = ctx.get("db")
    if not isinstance(db, AsyncSession):
        raise RuntimeError("db session unavailable")
    return await ContextService.build(
        db=db,
        user_id=ctx.get("user_id"),
        scene=scene or "chat",
        session_id=ctx.get("session_id"),
        overrides=ctx.get("context") if isinstance(ctx.get("context"), dict) else None,
    )


get_user_info_tool = StructuredTool.from_function(
    coroutine=_get_user_info,
    name="get_user_info",
    description="Fetch user profile, preferences, and environment snapshot.",
    args_schema=GetUserInfoArgs,
    infer_schema=False,
)
