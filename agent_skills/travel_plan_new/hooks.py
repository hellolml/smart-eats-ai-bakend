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
        trip_meta = _trip_meta(state, context, overrides)
        phase = _phase(action=action, candidates=candidates, map_payload=map_payload)

        return {
            "intent": "travel",
            "travel_state": {
                "skill_id": "travel_plan_new",
                "phase": phase,
                "await_confirmation": phase == "candidates_ready",
                "travel_action": action,
                "trip_meta": trip_meta,
                "candidates": candidates,
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


def _context_overrides(state: Any) -> dict[str, Any]:
    overrides = getattr(state, "context_overrides", None)
    return overrides if isinstance(overrides, dict) else {}


def _travel_action(overrides: dict[str, Any]) -> str:
    value = overrides.get("travel_action")
    return str(value or "").strip()


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


def _phase(*, action: str, candidates: list[dict[str, Any]], map_payload: dict[str, Any] | None) -> str:
    if map_payload and (map_payload.get("qr_code_url") or map_payload.get("schema_url")):
        return "map_generated"
    if action == "confirm_candidates":
        return "candidates_confirmed"
    if candidates:
        return "candidates_ready"
    return "ingesting_content"


def _system_directive(*, phase: str, action: str, candidate_count: int, has_attachments: bool) -> str:
    attachment_note = "当前有图片附件，必须先从图片中识别地点。" if has_attachments else "当前没有图片附件，优先使用用户文本中的攻略原文或 URL。"
    if phase == "candidates_confirmed":
        return (
            "旅行规划候选已确认。现在必须生成结构化 itinerary.days，然后调用 travel_create_personal_map "
            "生成高德二维码和 schema。不要再返回候选确认卡片。"
        )
    if phase == "candidates_ready":
        return (
            f"已验证 {candidate_count} 个候选 POI。必须停在候选确认阶段，展示候选并等待用户确认；"
            "用户确认前禁止生成最终每日行程或高德地图。"
        )
    return (
        "旅行规划必须按 created -> ingesting_content -> places_extracted -> candidates_ready -> "
        "candidates_confirmed -> itinerary_generated -> map_generated 执行。"
        f"{attachment_note} 先提取地点，再用 travel_search_poi 验证 POI。"
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
    return {
        "state": "candidates_ready",
        "await_confirmation": True,
        "trip_meta": _trip_meta(state, {}, _context_overrides(state)),
        "sources": _sources(state),
        "places": [{"name": item.get("name"), "category": item.get("category")} for item in candidates],
        "candidates": candidates,
        "itinerary": {"days": []},
        "map": {"qr_code_url": None, "schema_url": None},
        "raw_text": str(getattr(state, "message", "") or ""),
        "recommendations": [
            {
                "title": f"已验证 {len(candidates)} 个候选地点",
                "reason": "请先确认、删除或补充候选地点，确认后我再生成每日行程和高德地图。",
            }
        ],
        "followups": ["确认候选地点后，将继续生成最终行程和高德二维码。"],
        "warnings": [],
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
