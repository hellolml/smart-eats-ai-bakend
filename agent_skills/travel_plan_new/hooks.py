from __future__ import annotations

from typing import Any

from app.agent.runtime.hooks import BaseSkillHooks


class TravelPlanNewHooks(BaseSkillHooks):
    def build_context(
        self,
        state: Any,
        context: dict[str, Any],
        runtime: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        overrides = _context_overrides(state)
        action = _travel_action(overrides)
        candidates = _collect_verified_candidates(state)
        map_payload = _latest_map_payload(state)
        itinerary = _payload_itinerary(overrides)
        trip_meta = _trip_meta(state, context, overrides)
        phase = _phase(action=action, candidates=candidates, itinerary=itinerary, map_payload=map_payload)

        return {
            "intent": "travel",
            "travel_state": {
                "skill_id": "travel_plan_new",
                "phase": phase,
                "await_confirmation": phase in {"candidates_ready", "itinerary_generated"},
                "travel_action": action,
                "trip_meta": trip_meta,
                "candidates": candidates,
                "failed_places": _collect_failed_places(state),
                "itinerary": itinerary,
                "map": _map_summary(map_payload),
                "input_priority": ["raw_texts", "images", "urls"],
            },
            "system_directive": _system_directive(
                phase=phase,
                action=action,
                candidate_count=len(candidates),
                has_attachments=_has_attachments(context),
            ),
        }

    def normalize_tool_args(self, state: Any, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(args)
        if tool_name == "travel_search_poi":
            destination = _trip_meta(state, {}, _context_overrides(state)).get("destination")
            if destination and not normalized.get("city"):
                normalized["city"] = destination
            normalized.setdefault("page_size", 5)
        if tool_name == "travel_create_personal_map":
            overrides = _context_overrides(state)
            trip_meta = _trip_meta(state, {}, overrides)
            normalized.setdefault("title", _map_title(trip_meta))
            if not isinstance(normalized.get("line_list"), list) or not normalized.get("line_list"):
                normalized["line_list"] = _line_list_from_payload(overrides)
            normalized.setdefault("scene_type", 1)
        return normalized

    def preview_tool_result(self, state: Any, tool_name: str, result: Any) -> Any | None:
        if tool_name == "travel_search_poi" and isinstance(result, dict):
            pois = _valid_pois(result)
            return {
                "status": "poi_verified" if pois else "poi_not_found",
                "query": result.get("query"),
                "count": len(pois),
                "names": [str(item.get("name")) for item in pois[:5] if item.get("name")],
            }
        if tool_name == "travel_create_personal_map" and isinstance(result, dict):
            return {
                "status": "map_generated" if result.get("qr_code_url") or result.get("schema_url") else "map_unavailable",
                "title": result.get("title"),
                "qr_code_url": result.get("qr_code_url"),
                "schema_url": result.get("schema_url"),
            }
        return None

    def handle_tool_result(self, state: Any, tool_name: str, result: Any) -> dict[str, Any] | None:
        overrides = _context_overrides(state)
        action = _travel_action(overrides)
        if tool_name == "travel_search_poi" and action != "confirm_candidates":
            candidates = _collect_verified_candidates(state)
            if not candidates:
                return _no_places_final(state, result)
            return _candidate_confirmation_final(state, candidates)

        if tool_name == "travel_create_personal_map" and isinstance(result, dict):
            return _map_final(state, result)

        return None

    def best_effort_fallback(self, state: Any) -> dict[str, Any] | None:
        if getattr(state, "scene", "") != "travel_planner":
            return None
        candidates = _collect_verified_candidates(state)
        if candidates:
            return _candidate_confirmation_final(state, candidates)
        return {
            "state": "created",
            "await_confirmation": False,
            "trip_meta": _trip_meta(state, {}, _context_overrides(state)),
            "sources": _sources(state),
            "places": [],
            "candidates": [],
            "itinerary": {"days": []},
            "map": {"qr_code_url": None, "schema_url": None},
            "raw_text": str(getattr(state, "message", "") or ""),
            "recommendations": [
                {
                    "title": "旅行规划还需要更多信息",
                    "reason": "请补充目的地、出行天数，并上传清晰攻略截图或粘贴攻略原文。",
                }
            ],
            "followups": ["补充攻略内容后，我会先提取并验证候选地点。"],
            "warnings": [],
        }

    def should_build_vision_input(self, state: Any) -> bool:
        context = getattr(state, "context", None)
        return _has_attachments(context if isinstance(context, dict) else {})

    def forced_tool_calls(self, state: Any) -> list[dict[str, Any]] | None:
        overrides = _context_overrides(state)
        action = _travel_action(overrides)
        if not _is_map_action(action):
            return None
        line_list = _line_list_from_payload(overrides)
        if not line_list:
            return None
        return [
            {
                "name": "travel_create_personal_map",
                "args": {
                    "title": _map_title(_trip_meta(state, {}, overrides)),
                    "line_list": line_list,
                    "scene_type": 1,
                },
                "type": "tool_call",
            }
        ]


def _context_overrides(state: Any) -> dict[str, Any]:
    overrides = getattr(state, "context_overrides", None)
    return overrides if isinstance(overrides, dict) else {}


def _travel_action(overrides: dict[str, Any]) -> str:
    value = overrides.get("travel_action")
    return str(value or "").strip()


def _is_map_action(action: str) -> bool:
    return action in {"confirm_itinerary", "generate_map", "confirm_plan"}


def _has_attachments(context: dict[str, Any]) -> bool:
    attachments = context.get("attachments")
    return isinstance(attachments, list) and bool(attachments)


def _trip_meta(state: Any, context: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    payload = overrides.get("travel_payload")
    payload = payload if isinstance(payload, dict) else {}
    meta = payload.get("trip_meta") if isinstance(payload.get("trip_meta"), dict) else {}
    basic = payload.get("basic_info") if isinstance(payload.get("basic_info"), dict) else {}
    return {
        "destination": meta.get("destination") or basic.get("destination") or payload.get("destination"),
        "start_date": meta.get("start_date") or basic.get("start_date") or payload.get("start_date"),
        "end_date": meta.get("end_date") or basic.get("end_date") or payload.get("end_date"),
        "days": meta.get("days") or basic.get("days") or payload.get("days"),
        "travelers_count": meta.get("travelers_count") or basic.get("travelers_count") or payload.get("travelers_count"),
        "preferences": meta.get("preferences") or basic.get("preferences") or payload.get("preferences") or [],
        "budget": meta.get("budget") or basic.get("budget") or payload.get("budget"),
        "start_point": meta.get("start_point") or payload.get("start_point"),
        "end_point": meta.get("end_point") or payload.get("end_point"),
    }


def _phase(
    *,
    action: str,
    candidates: list[dict[str, Any]],
    itinerary: dict[str, Any],
    map_payload: dict[str, Any] | None,
) -> str:
    if map_payload and (map_payload.get("qr_code_url") or map_payload.get("schema_url")):
        return "map_generated"
    if _is_map_action(action):
        return "itinerary_generated"
    if action == "confirm_candidates":
        return "candidates_confirmed"
    if isinstance(itinerary.get("days"), list) and itinerary.get("days"):
        return "itinerary_generated"
    if candidates:
        return "candidates_ready"
    return "ingesting_content"


def _system_directive(*, phase: str, action: str, candidate_count: int, has_attachments: bool) -> str:
    attachment_note = "当前有图片附件，必须先从图片中识别地点。" if has_attachments else "当前没有图片附件，优先使用用户文本中的攻略原文或 URL。"
    if phase == "candidates_confirmed":
        return (
            "旅行规划候选已确认。现在只生成结构化 itinerary.days，并通过 submit_final_answer 返回 "
            "state=itinerary_generated、await_confirmation=true、trip_meta、candidates、itinerary.days、"
            "map={qr_code_url:null,schema_url:null}、raw_text。"
            "本阶段禁止调用 travel_create_personal_map；必须引导用户确认行程后再生成高德地图二维码。"
        )
    if phase == "itinerary_generated" and _is_map_action(action):
        return (
            "用户已确认最终行程。现在必须调用 travel_create_personal_map 生成高德二维码和 schema。"
            "只允许使用已验证 POI 构造 line_list；未验证地点不得进入地图点位。"
            "地图生成后返回 state=map_generated，并包含 itinerary.days、map.qr_code_url、map.schema_url。"
        )
    if phase == "itinerary_generated":
        return (
            "已生成每日行程。必须停在行程确认阶段，等待用户确认是否生成高德地图二维码；"
            "用户确认前禁止调用 travel_create_personal_map。"
        )
    if phase == "candidates_ready":
        return (
            f"已验证 {candidate_count} 个候选 POI。必须停在候选确认阶段，展示候选并等待用户确认；"
            "同时展示验证失败的地点和原因，引导用户增删地点；用户确认前禁止生成最终每日行程或高德地图。"
        )
    return (
        "旅行规划必须按 created -> ingesting_content -> places_extracted -> candidates_ready -> "
        "candidates_confirmed -> itinerary_generated -> map_generated 执行。"
        f"{attachment_note} 先提取地点，再用 travel_search_poi 验证 POI。"
        "如果用户上传了图片，你必须逐一识别图片中的景点、餐厅、酒店、商圈、车站等地点；"
        "图片清晰可读时禁止直接说无法识别图片；确实无法识别时先说明图片中可见内容并请求用户补充原文。"
    )


def _sources(state: Any) -> list[dict[str, Any]]:
    overrides = _context_overrides(state)
    attachments = overrides.get("attachments")
    sources: list[dict[str, Any]] = []
    if isinstance(attachments, list):
        for item in attachments:
            if isinstance(item, dict):
                sources.append(
                    {
                        "type": "image",
                        "filename": item.get("filename"),
                        "content_type": item.get("content_type"),
                        "parse_status": "image_received",
                    }
                )
    return sources


def _valid_pois(result: dict[str, Any]) -> list[dict[str, Any]]:
    pois = result.get("pois")
    if not isinstance(pois, list):
        return []
    valid: list[dict[str, Any]] = []
    for item in pois:
        if not isinstance(item, dict):
            continue
        if item.get("poi_id") and item.get("name") and item.get("longitude") is not None and item.get("latitude") is not None:
            valid.append(item)
    return valid


def _collect_verified_candidates(state: Any) -> list[dict[str, Any]]:
    payload_candidates = _payload_candidates(_context_overrides(state))
    observations = getattr(state, "observations", None)
    candidates: list[dict[str, Any]] = list(payload_candidates)
    seen: set[str] = {
        str(
            (((item.get("poi") or {}) if isinstance(item.get("poi"), dict) else {}).get("poi_id"))
            or item.get("poi_id")
            or item.get("name")
        )
        for item in candidates
        if isinstance(item, dict)
    }
    if not isinstance(observations, list):
        return candidates
    for observation in observations:
        if not isinstance(observation, dict) or observation.get("tool") != "travel_search_poi":
            continue
        result = observation.get("result")
        if not isinstance(result, dict):
            continue
        query = result.get("query") if isinstance(result.get("query"), dict) else {}
        for poi in _valid_pois(result):
            key = str(poi.get("poi_id") or poi.get("name"))
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "candidate_id": f"candidate_{len(candidates) + 1:03d}",
                    "name": poi.get("name"),
                    "category": _category_from_query(query),
                    "source": "poi_verified",
                    "score": 8,
                    "reason": "已通过高德 POI 验证",
                    "poi": {
                        "poi_id": poi.get("poi_id"),
                        "name": poi.get("name"),
                        "address": poi.get("address"),
                        "longitude": poi.get("longitude"),
                        "latitude": poi.get("latitude"),
                    },
                }
            )
    return candidates


def _payload_candidates(overrides: dict[str, Any]) -> list[dict[str, Any]]:
    payload = overrides.get("travel_payload")
    if not isinstance(payload, dict):
        return []
    raw_candidates = payload.get("candidates") or payload.get("confirmed_candidates")
    if not isinstance(raw_candidates, list):
        return []
    candidates: list[dict[str, Any]] = []
    for item in raw_candidates:
        if not isinstance(item, dict):
            continue
        poi = item.get("poi") if isinstance(item.get("poi"), dict) else item
        name = item.get("name") or poi.get("name")
        poi_id = item.get("poi_id") or poi.get("poi_id")
        longitude = item.get("longitude") if item.get("longitude") is not None else poi.get("longitude")
        latitude = item.get("latitude") if item.get("latitude") is not None else poi.get("latitude")
        if not name:
            continue
        candidates.append(
            {
                "candidate_id": item.get("candidate_id") or f"candidate_{len(candidates) + 1:03d}",
                "name": name,
                "category": item.get("category") or "attraction",
                "source": item.get("source") or "user_confirmed",
                "score": item.get("score") or 9,
                "reason": item.get("reason") or "用户已确认候选地点",
                "poi": {
                    "poi_id": poi_id,
                    "name": name,
                    "address": item.get("address") or poi.get("address"),
                    "longitude": longitude,
                    "latitude": latitude,
                },
            }
        )
    return candidates


def _payload_itinerary(overrides: dict[str, Any]) -> dict[str, Any]:
    payload = overrides.get("travel_payload")
    if not isinstance(payload, dict):
        return {"days": []}
    itinerary = payload.get("itinerary")
    if isinstance(itinerary, dict):
        days = itinerary.get("days")
        return {"days": days if isinstance(days, list) else []}
    days = payload.get("days")
    if isinstance(days, list):
        return {"days": days}
    return {"days": []}


def _collect_failed_places(state: Any) -> list[dict[str, Any]]:
    observations = getattr(state, "observations", None)
    if not isinstance(observations, list):
        return []
    failed: list[dict[str, Any]] = []
    seen: set[str] = set()
    for observation in observations:
        if not isinstance(observation, dict) or observation.get("tool") != "travel_search_poi":
            continue
        result = observation.get("result")
        if not isinstance(result, dict):
            continue
        if _valid_pois(result):
            continue
        query = result.get("query") if isinstance(result.get("query"), dict) else {}
        name = str(query.get("keywords") or result.get("keywords") or result.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        failed.append(
            {
                "name": name,
                "city": query.get("city"),
                "reason": result.get("error") or "未在高德 POI 中验证到有效坐标",
            }
        )
    return failed


def _category_from_query(query: dict[str, Any]) -> str:
    text = " ".join(str(query.get(key) or "") for key in ("keywords", "types")).lower()
    if any(token in text for token in ("餐", "饭", "美食", "咖啡", "restaurant", "food", "cafe")):
        return "restaurant"
    if any(token in text for token in ("酒店", "住宿", "hotel")):
        return "hotel"
    return "attraction"


def _latest_map_payload(state: Any) -> dict[str, Any] | None:
    observations = getattr(state, "observations", None)
    if not isinstance(observations, list):
        return None
    for observation in reversed(observations):
        if isinstance(observation, dict) and observation.get("tool") == "travel_create_personal_map":
            result = observation.get("result")
            return result if isinstance(result, dict) else None
    return None


def _map_summary(result: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"qr_code_url": None, "schema_url": None}
    return {
        "qr_code_url": result.get("qr_code_url"),
        "schema_url": result.get("schema_url"),
        "title": result.get("title"),
    }


def _candidate_confirmation_final(state: Any, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    failed_places = _collect_failed_places(state)
    followups = ["确认候选地点后，将继续生成最终每日行程。"]
    if failed_places:
        followups.insert(0, "部分地点未验证通过，你可以补充准确名称、删除或改成附近地标。")
    return {
        "state": "candidates_ready",
        "await_confirmation": True,
        "trip_meta": _trip_meta(state, {}, _context_overrides(state)),
        "sources": _sources(state),
        "places": [{"name": item.get("name"), "category": item.get("category")} for item in candidates],
        "candidates": candidates,
        "failed_places": failed_places,
        "itinerary": {"days": []},
        "map": {"qr_code_url": None, "schema_url": None},
        "raw_text": str(getattr(state, "message", "") or ""),
        "recommendations": [
            {
                "title": f"已验证 {len(candidates)} 个候选地点",
                "reason": "请先确认、删除或补充候选地点，确认后我再生成每日行程。",
            }
        ],
        "followups": followups,
        "warnings": [f"{item.get('name')}：{item.get('reason')}" for item in failed_places],
    }


def _no_places_final(state: Any, result: Any) -> dict[str, Any]:
    warnings = ["未能从当前攻略内容中验证出有效 POI，请补充更清晰截图或粘贴攻略原文。"]
    if isinstance(result, dict) and result.get("error"):
        warnings.append(str(result.get("error")))
    return {
        "state": "places_extracted",
        "await_confirmation": False,
        "trip_meta": _trip_meta(state, {}, _context_overrides(state)),
        "sources": _sources(state),
        "places": [],
        "candidates": [],
        "itinerary": {"days": []},
        "map": {"qr_code_url": None, "schema_url": None},
        "raw_text": str(getattr(state, "message", "") or ""),
        "recommendations": [
            {
                "title": "暂未识别到可用地点",
                "reason": "我没有拿到可用于高德验证的地点名称。",
            }
        ],
        "followups": ["请上传更清晰的攻略图片，或直接粘贴攻略原文中的地点列表。"],
        "warnings": warnings,
    }


def _map_final(state: Any, result: dict[str, Any]) -> dict[str, Any]:
    line_list = result.get("line_list") if isinstance(result.get("line_list"), list) else []
    days = _days_from_line_list(line_list)
    overrides = _context_overrides(state)
    payload_days = _payload_itinerary(overrides).get("days")
    if not days and isinstance(payload_days, list):
        days = payload_days
    return {
        "state": "map_generated",
        "await_confirmation": False,
        "trip_meta": _trip_meta(state, {}, _context_overrides(state)),
        "sources": _sources(state),
        "places": [],
        "candidates": _collect_verified_candidates(state),
        "itinerary": {"days": days},
        "map": {
            "qr_code_url": result.get("qr_code_url"),
            "schema_url": result.get("schema_url"),
            "title": result.get("title"),
            "line_list": line_list,
        },
        "raw_text": str(getattr(state, "message", "") or ""),
        "recommendations": [
            {
                "title": result.get("title") or "旅行计划已生成",
                "reason": "已完成每日行程并生成高德个人地图。",
            }
        ],
        "followups": ["你可以继续提出调整要求，我会在当前计划基础上修改。"],
        "warnings": [] if result.get("qr_code_url") or result.get("schema_url") else ["高德地图二维码暂不可用，请稍后重试。"],
    }


def _map_title(trip_meta: dict[str, Any]) -> str:
    destination = str(trip_meta.get("destination") or "旅行").strip()
    days = str(trip_meta.get("days") or "").strip()
    return f"{destination}{days}日游地图" if days else f"{destination}旅行地图"


def _line_list_from_payload(overrides: dict[str, Any]) -> list[dict[str, Any]]:
    payload = overrides.get("travel_payload")
    payload = payload if isinstance(payload, dict) else {}
    raw_line_list = payload.get("line_list")
    if isinstance(raw_line_list, list) and raw_line_list:
        return [item for item in raw_line_list if isinstance(item, dict)]

    itinerary_days = _payload_itinerary(overrides).get("days")
    candidates = _payload_candidates(overrides)
    if not isinstance(itinerary_days, list) or not itinerary_days:
        itinerary_days = _rough_days_from_candidates(candidates, _trip_meta_from_payload(payload))

    candidate_by_name = {str(item.get("name") or ""): item for item in candidates if isinstance(item, dict)}
    line_list: list[dict[str, Any]] = []
    for index, day in enumerate(itinerary_days, start=1):
        if not isinstance(day, dict):
            continue
        raw_items = day.get("items")
        if not isinstance(raw_items, list):
            raw_items = []
        points: list[dict[str, Any]] = []
        for item in raw_items:
            item_name = item.get("place_name") or item.get("name") or item.get("title") if isinstance(item, dict) else item
            name = str(item_name or "").strip()
            if not name:
                continue
            candidate = candidate_by_name.get(name)
            if not candidate:
                candidate = next(
                    (
                        item
                        for candidate_name, item in candidate_by_name.items()
                        if candidate_name and (candidate_name in name or name in candidate_name)
                    ),
                    None,
                )
            poi = candidate.get("poi") if isinstance(candidate, dict) and isinstance(candidate.get("poi"), dict) else {}
            if not poi and isinstance(item, dict):
                poi = item
            longitude = poi.get("longitude") or poi.get("lon")
            latitude = poi.get("latitude") or poi.get("lat")
            if longitude is None or latitude is None:
                continue
            points.append(
                {
                    "name": name,
                    "poiId": poi.get("poi_id") or poi.get("poiId"),
                    "lon": longitude,
                    "lat": latitude,
                }
            )
        if points:
            line_list.append(
                {
                    "title": day.get("theme") or day.get("day") or f"Day {index}",
                    "pointInfoList": points[:16],
                }
            )
    return line_list


def _trip_meta_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    meta = payload.get("trip_meta") if isinstance(payload.get("trip_meta"), dict) else {}
    basic = payload.get("basic_info") if isinstance(payload.get("basic_info"), dict) else {}
    return {
        "destination": meta.get("destination") or basic.get("destination") or payload.get("destination"),
        "days": meta.get("days") or basic.get("days") or payload.get("days"),
    }


def _rough_days_from_candidates(candidates: list[dict[str, Any]], trip_meta: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        day_count = int(trip_meta.get("days") or 1)
    except (TypeError, ValueError):
        day_count = 1
    day_count = max(1, min(day_count, 15))
    days: list[dict[str, Any]] = []
    for index in range(day_count):
        items = candidates[index::day_count] or candidates[:1]
        days.append(
            {
                "day_number": index + 1,
                "theme": f"Day {index + 1}",
                "items": [
                    {
                        "place_name": item.get("name"),
                        "name": item.get("name"),
                        **(item.get("poi") if isinstance(item.get("poi"), dict) else {}),
                    }
                    for item in items
                ],
            }
        )
    return days


def _days_from_line_list(line_list: list[Any]) -> list[dict[str, Any]]:
    days: list[dict[str, Any]] = []
    for index, line in enumerate(line_list, start=1):
        if not isinstance(line, dict):
            continue
        points = line.get("pointInfoList")
        if not isinstance(points, list):
            points = line.get("points") if isinstance(line.get("points"), list) else []
        items: list[dict[str, Any]] = []
        for order, point in enumerate(points, start=1):
            if not isinstance(point, dict):
                continue
            items.append(
                {
                    "order": order,
                    "place_name": point.get("name"),
                    "poi_id": point.get("poiId") or point.get("poi_id"),
                    "longitude": point.get("lon") or point.get("longitude"),
                    "latitude": point.get("lat") or point.get("latitude"),
                }
            )
        days.append(
            {
                "day_number": index,
                "theme": line.get("title") or f"Day {index}",
                "items": items,
            }
        )
    return days
