from __future__ import annotations

import json
import logging
import re
from typing import Any, AsyncGenerator

import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import conversation
from app.agent.state import ChatState
from app.agent.tools.travel_create_personal_map import travel_create_personal_map
from app.agent.tools.travel_search_poi import travel_search_poi
from app.common.config import settings

logger = logging.getLogger("agent.travel_workflow")

_STATE_TTL_SECONDS = 3600 * 24


async def run_travel_planner_workflow(
    request: Any,
    db: AsyncSession | None,
    redis_client: redis.Redis,
    state: ChatState,
) -> AsyncGenerator[dict[str, Any], None]:
    action = _travel_action(state)
    if db is not None:
        await conversation.save_user_message(
            db,
            redis_client,
            state.session_id,
            state.message or _action_message(action),
        )

    yield {"event": "thinking", "data": {"status": "start", "workflow": "travel_planner"}}
    if await _should_stop(request, redis_client, state.session_id):
        yield {"event": "final", "data": {"stopped": True}}
        return

    if action in {"confirm_candidates", "update_candidates"}:
        final_json = await _finalize_itinerary(redis_client, state)
    else:
        final_json = await _prepare_candidates(redis_client, state)

    text = _render_travel_text(final_json)
    if await _should_stop(request, redis_client, state.session_id):
        yield {"event": "final", "data": {"stopped": True}}
        return
    yield {"event": "delta", "data": {"token": text}}
    yield {"event": "thinking", "data": {"status": "done", "workflow": "travel_planner"}}
    if db is not None:
        await conversation.save_assistant_message(db, redis_client, state.session_id, text, final_json)
    yield {"event": "final", "data": {"stopped": False, "answer": final_json}}


async def _prepare_candidates(redis_client: redis.Redis, state: ChatState) -> dict[str, Any]:
    constraints = _extract_constraints(state.message or "", _travel_payload(state).get("constraints"))
    names = _candidate_names(state.message or "", _travel_payload(state).get("candidates"), constraints)
    if _has_attachments(state):
        vision_names = await _extract_names_from_attachments(state)
        if vision_names:
            names = list(dict.fromkeys([*names, *vision_names]))[:16]
        else:
            destination = str(constraints.get("destination") or "").strip()
            names = [name for name in names if name != destination]
    verified: list[dict[str, Any]] = []
    for name in names:
        result = await travel_search_poi(
            {
                "keywords": name,
                "city": constraints.get("destination"),
                "page_size": 3,
                "redis_client": redis_client,
                "session_id": state.session_id,
                "servers_path": settings.MCP_SERVERS_CONFIG_PATH,
            }
        )
        poi = _first_valid_poi(result.get("pois") if isinstance(result, dict) else None)
        if poi:
            verified.append({"source_name": name, **poi})

    if not verified:
        payload = _empty_candidates_payload(constraints, names, has_attachments=_has_attachments(state))
        await _save_state(redis_client, state.session_id, {"stage": payload["status"], "constraints": constraints, "candidates": []})
        return payload

    await _save_state(redis_client, state.session_id, {"stage": "await_confirmation", "constraints": constraints, "candidates": verified})
    return {
        "type": "travel_plan",
        "status": "await_confirmation",
        "constraints": constraints,
        "candidates": verified,
        "itinerary": {"days": []},
        "map": {},
        "raw_text": _candidate_summary(verified),
        "recommendations": [
            {
                "type": "travel_candidates",
                "title": f"已验证 {len(verified)} 个候选地点，等待你确认",
                "reason": "确认后我会继续生成每日行程并创建高德个人地图。",
            }
        ],
        "followups": ["确认这些地点", "删除或补充候选地点后再确认"],
        "warnings": [] if verified else ["暂未验证到有效 POI，请补充更明确的地点名。"],
    }


