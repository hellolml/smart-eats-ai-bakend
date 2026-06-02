from __future__ import annotations

from typing import Any

import httpx

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agent.tools.native import RuntimeContext
from app.infra.external.amap.amap import create_personal_map


def _normalize_scene_type(value: Any) -> int:
    try:
        scene_type = int(value)
    except (TypeError, ValueError):
        return 1
    return scene_type if scene_type in {1, 2, 3} else 1


def _validate_line_list(line_list: list[dict[str, Any]]) -> tuple[bool, str | None]:
    valid_line_count = 0
    for line_index, line in enumerate(line_list, start=1):
        if not isinstance(line, dict):
            return False, f"line_list 第 {line_index} 项不是对象"
        points = line.get("pointInfoList")
        if not isinstance(points, list) or not points:
            continue
        valid_line_count += 1
        for point_index, point in enumerate(points, start=1):
            if not isinstance(point, dict):
                return False, f"第 {line_index} 天第 {point_index} 个点不是对象"
            missing = [
                key
                for key in ("name", "poiId", "lon", "lat")
                if point.get(key) in (None, "")
            ]
            if missing:
                return False, f"第 {line_index} 天第 {point_index} 个点缺少 {', '.join(missing)}"
    if valid_line_count <= 0:
        return False, "line_list 没有有效点位"
    return True, None


class TravelCreatePersonalMapArgs(BaseModel):
    title: str = Field(..., description="Map title.")
    line_list: list[dict[str, Any]] = Field(..., description="AMap line list payload.")
    scene_type: int | None = Field(default=None, description="AMap scene type.")
    runtime_context: RuntimeContext = Field(default_factory=dict)


async def _travel_create_personal_map(
    title: str,
    line_list: list[dict[str, Any]],
    scene_type: int | None = None,
    runtime_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = runtime_context or {}
    title = str(title or "").strip()
    if not title:
        return {"error": "missing_title"}
    if not isinstance(line_list, list) or not line_list:
        return {"error": "missing_line_list"}
    valid, reason = _validate_line_list([item for item in line_list if isinstance(item, dict)])
    if not valid:
        return {
            "title": title,
            "line_list": line_list,
            "error": "invalid_line_list",
            "message": reason or "地图点位不完整",
        }

    try:
        payload = await create_personal_map(
            title,
            [item for item in line_list if isinstance(item, dict)],
            scene_type=_normalize_scene_type(scene_type),
            servers_path=ctx.get("servers_path"),
        )
    except (TimeoutError, httpx.TimeoutException) as exc:
        return {
            "title": title,
            "line_list": line_list,
            "error": "personal_map_timeout",
            "message": f"高德地图生成超时：{exc}",
        }
    except Exception as exc:
        return {
            "title": title,
            "line_list": line_list,
            "error": "personal_map_failed",
            "message": f"高德地图生成失败：{exc}",
        }
    if not payload:
        return {
            "title": title,
            "line_list": line_list,
            "error": "personal_map_unavailable",
        }
    return {
        "title": title,
        "line_list": line_list,
        "qr_code_url": payload.get("qr_code_url") or payload.get("qrCodeUrl"),
        "schema_url": payload.get("schema_url") or payload.get("schemaUrl"),
        "raw": payload,
    }


travel_create_personal_map_tool = StructuredTool.from_function(
    coroutine=_travel_create_personal_map,
    name="travel_create_personal_map",
    description=(
        "Create an AMap personal map QR code for a travel itinerary. "
        "Input: {title:string,line_list:[...],scene_type?:integer}."
    ),
    args_schema=TravelCreatePersonalMapArgs,
    infer_schema=False,
)
