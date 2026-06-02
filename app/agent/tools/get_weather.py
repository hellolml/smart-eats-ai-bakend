from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agent.tools.native import RuntimeContext
from app.infra.external.amap import amap


class GetWeatherArgs(BaseModel):
    city: str = Field(..., description="City name.")
    runtime_context: RuntimeContext = Field(default_factory=dict)


async def _get_weather(
    city: str,
    runtime_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = runtime_context or {}
    return await amap.get_weather(city or "unknown", servers_path=ctx.get("servers_path"))


get_weather_tool = StructuredTool.from_function(
    coroutine=_get_weather,
    name="get_weather",
    description="Get weather by city name. Input: {city:string}. Output: {city,status,temperature_c,raw}.",
    args_schema=GetWeatherArgs,
    infer_schema=False,
)