def _empty_candidates_payload(constraints: dict[str, Any], names: list[str], *, has_attachments: bool) -> dict[str, Any]:
    if has_attachments:
        title = "攻略图片已收到，暂未提取到可验证地点"
        reason = "当前确定性旅行流程只能验证明确地点名；请补充图片里的景点、餐厅或酒店文字，我会继续验证 POI。"
        raw_text = "请直接输入攻略中的地点名称，例如：西湖、灵隐寺、法喜寺。"
        status = "await_candidate_text"
        warnings = ["图片附件已收到，但未从本轮输入中获得可用于高德验证的地点名。"]
    else:
        title = "暂未验证到有效候选地点"
        reason = "请补充更明确的景点、餐厅或酒店名称。"
        raw_text = "未验证地点：" + ("、".join(names) if names else "无")
        status = "await_candidate_text"
        warnings = ["未验证到有效 POI。"]
    return {
        "type": "travel_plan",
        "status": status,
        "constraints": constraints,
        "candidates": [],
        "unverified_candidates": names,
        "itinerary": {"days": []},
        "map": {},
        "raw_text": raw_text,
        "recommendations": [{"type": "travel_candidates", "title": title, "reason": reason}],
        "followups": ["补充地点名称", "重新上传更清晰的攻略文字截图"],
        "warnings": warnings,
    }


async def _finalize_itinerary(redis_client: redis.Redis, state: ChatState) -> dict[str, Any]:
    payload = _travel_payload(state)
    cached = await _load_state(redis_client, state.session_id)
    constraints = _extract_constraints(state.message or "", payload.get("constraints") or cached.get("constraints"))
    candidates = _normalize_payload_candidates(payload.get("candidates")) or cached.get("candidates") or []
    candidates = [item for item in candidates if isinstance(item, dict) and _is_valid_poi(item)]
    await _cache_session_pois(redis_client, state.session_id, candidates)
    days = _build_itinerary_days(candidates, constraints)
    line_list = _build_map_lines(days)
    map_payload = await travel_create_personal_map(
        {
            "title": _itinerary_title(constraints),
            "line_list": line_list,
            "servers_path": settings.MCP_SERVERS_CONFIG_PATH,
        }
    ) if line_list else {"error": "missing_line_list"}
    final = {
        "type": "travel_plan",
        "status": "completed",
        "constraints": constraints,
        "candidates": candidates,
        "itinerary": {"days": days},
        "map": {
            "qr_code_url": map_payload.get("qr_code_url") if isinstance(map_payload, dict) else None,
            "schema_url": map_payload.get("schema_url") if isinstance(map_payload, dict) else None,
            "raw": map_payload,
        },
        "raw_text": _itinerary_text(days),
        "recommendations": [
            {
                "type": "travel_plan",
                "title": f"{_itinerary_title(constraints)}已生成，地图已完成",
                "reason": "已基于确认 POI 生成每日路线，并调用高德个人地图。",
            }
        ],
        "followups": ["查看高德二维码", "继续调整某一天路线"],
        "warnings": [] if candidates else ["没有可用于生成地图的已验证 POI。"],
    }
    await _save_state(redis_client, state.session_id, {"stage": "completed", **final})
    return final


def _travel_action(state: ChatState) -> str:
    overrides = state.context_overrides if isinstance(state.context_overrides, dict) else {}
    return str(overrides.get("travel_action") or "").strip()


def _travel_payload(state: ChatState) -> dict[str, Any]:
    overrides = state.context_overrides if isinstance(state.context_overrides, dict) else {}
    payload = overrides.get("travel_payload")
    return payload if isinstance(payload, dict) else {}


def _has_attachments(state: ChatState) -> bool:
    overrides = state.context_overrides if isinstance(state.context_overrides, dict) else {}
    attachments = overrides.get("attachments")
    return isinstance(attachments, list) and bool(attachments)


