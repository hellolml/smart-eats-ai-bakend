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
        excluded_places = _payload_excluded_places(overrides)
        user_added_places = _payload_user_added_places(overrides)
        phase = _phase(action=action, candidates=candidates, itinerary=itinerary, map_payload=map_payload, trip_meta=trip_meta)

        return {
            "intent": "travel",
            "travel_state": {
                "skill_id": "travel_plan_new",
                "phase": phase,
                "await_confirmation": phase in {"candidates_ready", "itinerary_generated"},
                "travel_action": action,
                "trip_meta": trip_meta,
                "candidates": candidates,
                "excluded_places": excluded_places,
                "user_added_places": user_added_places,
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
                has_url_failures=bool(_url_failures(state)),
                generic_food_additions=_generic_food_additions(user_added_places),
            ),
        }

    def normalize_tool_args(self, state: Any, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(args)
        if tool_name == "travel_search_poi":
            destination = _trip_meta(state, {}, _context_overrides(state)).get("destination")
            if destination and not normalized.get("city"):
                normalized["city"] = destination
            normalized.setdefault("page_size", 5)
        if tool_name == "travel_search_nearby_poi":
            normalized.setdefault("page_size", 5)
            normalized.setdefault("radius", 1500)
            if not normalized.get("location") and not (
                normalized.get("longitude") is not None and normalized.get("latitude") is not None
            ):
                poi = _first_verified_poi(state)
                if poi:
                    normalized["longitude"] = poi.get("longitude")
                    normalized["latitude"] = poi.get("latitude")
        if tool_name == "travel_fetch_url_content":
            normalized.setdefault("timeout_seconds", 8)
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
        if tool_name == "travel_search_nearby_poi" and isinstance(result, dict):
            pois = _valid_pois(result)
            return {
                "status": "nearby_poi_found" if pois else "nearby_poi_not_found",
                "query": result.get("query"),
                "count": len(pois),
                "names": [str(item.get("name")) for item in pois[:5] if item.get("name")],
            }
        if tool_name == "travel_fetch_url_content" and isinstance(result, dict):
            text = str(result.get("text") or "")
            return {
                "status": result.get("parse_status") or "failed",
                "url": result.get("url"),
                "title": result.get("title"),
                "text_chars": len(text),
                "error": result.get("error"),
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
        if tool_name == "submit_final_answer":
            return _handle_submit_final_answer(state, action, result)

        if tool_name in {"travel_search_poi", "travel_search_nearby_poi"}:
            if action == "confirm_candidates":
                # 用户已确认候选，不再继续搜索 POI，直接生成行程
                candidates = _collect_verified_candidates(state)
                if candidates:
                    return _submit_final_for_confirmed(state, action, candidates)
                return _no_places_final(state, result)
            # 正常流程：收集候选并展示给用户确认
            candidates = _collect_verified_candidates(state)
            if not candidates:
                return _no_places_final(state, result)
            return _candidate_confirmation_final(state, candidates)

        if tool_name == "travel_fetch_url_content" and isinstance(result, dict) and result.get("parse_status") == "failed":
            candidates = _collect_verified_candidates(state)
            if candidates:
                return _candidate_confirmation_final(state, candidates)
            return _url_fetch_failed_final(state, result)

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

    def filter_allowed_tools(self, state: Any, allowed_tools: list[str]) -> list[str] | None:
        overrides = _context_overrides(state)
        action = _travel_action(overrides)
        trip_meta = _trip_meta(state, {}, overrides)
        phase = _phase(
            action=action,
            candidates=_collect_verified_candidates(state),
            itinerary=_payload_itinerary(overrides),
            map_payload=_latest_map_payload(state),
            trip_meta=trip_meta,
        )
        if _is_map_action(action):
            return None
        if phase != "map_generated":
            return [tool_name for tool_name in allowed_tools if tool_name != "travel_create_personal_map"]
        return None


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
    trip_meta: dict[str, Any] | None = None,
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
    if trip_meta and trip_meta.get("destination"):
        return "places_extracted"
    if trip_meta:
        return "created"
    return "ingesting_content"


def _system_directive(
    *,
    phase: str,
    action: str,
    candidate_count: int,
    has_attachments: bool,
    has_url_failures: bool,
    generic_food_additions: list[str],
) -> str:
    attachment_note = "当前有图片附件，必须先从图片中识别地点。" if has_attachments else "当前没有图片附件，优先使用用户文本中的攻略原文或 URL。"
    url_note = "已有 URL 获取失败记录；继续基于成功来源处理，并提示用户改用截图或粘贴原文。" if has_url_failures else ""
    food_note = ""
    if generic_food_additions:
        food_note = (
            "用户补充的美食地点不够具体："
            f"{'、'.join(generic_food_additions)}。必须先请用户补充可在高德检索的具体店名。"
        )
    if phase == "candidates_confirmed":
        return (
            "旅行规划候选已确认。现在只生成结构化 itinerary.days，并通过 submit_final_answer 返回 "
            "state=itinerary_generated、await_confirmation=true、trip_meta、candidates、itinerary.days、"
            "map={qr_code_url:null,schema_url:null}、raw_text。"
            "本阶段禁止调用 travel_create_personal_map；必须引导用户确认行程后再生成高德地图二维码。"
            f"{food_note}"
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
            f"{food_note}"
        )
    return (
        "旅行规划必须按 created -> ingesting_content -> places_extracted -> candidates_ready -> "
        "candidates_confirmed -> itinerary_generated -> map_generated 执行。"
        f"{attachment_note} 先提取地点，再用 travel_search_poi 验证 POI。"
        "如果用户上传了图片，你必须逐一识别图片中的景点、餐厅、酒店、商圈、车站等地点；"
        "图片清晰可读时禁止直接说无法识别图片；确实无法识别时先说明图片中可见内容并请求用户补充原文。"
        f"{url_note}{food_note}"
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
    observations = getattr(state, "observations", None)
    if isinstance(observations, list):
        for observation in observations:
            if not isinstance(observation, dict) or observation.get("tool") != "travel_fetch_url_content":
                continue
            result = observation.get("result")
            if not isinstance(result, dict):
                continue
            sources.append(
                {
                    "type": "url",
                    "url": result.get("url"),
                    "title": result.get("title"),
                    "parse_status": result.get("parse_status") or "failed",
                    "error": result.get("error"),
                }
            )
    return sources


def _url_failures(state: Any) -> list[dict[str, Any]]:
    observations = getattr(state, "observations", None)
    failures: list[dict[str, Any]] = []
    if not isinstance(observations, list):
        return failures
    for observation in observations:
        if not isinstance(observation, dict) or observation.get("tool") != "travel_fetch_url_content":
            continue
        result = observation.get("result")
        if isinstance(result, dict) and result.get("parse_status") == "failed":
            failures.append(result)
    return failures


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
        if not isinstance(observation, dict) or observation.get("tool") not in {"travel_search_poi", "travel_search_nearby_poi"}:
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
    excluded = _payload_excluded_places(_context_overrides(state))
    if excluded:
        candidates = [item for item in candidates if not _matches_excluded(item, excluded)]
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
    seen = {_candidate_key(item) for item in candidates}
    for added in _payload_user_added_places(overrides):
        key = _candidate_key(added)
        if key in seen:
            for existing in candidates:
                if _candidate_key(existing) == key:
                    existing["source"] = "user_added"
                    existing["score"] = 10
                    existing["reason"] = "用户补充地点，最高优先级"
            continue
        seen.add(key)
        candidates.append(
            {
                "candidate_id": added.get("candidate_id") or f"candidate_{len(candidates) + 1:03d}",
                "name": added.get("name"),
                "category": added.get("category") or "attraction",
                "source": "user_added",
                "score": 10,
                "reason": added.get("reason") or "用户补充地点，最高优先级",
                "poi": {
                    "poi_id": added.get("poi_id"),
                    "name": added.get("name"),
                    "address": added.get("address"),
                    "longitude": added.get("longitude"),
                    "latitude": added.get("latitude"),
                },
            }
        )
    excluded = _payload_excluded_places(overrides)
    if excluded:
        candidates = [item for item in candidates if not _matches_excluded(item, excluded)]
    return candidates


def _payload_user_added_places(overrides: dict[str, Any]) -> list[dict[str, Any]]:
    payload = overrides.get("travel_payload")
    payload = payload if isinstance(payload, dict) else {}
    raw_items = payload.get("user_added_places") or payload.get("added_places") or payload.get("additions") or []
    if not isinstance(raw_items, list):
        raw_items = [raw_items]
    places: list[dict[str, Any]] = []
    for item in raw_items:
        normalized = _normalize_user_added_place(item)
        if normalized:
            places.append(normalized)
    return places


def _normalize_user_added_place(item: Any) -> dict[str, Any] | None:
    if isinstance(item, str):
        name = item.strip()
        return {"name": name, "category": _category_from_text(name)} if name else None
    if not isinstance(item, dict):
        return None
    poi = item.get("poi") if isinstance(item.get("poi"), dict) else {}
    name = str(item.get("name") or poi.get("name") or "").strip()
    if not name:
        return None
    longitude = item.get("longitude") if item.get("longitude") is not None else poi.get("longitude")
    latitude = item.get("latitude") if item.get("latitude") is not None else poi.get("latitude")
    return {
        "candidate_id": item.get("candidate_id"),
        "name": name,
        "category": item.get("category") or _category_from_text(name),
        "reason": item.get("reason"),
        "poi_id": item.get("poi_id") or poi.get("poi_id"),
        "address": item.get("address") or poi.get("address"),
        "longitude": longitude,
        "latitude": latitude,
    }


def _payload_excluded_places(overrides: dict[str, Any]) -> list[str]:
    payload = overrides.get("travel_payload")
    payload = payload if isinstance(payload, dict) else {}
    raw_items = (
        payload.get("excluded_places")
        or payload.get("removed_places")
        or payload.get("deleted_places")
        or payload.get("delete_places")
        or []
    )
    if not isinstance(raw_items, list):
        raw_items = [raw_items]
    names: list[str] = []
    for item in raw_items:
        if isinstance(item, dict):
            value = item.get("candidate_id") or item.get("name")
        else:
            value = item
        name = str(value or "").strip()
        if name:
            names.append(name)
    return names


def _candidate_key(item: dict[str, Any]) -> str:
    poi = item.get("poi") if isinstance(item.get("poi"), dict) else {}
    return str(item.get("candidate_id") or poi.get("poi_id") or item.get("poi_id") or item.get("name") or "").strip()


def _matches_excluded(item: dict[str, Any], excluded: list[str]) -> bool:
    key = _candidate_key(item)
    name = str(item.get("name") or "").strip()
    return any(value and (value == key or value == name) for value in excluded)


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
        if not isinstance(observation, dict) or observation.get("tool") not in {"travel_search_poi", "travel_search_nearby_poi"}:
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


def _category_from_text(text: str) -> str:
    value = str(text or "").lower()
    if any(token in value for token in ("餐", "饭", "美食", "咖啡", "拉面", "火锅", "restaurant", "food", "cafe")):
        return "restaurant"
    if any(token in value for token in ("酒店", "住宿", "民宿", "hotel")):
        return "hotel"
    if any(token in value for token in ("机场", "车站", "火车站", "地铁", "transport")):
        return "transport_hub"
    return "attraction"


def _generic_food_additions(user_added_places: list[dict[str, Any]]) -> list[str]:
    generic: list[str] = []
    for item in user_added_places:
        category = str(item.get("category") or "")
        name = str(item.get("name") or "").strip()
        if category in {"restaurant", "cafe", "nightlife", "food"} and _is_generic_food_name(name):
            generic.append(name)
    return generic


def _is_generic_food_name(name: str) -> bool:
    value = str(name or "").strip()
    if not value:
        return False
    generic_exact = {
        "美食",
        "当地美食",
        "特色美食",
        "午餐",
        "晚餐",
        "早餐",
        "餐厅",
        "饭店",
        "小吃",
        "咖啡",
        "火锅",
        "烧烤",
        "面馆",
        "夜市",
        "甜品",
        "奶茶",
        "烤肉",
        "串串",
        "自助餐",
        "农家菜",
        "当地菜",
    }
    if value in generic_exact:
        return True
    return any(token in value for token in ("附近", "随便", "吃饭", "吃点", "好吃的"))


def _first_verified_poi(state: Any) -> dict[str, Any] | None:
    for candidate in _collect_verified_candidates(state):
        poi = candidate.get("poi") if isinstance(candidate.get("poi"), dict) else {}
        if poi.get("longitude") is not None and poi.get("latitude") is not None:
            return poi
    return None


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


def _url_fetch_failed_final(state: Any, result: dict[str, Any]) -> dict[str, Any]:
    error = str(result.get("error") or "URL 内容获取失败")
    url = str(result.get("url") or "")
    return {
        "state": "ingesting_content",
        "await_confirmation": False,
        "trip_meta": _trip_meta(state, {}, _context_overrides(state)),
        "sources": _sources(state),
        "places": [],
        "candidates": [],
        "failed_places": [],
        "itinerary": {"days": []},
        "map": {"qr_code_url": None, "schema_url": None},
        "raw_text": str(getattr(state, "message", "") or ""),
        "recommendations": [
            {
                "title": "攻略链接暂时无法读取",
                "reason": "我会跳过失败链接；为了提高成功率，建议上传攻略截图或直接粘贴攻略原文。",
            }
        ],
        "followups": ["请补充截图或攻略原文后，我会继续提取候选地点。"],
        "warnings": [f"{url}：{error}" if url else error],
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


def _submit_final_for_confirmed(state: Any, action: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """用户已确认候选地点，直接生成行程."""
    overrides = _context_overrides(state)
    trip_meta = _trip_meta(state, {}, overrides)
    llm_days = _payload_itinerary(overrides).get("days")
    if not isinstance(llm_days, list) or not llm_days:
        llm_days = _build_rough_itinerary(candidates, trip_meta).get("days", [])
    return {
        "state": "itinerary_generated",
        "await_confirmation": True,
        "trip_meta": trip_meta,
        "sources": _sources(state),
        "places": [{"name": item.get("name"), "category": item.get("category")} for item in candidates],
        "candidates": candidates,
        "itinerary": {"days": llm_days},
        "map": {"qr_code_url": None, "schema_url": None},
        "raw_text": str(getattr(state, "message", "") or ""),
        "recommendations": [
            {"title": "行程已生成", "reason": "请确认最终行程，确认后可生成高德地图二维码。"}
        ],
        "followups": ["确认行程后即可生成高德地图二维码。"],
        "warnings": [],
    }


def _handle_submit_final_answer(state: Any, action: str, result: Any) -> dict[str, Any] | None:
    """当 LLM 调用 submit_final_answer 时，根据当前阶段返回结构化数据."""
    final_answer = result.get("_final_answer") if isinstance(result, dict) else {}
    if not isinstance(final_answer, dict):
        final_answer = {}

    overrides = _context_overrides(state)
    candidates = _collect_verified_candidates(state)
    trip_meta = _trip_meta(state, {}, overrides)
    sources = _sources(state)

    # 从 LLM 的输出中提取 itinerary，如果 LLM 没生成就用候选数据构造
    itinerary = final_answer.get("itinerary") if isinstance(final_answer.get("itinerary"), dict) else _payload_itinerary(overrides)
    llm_days = itinerary.get("days") if isinstance(itinerary, dict) and isinstance(itinerary.get("days"), list) else []

    if action == "confirm_itinerary" or action == "generate_map":
        return {
            "state": "itinerary_generated",
            "await_confirmation": True,
            "trip_meta": trip_meta,
            "sources": sources,
            "places": [{"name": item.get("name"), "category": item.get("category")} for item in candidates],
            "candidates": candidates,
            "itinerary": {"days": llm_days} if llm_days else _build_rough_itinerary(candidates, trip_meta),
            "map": {"qr_code_url": None, "schema_url": None},
            "raw_text": str(getattr(state, "message", "") or ""),
            "recommendations": [
                {"title": "行程已生成", "reason": "请确认最终行程，确认后可生成高德地图二维码。"}
            ],
            "followups": ["确认行程后即可生成高德地图二维码。", "你可以继续调整行程内容。"],
            "warnings": [],
        }

    if action == "confirm_candidates":
        return {
            "state": "itinerary_generated",
            "await_confirmation": True,
            "trip_meta": trip_meta,
            "sources": sources,
            "places": [{"name": item.get("name"), "category": item.get("category")} for item in candidates],
            "candidates": candidates,
            "itinerary": {"days": llm_days} if llm_days else _build_rough_itinerary(candidates, trip_meta),
            "map": {"qr_code_url": None, "schema_url": None},
            "raw_text": str(getattr(state, "message", "") or ""),
            "recommendations": [
                {"title": "行程已生成", "reason": "请确认最终行程，确认后可生成高德地图二维码。"}
            ],
            "followups": ["确认行程后即可生成高德地图二维码。"],
            "warnings": [],
        }

    # 默认：返回现有的候选确认状态
    if candidates:
        return _candidate_confirmation_final(state, candidates)
    return _no_places_final(state, {})


def _build_rough_itinerary(candidates: list[dict[str, Any]], trip_meta: dict[str, Any]) -> dict[str, Any]:
    """从候选列表构造粗略行程."""
    try:
        day_count = int(trip_meta.get("days") or 1)
    except (TypeError, ValueError):
        day_count = 1
    day_count = max(1, min(day_count, 15))
    if not candidates:
        return {"days": []}
    per_day = max(1, len(candidates) // day_count)
    days = []
    for day_index in range(day_count):
        start = day_index * per_day
        end = start + per_day if day_index < day_count - 1 else len(candidates)
        day_candidates = candidates[start:end]
        items = []
        for item in day_candidates:
            poi = item.get("poi") if isinstance(item.get("poi"), dict) else {}
            items.append({
                "place_name": item.get("name") or poi.get("name", ""),
                "name": item.get("name") or poi.get("name", ""),
                "poi_id": poi.get("poi_id"),
                "longitude": poi.get("longitude"),
                "latitude": poi.get("latitude"),
                "address": poi.get("address"),
            })
        days.append({"day_number": day_index + 1, "theme": f"Day {day_index + 1}", "items": items})
    return {"days": days}


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
        seen_poi_ids: set[str] = set()
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
            poi_id = poi.get("poi_id") or poi.get("poiId")
            if not poi_id or longitude is None or latitude is None:
                continue
            poi_id = str(poi_id)
            if poi_id in seen_poi_ids:
                continue
            seen_poi_ids.add(poi_id)
            points.append(
                {
                    "name": name,
                    "poiId": poi_id,
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
    return _connect_line_list_days(line_list)


def _connect_line_list_days(line_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for index in range(len(line_list) - 1):
        current_points = line_list[index].get("pointInfoList")
        next_points = line_list[index + 1].get("pointInfoList")
        if not isinstance(current_points, list) or not current_points:
            continue
        if not isinstance(next_points, list) or not next_points:
            continue
        current_end = current_points[-1]
        next_start = next_points[0]
        if not isinstance(current_end, dict) or not isinstance(next_start, dict):
            continue
        if current_end.get("poiId") == next_start.get("poiId"):
            continue
        shared = dict(current_end)
        deduped = [shared]
        seen = {str(shared.get("poiId"))}
        for point in next_points:
            if not isinstance(point, dict):
                continue
            poi_id = str(point.get("poiId") or "")
            if poi_id and poi_id in seen:
                continue
            if poi_id:
                seen.add(poi_id)
            deduped.append(point)
        line_list[index + 1]["pointInfoList"] = deduped[:16]
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
