from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools.native import RuntimeContext
from app.infra.models.fridge import FridgeItem


class GetFridgeItemsArgs(BaseModel):
    runtime_context: RuntimeContext = Field(default_factory=dict)


async def _get_fridge_items(runtime_context: dict[str, Any] | None = None) -> dict[str, Any]:
    db = (runtime_context or {}).get("db")
    user_id = (runtime_context or {}).get("user_id")
    if not isinstance(db, AsyncSession):
        raise RuntimeError("db session unavailable")
    if not user_id:
        return {"items": []}
    result = await db.execute(
        select(FridgeItem).where(FridgeItem.user_id == user_id).limit(20)
    )
    items = result.scalars().all()
    return {
        "items": [
            {
                "id": item.id,
                "name": item.name,
                "quantity": item.quantity,
                "unit": item.unit,
                "expiry_date": item.expiry_date.isoformat() if item.expiry_date else None,
                "source": item.source,
            }
            for item in items
        ]
    }


get_fridge_items_tool = StructuredTool.from_function(
    coroutine=_get_fridge_items,
    name="get_fridge_items",
    description="Fetch the user's fridge items. Input: {}. Output: {items:[...]}.",
    args_schema=GetFridgeItemsArgs,
    infer_schema=False,
)