async def _extract_names_from_attachments(state: ChatState) -> list[str]:
    if not settings.LLM_VISION_ENABLED:
        return []
    try:
        from app.agent.llm_adapters import ProviderRegistry, build_planner
        from app.agent.vision import build_vision_content_parts
        from app.infra.minio import get_minio

        planner_config = (
            ProviderRegistry.from_resolved_config(state.resolved_model_config)
            if isinstance(state.resolved_model_config, dict) and state.resolved_model_config.get("source") == "user_config"
            else None
        )
        planner = build_planner(provider=state.provider, config=planner_config)
        image_parts = await build_vision_content_parts(
            (state.context_overrides or {}).get("attachments") if isinstance(state.context_overrides, dict) else None,
            minio=await get_minio(),
        )
        if not image_parts:
            return []
        result = await planner.plan_tool_calls(
            "你是旅行攻略图片 OCR 和地点抽取器。只从图片中提取真实出现的景点、餐厅、酒店、商圈、车站等地点名，不要编造。",
            "请返回 JSON：{\"places\":[\"地点1\",\"地点2\"]}。只返回 JSON，不要解释。",
            [],
            image_parts=image_parts,
        )
        content = str(result.get("content") or "").strip()
        names = _extract_place_names_from_text(content)
        if names:
            return names
        return _extract_place_names_from_tool_calls(result.get("tool_calls"))
    except Exception as exc:
        logger.info("travel_vision_extract_failed session_id=%s reason=%s", state.session_id, str(exc))
        return []


def _extract_place_names_from_text(content: str) -> list[str]:
    if not content:
        return []
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        payload = None
    places: list[str] = []
    if isinstance(payload, dict) and isinstance(payload.get("places"), list):
        places = [str(item).strip() for item in payload["places"]]
    elif isinstance(payload, list):
        places = [str(item).strip() for item in payload]
    else:
        for item in re.split(r"[\n，,。；;、\[\]\"]", cleaned):
            text = item.strip()
            if 2 <= len(text) <= 20 and text not in {"places", "地点"}:
                places.append(text)
    return list(dict.fromkeys(item for item in places if item))[:16]


def _extract_place_names_from_tool_calls(tool_calls: Any) -> list[str]:
    if not isinstance(tool_calls, list):
        return []
    texts: list[str] = []
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        args = call.get("args")
        if isinstance(args, dict):
            texts.extend(_collect_strings(args))
    names: list[str] = []
    for text in texts:
        names.extend(_extract_place_names_from_text(text))
    return list(dict.fromkeys(names))[:16]


def _collect_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        rows: list[str] = []
        for item in value:
            rows.extend(_collect_strings(item))
        return rows
    if isinstance(value, dict):
        rows: list[str] = []
        for item in value.values():
            rows.extend(_collect_strings(item))
        return rows
    return []


def _extract_constraints(message: str, payload: Any = None) -> dict[str, Any]:
    constraints = dict(payload) if isinstance(payload, dict) else {}
    destination = constraints.get("destination")
    if not destination:
        match = re.search(r"目的地[:：]\s*([^\n，,。；;]+)", message)
        if not match:
            match = re.search(r"(?:去|到)([\u4e00-\u9fa5A-Za-z·]{2,12})(?:旅|玩|游|[0-9一二三四五六七八九十]+天)", message)
        destination = match.group(1).strip() if match else ""
    days = constraints.get("travelDays") or constraints.get("days")
    if not days:
        match = re.search(r"(\d+)\s*[天日]", message)
        days = int(match.group(1)) if match else 1
    try:
        days = max(1, min(int(str(days).rstrip("天日")), 30))
    except (TypeError, ValueError):
        days = 1
    return {**constraints, "destination": destination or "目的地待定", "days": days}


def _candidate_names(message: str, payload: Any, constraints: dict[str, Any]) -> list[str]:
    names: list[str] = []
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                names.append(str(item.get("name") or item.get("source_name") or ""))
    if not names:
        for line in re.split(r"[\n，,。；;、]", message):
            text = line.strip()
            if 2 <= len(text) <= 14 and not any(token in text for token in ("请", "帮我", "旅行", "旅游", "规划", "目的地", "出行", "人数", "候选", "生成", "确认")):
                names.append(text)
    destination = str(constraints.get("destination") or "").strip()
    if destination and destination != "目的地待定":
        names.insert(0, destination)
    return list(dict.fromkeys(item.strip() for item in names if item.strip()))[:16]


