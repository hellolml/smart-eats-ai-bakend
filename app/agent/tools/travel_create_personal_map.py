from __future__ import annotations

from typing import Any

from app.agent.tools_registry import register_tool
from app.infra.external.amap.amap import create_personal_map


def _normalize_scene_type(value: Any) -> int:
    try:
        scene_type = int(value)
    except (TypeError, ValueError):
        return 1
    return scene_type if scene_type in {1, 2, 3} else 1


@register_tool(
    name="travel_create_personal_map",
    description=(
        "Create an AMap personal map QR code for a travel itinerary. "
        "Input: {title:string,line_list:[{title,pointInfoList:[{name,lon,lat,poiId?}]}],scene_type?:integer}. "
        "Output: {title,line_list,qr_code_url?,schema_url?,raw?} or {error:string}."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "line_list": {"type": "array", "items": {"type": "object"}},
            "scene_type": {"type": "integer"},
        },
        "required": ["title", "line_list"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "line_list": {"type": "array", "items": {"type": "object"}},
            "qr_code_url": {"type": "string"},
            "schema_url": {"type": "string"},
            "raw": {"type": "object"},
            "error": {"type": "string"},
        },
    },
)
async def travel_create_personal_map(args: dict[str, Any]) -> dict[str, Any]:
    title = str(args.get("title") or "").strip()
    line_list = args.get("line_list")
    if not title:
        return {"error": "missing_title"}
    if not isinstance(line_list, list) or not line_list:
        return {"error": "missing_line_list"}

    payload = await create_personal_map(
        title,
        [item for item in line_list if isinstance(item, dict)],
        scene_type=_normalize_scene_type(args.get("scene_type")),
        servers_path=args.get("servers_path"),
    )
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

