from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool

from app.agent.tools.context_memory import (
    memory_forget_tool,
    memory_search_tool,
    memory_update_tool,
    memory_write_tool,
    source_event_search_tool,
)
from app.agent.tools.food_decision import food_decision_tool
from app.agent.tools.geocode_location import geocode_location_tool
from app.agent.tools.get_fridge_items import get_fridge_items_tool
from app.agent.tools.get_ip_location import get_ip_location_tool
from app.agent.tools.get_user_info import get_user_info_tool
from app.agent.tools.get_weather import get_weather_tool
from app.agent.tools.plan_route import plan_route_tool
from app.agent.tools.rag_search_recipes import rag_search_recipes_tool
from app.agent.tools.search_recipes import search_recipes_tool
from app.agent.tools.search_restaurants import search_restaurants_tool
from app.agent.tools.travel_create_personal_map import travel_create_personal_map_tool
from app.agent.tools.travel_search_poi import travel_search_poi_tool


ALL_TOOLS: tuple[BaseTool, ...] = (
    get_weather_tool,
    get_fridge_items_tool,
    search_recipes_tool,
    rag_search_recipes_tool,
    food_decision_tool,
    search_restaurants_tool,
    plan_route_tool,
    get_ip_location_tool,
    geocode_location_tool,
    get_user_info_tool,
    memory_search_tool,
    memory_write_tool,
    memory_update_tool,
    memory_forget_tool,
    source_event_search_tool,
    travel_search_poi_tool,
    travel_create_personal_map_tool,
)


def all_tools() -> tuple[BaseTool, ...]:
    return ALL_TOOLS


def tool_names() -> list[str]:
    return [tool.name for tool in ALL_TOOLS]


def select_tools(allowlist: list[str] | None = None) -> list[BaseTool]:
    if not allowlist:
        return list(ALL_TOOLS)
    names = set(allowlist)
    return [tool for tool in ALL_TOOLS if tool.name in names]


def describe_tools(allowlist: list[str] | None = None) -> list[dict[str, Any]]:
    return [
        {
            "name": tool.name,
            "description": str(tool.description or ""),
            "input_schema": _tool_call_schema(tool),
        }
        for tool in select_tools(allowlist)
    ]


def _tool_call_schema(tool: BaseTool) -> dict[str, Any]:
    schema = getattr(tool, "tool_call_schema", None)
    if hasattr(schema, "model_json_schema"):
        return schema.model_json_schema()
    args = getattr(tool, "args", None)
    if isinstance(args, dict):
        return {"type": "object", "properties": args}
    return {"type": "object", "properties": {}}