def _normalize_payload_candidates(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        if "longitude" not in row and "lon" in row:
            row["longitude"] = row.get("lon")
        if "latitude" not in row and "lat" in row:
            row["latitude"] = row.get("lat")
        rows.append(row)
    return rows


def _first_valid_poi(pois: Any) -> dict[str, Any] | None:
    if not isinstance(pois, list):
        return None
    for item in pois:
        if isinstance(item, dict) and _is_valid_poi(item):
            return item
    return None


def _is_valid_poi(item: dict[str, Any]) -> bool:
    return bool(item.get("poi_id") and item.get("name") and item.get("longitude") is not None and item.get("latitude") is not None)


def _build_itinerary_days(candidates: list[dict[str, Any]], constraints: dict[str, Any]) -> list[dict[str, Any]]:
    day_count = int(constraints.get("days") or 1)
    days = [{"day": index + 1, "title": f"Day {index + 1}", "route": []} for index in range(day_count)]
    for index, poi in enumerate(candidates):
        day = days[index % day_count]
        day["route"].append(
            {
                "time": _time_bucket(len(day["route"])),
                "title": poi.get("name"),
                "location": poi.get("name"),
                "address": poi.get("address"),
                "poi_id": poi.get("poi_id"),
                "longitude": poi.get("longitude"),
                "latitude": poi.get("latitude"),
                "note": "已验证 POI",
            }
        )
    return days


def _time_bucket(index: int) -> str:
    return ["09:30", "11:30", "14:30", "17:30", "19:30"][min(index, 4)]


def _build_map_lines(days: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for day in days:
        points = []
        for item in day.get("route") or []:
            if not isinstance(item, dict) or item.get("longitude") is None or item.get("latitude") is None:
                continue
            points.append(
                {
                    "name": item.get("location") or item.get("title"),
                    "lon": item.get("longitude"),
                    "lat": item.get("latitude"),
                    "poiId": item.get("poi_id"),
                }
            )
        if points:
            lines.append({"title": day.get("title") or f"Day {day.get('day')}", "pointInfoList": points[:16]})
    return lines


def _itinerary_title(constraints: dict[str, Any]) -> str:
    return f"{constraints.get('destination') or '旅行'} {constraints.get('days') or 1} 日游"


def _candidate_summary(candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return "暂未验证到候选地点。"
    return "候选地点：\n" + "\n".join(f"- {item.get('name')}（{item.get('address') or '地址待确认'}）" for item in candidates)


def _itinerary_text(days: list[dict[str, Any]]) -> str:
    chunks = []
    for day in days:
        route = " -> ".join(str(item.get("location") or item.get("title")) for item in day.get("route") or [])
        chunks.append(f"Day{day.get('day')}: {route or '自由安排'}")
    return "\n".join(chunks)


def _render_travel_text(final_json: dict[str, Any]) -> str:
    if final_json.get("status") == "await_confirmation":
        return f"{final_json['recommendations'][0]['title']}\n\n{final_json.get('raw_text')}\n\n确认后我会生成每日行程和高德地图。"
    if final_json.get("status") != "completed":
        return f"{final_json['recommendations'][0]['title']}\n\n{final_json.get('raw_text')}\n\n补充地点后我会继续验证 POI。"
    return f"{final_json['recommendations'][0]['title']}\n\n{final_json.get('raw_text')}\n\n高德二维码：{(final_json.get('map') or {}).get('qr_code_url') or '暂无'}"


async def _save_state(redis_client: redis.Redis, session_id: str, payload: dict[str, Any]) -> None:
    await redis_client.setex(f"travel:state:{session_id}", _STATE_TTL_SECONDS, json.dumps(payload, ensure_ascii=True))


async def _load_state(redis_client: redis.Redis, session_id: str) -> dict[str, Any]:
    raw = await redis_client.get(f"travel:state:{session_id}")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


async def _cache_session_pois(redis_client: redis.Redis, session_id: str, pois: list[dict[str, Any]]) -> None:
    await redis_client.setex(f"travel:pois:{session_id}", _STATE_TTL_SECONDS, json.dumps(pois, ensure_ascii=True))


async def _should_stop(request: Any, redis_client: redis.Redis, session_id: str) -> bool:
    if request is not None and hasattr(request, "is_disconnected") and await request.is_disconnected():
        return True
    return bool(await redis_client.get(f"chat:cancel:{session_id}"))


def _action_message(action: str) -> str:
    return "确认候选地点并生成旅行计划" if action else "创建旅行计划"
