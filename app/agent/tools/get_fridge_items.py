from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools_registry import register_tool
from app.infra.models.fridge import FridgeItem


@register_tool(
    name="get_fridge_items",
    description=(
        "Fetch the user's fridge items. "
        "Input: {}. Output: {items:[{id,name,quantity,unit,expiry_date,source},...]}. "
        "Example input: {}."
    ),
    input_schema={
        "type": "object",
        "properties": {},
        "required": [],
    },
    output_schema={
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": {"type": "object"}},
        },
    },
)
async def get_fridge_items(args: dict[str, Any]) -> dict[str, Any]:
    """获取用户冰箱食材列表（原子化工具，不再内嵌食谱搜索）"""
    db = args.get("db")
    user_id = args.get("user_id")
    if not isinstance(db, AsyncSession):
        raise RuntimeError("db session unavailable")
    if not user_id:
        return {"items": []}
    result = await db.execute(
        select(FridgeItem).where(FridgeItem.user_id == user_id).limit(20)
    )
    items = result.scalars().all()
    payload_items = [
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
    return {"items": payload_items}
